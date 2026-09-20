"""Official-adapter boundary tests: standard library only, no credentials/network."""
from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock, patch

import adapter
from adapter import make_choice_input, parse_choice_response
from server import fixtures


class ChoiceInputTests(unittest.TestCase):
    def test_choice_input_preserves_text_and_maps_every_option_id_to_its_meaning(self):
        task = fixtures()[0]
        original = deepcopy(task)
        payload = make_choice_input(task)
        self.assertEqual(payload['state'], task['text'])
        self.assertIn(task['question'], payload['instructions'])
        self.assertIn(task['criteria'], payload['instructions'])
        self.assertEqual(list(payload['criteria']), [option['id'] for option in task['options']])
        for option in task['options']:
            self.assertIn(option['label'], payload['criteria'][option['id']])
            self.assertIn(option['description'], payload['criteria'][option['id']])
        self.assertEqual(task, original, 'Prompt construction must not mutate a fixture')

    def test_reference_answers_and_human_rationale_never_enter_provider_input(self):
        task = deepcopy(fixtures()[0])
        task.update(fixtureId='PRIVATE_FIXTURE_MARKER', rationale='PRIVATE_HUMAN_RATIONALE', title='PRIVATE_TITLE')
        first = make_choice_input(task)
        task['expectedOptionId'] = task['options'][0]['id']
        task['rationale'] = 'DIFFERENT_HUMAN_RATIONALE'
        second = make_choice_input(task)
        self.assertEqual(first, second, 'Changing the human reference must not influence model input')
        serialized = json.dumps(first, ensure_ascii=False)
        for forbidden in ('PRIVATE_FIXTURE_MARKER', 'PRIVATE_HUMAN_RATIONALE', 'DIFFERENT_HUMAN_RATIONALE', 'PRIVATE_TITLE', 'expectedOptionId', 'rationale'):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(set(first), {'state', 'instructions', 'criteria'})

    def test_question_criteria_and_choice_edits_change_the_actual_choice_input(self):
        task = deepcopy(fixtures()[0])
        baseline = make_choice_input(task)
        task['question'] = 'A different question?'
        task['criteria'] = 'Use a different criterion.'
        task['options'][0]['description'] = 'A different option definition.'
        edited = make_choice_input(task)
        self.assertNotEqual(edited['instructions'], baseline['instructions'])
        self.assertNotEqual(edited['criteria'], baseline['criteria'])
        self.assertEqual(edited['state'], baseline['state'])


class ChoiceResponseTests(unittest.TestCase):
    option_ids = ['meets', 'violates', 'insufficient']

    def response(self):
        return {'answers': {'decision': {
            'type': 'choice', 'choice': 'violates',
            'probabilities': {'meets': 0.1, 'violates': 0.8, 'insufficient': 0.1},
            'confidence': 0.8,
        }}}

    def test_choice_response_maps_probabilities_without_inventing_logits(self):
        response = self.response()
        original = deepcopy(response)
        result = parse_choice_response(response, self.option_ids)
        self.assertEqual(result['selectedOptionId'], 'violates')
        self.assertEqual(result['rawAnswer'], 'violates')
        self.assertEqual([row['optionId'] for row in result['scores']], self.option_ids)
        self.assertEqual([row['score'] for row in result['scores']], [0.1, 0.8, 0.1])
        self.assertTrue(all(row['rawScore'] is None for row in result['scores']))
        self.assertEqual(response, original)

    def test_unknown_selected_id_missing_or_extra_distribution_ids_are_rejected(self):
        invalid = []
        unknown_choice = self.response()
        unknown_choice['answers']['decision']['choice'] = 'invented-option'
        invalid.append(unknown_choice)
        missing = self.response()
        del missing['answers']['decision']['probabilities']['insufficient']
        invalid.append(missing)
        extra = self.response()
        extra['answers']['decision']['probabilities']['invented-option'] = 0.0
        invalid.append(extra)
        for response in invalid:
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    parse_choice_response(response, self.option_ids)

    def test_nonfinite_out_of_range_and_nonnumeric_probabilities_are_rejected(self):
        for invalid in (float('nan'), float('inf'), -float('inf'), -0.1, 1.1, '0.8', True, None):
            response = self.response()
            response['answers']['decision']['probabilities']['violates'] = invalid
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    parse_choice_response(response, self.option_ids)

    def test_nonunit_distributions_and_choice_not_at_maximum_are_not_repaired(self):
        nonunit = self.response()
        nonunit['answers']['decision']['probabilities']['violates'] = 0.6
        lower_choice = self.response()
        lower_choice['answers']['decision']['choice'] = 'meets'
        lower_choice['answers']['decision']['confidence'] = 0.1
        for response in (nonunit, lower_choice):
            with self.assertRaises(ValueError):
                parse_choice_response(response, self.option_ids)

    def test_malformed_or_error_responses_cannot_become_successful_choices(self):
        wrong_type = self.response()
        wrong_type['answers']['decision']['type'] = 'score'
        for response in (None, [], {}, {'error': 'provider failed'}, {'answers': {}}, wrong_type):
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    parse_choice_response(response, self.option_ids)


class AdapterExecutionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.task = fixtures()[0]
        self.config = {
            'DEMO_LLM_PROVIDER': 'openai', 'DEMO_LLM_MODEL': 'fixture-model',
            'DEMO_LLM_API_KEY': 'TEST_ONLY_SECRET_MARKER', 'DEMO_LLM_BASE_URL': '',
        }

    def execute(self, request):
        with patch.object(adapter, '_configuration', return_value=self.config), \
                patch.object(adapter, '_missing_dependencies', return_value=[]), \
                patch.object(adapter, '_request', request):
            return adapter.infer_adapter(self.task)

    def test_validated_result_exposes_only_safe_model_and_usage_metadata(self):
        response = ChoiceResponseTests().response()
        response['usage'] = {'input_tokens_total': 50, 'output_tokens_total': 10, 'latency': 0.2,
                             'authorization': self.config['DEMO_LLM_API_KEY'], 'n_retries': float('nan')}
        response['debug'] = {'llm_attempts': [{'llm_response': {'model': 'fixture-model-snapshot'},
                                              'headers': {'Authorization': self.config['DEMO_LLM_API_KEY']}}]}
        request = AsyncMock(return_value=response)
        result = self.execute(request)
        request.assert_awaited_once()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['selectedOptionId'], 'violates')
        self.assertEqual(result['providerModel'], 'fixture-model-snapshot')
        self.assertEqual(result['scoreKind'], 'llm-reported-probabilities')
        self.assertEqual(result['usage'], {'input_tokens_total': 50, 'output_tokens_total': 10, 'latency': 0.2})
        self.assertNotIn(self.config['DEMO_LLM_API_KEY'], json.dumps(result))
        self.assertNotIn('debug', result)
        payload = request.await_args.args[1]
        self.assertEqual(payload, make_choice_input(self.task))

    def test_provider_failures_timeouts_and_bad_answers_never_fabricate_success_or_leak_errors(self):
        bad_response = ChoiceResponseTests().response()
        bad_response['answers']['decision']['choice'] = 'invented'
        for request in (
            AsyncMock(side_effect=RuntimeError('Provider error containing TEST_ONLY_SECRET_MARKER')),
            AsyncMock(side_effect=TimeoutError('Endpoint error containing TEST_ONLY_SECRET_MARKER')),
            AsyncMock(return_value=bad_response),
        ):
            result = self.execute(request)
            request.assert_awaited_once()
            self.assertEqual(result['status'], 'error')
            self.assertIsNone(result['selectedOptionId'])
            self.assertEqual(result['scores'], [])
            self.assertTrue(result['error'])
            self.assertNotIn(self.config['DEMO_LLM_API_KEY'], json.dumps(result))

    def test_unconfigured_adapter_makes_no_request_and_does_not_claim_availability(self):
        self.config = {key: '' for key in self.config}
        request = AsyncMock()
        result = self.execute(request)
        request.assert_not_awaited()
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['selectedOptionId'])
        with patch.object(adapter, '_configuration', return_value=self.config), \
                patch.object(adapter, '_missing_dependencies', return_value=[]):
            self.assertFalse(adapter.adapter_status()['available'])

    def test_codex_uses_existing_cli_without_requiring_or_exposing_an_api_key(self):
        self.config.update(DEMO_LLM_PROVIDER='codex', DEMO_LLM_API_KEY='')
        response = ChoiceResponseTests().response()
        response['model'] = 'fixture-reported-model'
        request = AsyncMock(return_value=response)
        with patch.object(adapter.shutil, 'which', return_value='/fixture/codex'), \
                patch.object(adapter, '_configuration', return_value=self.config), \
                patch.object(adapter, '_missing_dependencies', return_value=[]):
            status = adapter.adapter_status()
            self.assertTrue(status['available'])
            self.assertIn('saved login', status['detail'])
            self.assertIn('does not', status['detail'])
            result = self.execute(request)
        request.assert_awaited_once()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['provider'], 'codex')
        self.assertEqual(result['providerModel'], 'fixture-reported-model')
        self.assertEqual(request.await_args.args[0]['DEMO_LLM_API_KEY'], '')

    def test_missing_codex_cli_is_setup_failure_without_request(self):
        self.config.update(DEMO_LLM_PROVIDER='codex', DEMO_LLM_API_KEY='')
        request = AsyncMock()
        with patch.object(adapter.shutil, 'which', return_value=None):
            result = self.execute(request)
        request.assert_not_awaited()
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['selectedOptionId'])
        self.assertIn('Codex CLI', result['error'])


if __name__ == '__main__':
    unittest.main()
