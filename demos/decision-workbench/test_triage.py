"""Issue-triage contracts and orchestration, with fake inference and no network."""
from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock, patch

import triage


def request_data(**overrides):
    fixture = triage.preset()
    data = {'text': fixture['text'], 'questions': deepcopy(fixture['questions']),
            'questionCount': 4, 'models': ['jev'], 'mode': 'batch'}
    data.update(overrides)
    return data


def sdk_response(questions):
    answers = {}
    for question in questions:
        option_ids = list(question['criteria'])
        selected = question.get('expectedOptionId') or option_ids[0]
        answers[question['id']] = {
            'type': 'choice', 'choice': selected,
            'probabilities': {option_id: float(option_id == selected) for option_id in option_ids},
            'confidence': 0.75,
        }
    return {'answers': answers, 'model': 'fixture-provider-model', 'usage': {'input_tokens_total': 100}}


class TriageValidationTests(unittest.TestCase):
    def test_preset_has_sixteen_distinct_questions_and_defensible_references(self):
        fixture = triage.preset()
        self.assertTrue(fixture['id'])
        self.assertTrue(fixture['title'])
        self.assertTrue(fixture['text'].strip())
        self.assertEqual(len(fixture['questions']), 16)
        self.assertEqual(len({question['id'] for question in fixture['questions']}), 16)
        clean = triage.validate_request(request_data())
        self.assertEqual(sum(question['expectedOptionId'] is not None for question in clean['questions']), 12)
        for question in clean['questions']:
            if question['basis'] == 'text':
                self.assertIn(question['expectedOptionId'], question['criteria'])
            else:
                self.assertIsNone(question['expectedOptionId'])
            self.assertTrue(question['rationale'].strip())
            self.assertIn(question['basis'], ('text', 'judgment'))

    def test_counts_modes_and_provider_allowlist_accept_only_supported_combinations(self):
        for count in (1, 4, 8, 16):
            for mode in ('batch', 'sequential', 'compare', 'sweep'):
                clean = triage.validate_request(request_data(questionCount=count, mode=mode, models=['jev', 'llm-adapter']))
                self.assertEqual(clean['questionCount'], count)
                self.assertEqual(clean['mode'], mode)
                self.assertEqual(clean['models'], ['jev', 'llm-adapter'])
        for field, values in {
            'questionCount': (True, False, 0, 2, 17, '4', None),
            'mode': ('unknown', '', None),
            'models': ([], ['modernbert'], ['unknown'], ['jev', 'jev'], ['jev', 'llm-adapter', 'jev'], 'jev', [None]),
            'text': ('', '   ', 'x' * 12001, None, 42),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        triage.validate_request(request_data(**{field: value}))

    def test_invalid_question_sets_ids_and_criteria_are_rejected(self):
        baseline = request_data()
        malformed = [None, [], baseline['questions'][:15], baseline['questions'] + [baseline['questions'][0]]]
        duplicate = deepcopy(baseline['questions'])
        duplicate[1]['id'] = duplicate[0]['id']
        malformed.append(duplicate)
        for field, value in (
            ('id', '../escape'), ('id', 'x' * 41), ('title', ''), ('title', 'x' * 161),
            ('instructions', ''), ('instructions', 'x' * 1501),
            ('criteria', {}), ('criteria', {'one': 'only choice'}),
            ('criteria', {str(index): 'choice' for index in range(5)}),
            ('criteria', {'valid': 'yes', '../bad': 'no'}),
            ('criteria', {'valid': 'yes', 'x' * 41: 'no'}),
            ('criteria', {'valid': 'yes', 'other': 'x' * 1001}),
            ('criteria', {'valid': 'yes', 'other': None}),
        ):
            questions = deepcopy(baseline['questions'])
            questions[0][field] = value
            malformed.append(questions)
        for questions in malformed:
            with self.subTest(questions=questions):
                with self.assertRaises(ValueError):
                    triage.validate_request(request_data(questions=questions))

    def test_reference_answers_are_server_owned_and_all_clear_on_any_semantic_edit(self):
        original = request_data()
        tampered = deepcopy(original)
        for question in tampered['questions']:
            question['expectedOptionId'] = 'INVENTED_REFERENCE'
            question['rationale'] = 'INVENTED_RATIONALE'
        trusted = triage.validate_request(tampered)
        for actual, expected in zip(trusted['questions'], triage.preset()['questions']):
            self.assertEqual(actual['expectedOptionId'], expected['expectedOptionId'])
            self.assertEqual(actual['rationale'], expected['rationale'])
        edited_requests = []
        edited_text = deepcopy(original)
        edited_text['text'] += ' Additional evidence.'
        edited_requests.append(edited_text)
        for field in ('id', 'title', 'instructions'):
            edited = deepcopy(original)
            edited['questions'][-1][field] += 'edited'
            edited_requests.append(edited)
        edited = deepcopy(original)
        option_id = next(iter(edited['questions'][-1]['criteria']))
        edited['questions'][-1]['criteria'][option_id] += ' Changed definition.'
        edited_requests.append(edited)
        for edited in edited_requests:
            clean = triage.validate_request(edited)
            self.assertTrue(all(question['expectedOptionId'] is None for question in clean['questions']))
            self.assertTrue(all(question['rationale'] == '' for question in clean['questions']))
        trusted['questions'][0]['criteria'].clear()
        self.assertTrue(triage.preset()['questions'][0]['criteria'], 'Validation must not mutate shared preset data')

    def test_payload_contains_only_task_evidence_and_requested_choice_definitions(self):
        data = request_data()
        questions = data['questions'][:4]
        payload = triage.make_payload(data['text'], questions)
        self.assertEqual(payload, {'state': data['text'], 'questions': {
            question['id']: {'instructions': question['instructions'], 'criteria': question['criteria']}
            for question in questions
        }})
        for question in questions:
            question.update(expectedOptionId='PRIVATE_EXPECTED', rationale='PRIVATE_RATIONALE', basis='PRIVATE_BASIS')
        self.assertEqual(payload, triage.make_payload(data['text'], questions))
        for marker in ('expectedOptionId', 'rationale', 'PRIVATE_EXPECTED', 'PRIVATE_RATIONALE', 'PRIVATE_BASIS'):
            self.assertNotIn(marker, json.dumps(payload))


class TriagePlanTests(unittest.TestCase):
    def plan(self, **overrides):
        request = triage.validate_request(request_data(**overrides))
        return [(item['model'], item['strategy'], item['questionCount']) for item in triage.make_plan(request)]

    def test_compare_uses_same_count_for_batch_and_sequential_per_model(self):
        self.assertEqual(self.plan(models=['jev', 'llm-adapter'], mode='compare', questionCount=8), [
            ('jev', 'batch', 8), ('jev', 'sequential', 8),
            ('llm-adapter', 'batch', 8), ('llm-adapter', 'sequential', 8),
        ])

    def test_sweep_is_four_batch_sizes_without_unrequested_sequential_runs(self):
        self.assertEqual(self.plan(models=['jev', 'llm-adapter'], mode='sweep', questionCount=4), [
            (model, 'batch', count) for model in ('jev', 'llm-adapter') for count in (1, 4, 8, 16)
        ])
        for strategy in ('batch', 'sequential'):
            self.assertEqual(self.plan(mode=strategy, questionCount=16), [('jev', strategy, 16)])


class TriageAnswerTests(unittest.TestCase):
    def setUp(self):
        self.questions = triage.validate_request(request_data())['questions'][:4]

    def test_all_question_answers_preserve_ids_scores_and_reference_matches(self):
        for model in ('jev', 'llm-adapter'):
            parsed = triage.parse_answers(sdk_response(self.questions), self.questions, model)
            self.assertEqual([row['questionId'] for row in parsed], [question['id'] for question in self.questions])
            for row, question in zip(parsed, self.questions):
                self.assertEqual(row['status'], 'ok')
                self.assertEqual(row['selectedOptionId'], question['expectedOptionId'] or next(iter(question['criteria'])))
                self.assertIs(row['matchesExpected'], True if question['expectedOptionId'] is not None else None)
                self.assertEqual([score['optionId'] for score in row['scores']], list(question['criteria']))
                if model == 'jev':
                    self.assertEqual(row['confidence'], 0.75)
        edited = deepcopy(self.questions)
        edited[0]['expectedOptionId'] = None
        parsed = triage.parse_answers(sdk_response(edited), edited, 'jev')
        self.assertIsNone(parsed[0]['matchesExpected'])

    def test_missing_extra_answers_and_invalid_choices_are_rejected(self):
        invalid = [None, {}, {'answers': {}}]
        first_id = self.questions[0]['id']
        missing = sdk_response(self.questions)
        del missing['answers'][first_id]
        invalid.append(missing)
        extra = sdk_response(self.questions)
        extra['answers']['unexpected'] = deepcopy(extra['answers'][first_id])
        invalid.append(extra)
        for field, value in (('choice', 'unknown'), ('probabilities', {}), ('type', 'score')):
            answer = sdk_response(self.questions)
            answer['answers'][first_id][field] = value
            invalid.append(answer)
        for answer in invalid:
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                triage.parse_answers(answer, self.questions, 'jev')

    def test_invalid_probabilities_or_jev_confidence_are_never_repaired(self):
        first = self.questions[0]
        option_id = next(iter(first['criteria']))
        for value in (float('nan'), float('inf'), -0.1, 1.1, None, True, '0.7'):
            for field in ('probability', 'confidence'):
                response = sdk_response(self.questions)
                answer = response['answers'][first['id']]
                if field == 'probability':
                    answer['probabilities'][option_id] = value
                else:
                    answer['confidence'] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    triage.parse_answers(response, self.questions, 'jev')


class TriageExecutionTests(unittest.TestCase):
    def successful_call(self, model, payload):
        questions = [dict(id=question_id, **question) for question_id, question in payload['questions'].items()]
        return {'status': 'ok', 'attempted': True, 'elapsedMs': 0.25,
                'answers': sdk_response(questions)['answers'], 'providerModel': 'fixture-model',
                'requestedModel': 'requested-fixture-model', 'modelName': model + ' fixture',
                'usage': {'input_tokens_total': 20}}

    def execute(self, request, inference):
        phases, progress = [], []
        with patch.object(triage, 'infer_call', side_effect=inference) as call:
            triage.execute(triage.validate_request(request), phases.append, progress.append)
        return phases, progress, call

    def test_compare_batches_once_and_sequentially_calls_each_same_question(self):
        request = request_data(questionCount=4, mode='compare', models=['jev', 'llm-adapter'])
        phases, progress, call = self.execute(request, self.successful_call)
        self.assertEqual(call.call_count, 10)
        self.assertEqual([phase['requestCount'] for phase in phases], [1, 4, 1, 4])
        self.assertEqual([phase['completedCalls'] for phase in phases], [1, 4, 1, 4])
        expected_ids = [question['id'] for question in request['questions'][:4]]
        for phase in phases:
            self.assertEqual(phase['status'], 'ok')
            self.assertEqual([answer['questionId'] for answer in phase['answers']], expected_ids)
            self.assertGreaterEqual(phase['elapsedMs'], 0)
            self.assertEqual(phase['providerModel'], 'fixture-model')
        for offset in (0, 5):
            batch = call.call_args_list[offset].args[1]
            sequential = call.call_args_list[offset + 1:offset + 5]
            self.assertEqual(list(batch['questions']), expected_ids)
            for question_id, item in zip(expected_ids, sequential):
                self.assertEqual(item.args[1]['state'], batch['state'])
                self.assertEqual(item.args[1]['questions'], {question_id: batch['questions'][question_id]})
        self.assertTrue(all(item['totalCalls'] == 10 for item in progress))
        self.assertEqual(progress[-1]['completedCalls'], 10)
        self.assertEqual([item['completedCalls'] for item in progress], sorted(item['completedCalls'] for item in progress))

    def test_sweep_sends_four_prefix_batches_and_does_not_amplify_into_29_requests(self):
        phases, progress, call = self.execute(request_data(mode='sweep'), self.successful_call)
        self.assertEqual(call.call_count, 4)
        self.assertEqual([len(item.args[1]['questions']) for item in call.call_args_list], [1, 4, 8, 16])
        self.assertEqual([phase['requestCount'] for phase in phases], [1, 1, 1, 1])
        self.assertEqual(progress[-1]['completedCalls'], 4)

    def test_failed_and_malformed_calls_continue_with_partial_results_without_fake_answers(self):
        count = 0
        def inference(model, payload):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError('PRIVATE_PROVIDER_EXCEPTION')
            if count == 3:
                return {'status': 'ok', 'elapsedMs': 0.5, 'answers': {}}
            return self.successful_call(model, payload)
        phases, progress, call = self.execute(request_data(mode='sequential'), inference)
        self.assertEqual(call.call_count, 4)
        phase = phases[0]
        self.assertEqual(phase['status'], 'partial')
        self.assertEqual(phase['requestCount'], 4)
        self.assertEqual(phase['completedCalls'], 2)
        self.assertEqual([answer['status'] for answer in phase['answers']], ['ok', 'error', 'error', 'ok'])
        for answer in phase['answers'][1:3]:
            self.assertIsNone(answer['selectedOptionId'])
            self.assertIsNone(answer['matchesExpected'])
            self.assertEqual(answer['scores'], [])
        self.assertNotIn('PRIVATE_PROVIDER_EXCEPTION', json.dumps(phases))
        self.assertEqual(progress[-1]['completedCalls'], 4)

    def test_unconfigured_provider_does_not_count_unsent_api_requests(self):
        failure = {'status': 'error', 'attempted': False, 'elapsedMs': 0,
                   'answers': {}, 'error': 'Provider setup is missing.'}
        phases, progress, call = self.execute(request_data(mode='sequential'), lambda *_args: failure)
        self.assertEqual(call.call_count, 4)
        self.assertEqual(phases[0]['status'], 'error')
        self.assertEqual(phases[0]['requestCount'], 0)
        self.assertEqual(phases[0]['completedCalls'], 0)
        self.assertEqual(progress[-1]['completedCalls'], 4)


class TriageProviderBoundaryTests(unittest.TestCase):
    def setUp(self):
        data = triage.validate_request(request_data())
        self.questions = data['questions'][:4]
        self.payload = triage.make_payload(data['text'], self.questions)
        self.config = {'api_key': 'SYNTHETIC_TRIAGE_SECRET', 'model': 'jev-fixture'}

    def jev_call(self, request, problem=None):
        with patch.object(triage.jev, '_configuration', return_value=self.config), \
                patch.object(triage.jev, '_problem', return_value=problem), \
                patch.object(triage.jev, '_request', request):
            return triage.infer_call('jev', self.payload)

    def test_jev_dispatch_validates_all_answers_and_drops_private_response_metadata(self):
        response = sdk_response(self.questions)
        response['debug'] = self.config['api_key']
        response['usage']['authorization'] = self.config['api_key']
        response['answers'][self.questions[0]['id']]['private'] = self.config['api_key']
        request = AsyncMock(return_value=response)
        result = self.jev_call(request)
        request.assert_awaited_once_with(self.config, self.payload)
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['attempted'])
        self.assertEqual(set(result['answers']), set(self.payload['questions']))
        self.assertEqual(result['usage'], {'input_tokens_total': 100})
        self.assertNotIn(self.config['api_key'], json.dumps(result))
        self.assertNotIn('expectedOptionId', json.dumps(request.await_args.args[1]))

    def test_setup_failure_errors_timeout_and_bad_answers_never_fabricate_result(self):
        request = AsyncMock()
        result = self.jev_call(request, problem='Missing setup.')
        request.assert_not_awaited()
        self.assertFalse(result['attempted'])
        for mocked in (AsyncMock(side_effect=RuntimeError(self.config['api_key'])),
                       AsyncMock(side_effect=TimeoutError(self.config['api_key'])),
                       AsyncMock(return_value={'model': 'jev-fixture', 'answers': {}})):
            result = self.jev_call(mocked)
            self.assertEqual(result['status'], 'error')
            self.assertTrue(result['attempted'])
            self.assertEqual(result['answers'], {})
            self.assertNotIn(self.config['api_key'], json.dumps(result))
            mocked.assert_awaited_once()

    def test_adapter_dispatch_preserves_multiple_question_ids_and_hides_private_fields(self):
        config = {'DEMO_LLM_PROVIDER': 'openai', 'DEMO_LLM_MODEL': 'fixture-model',
                  'DEMO_LLM_API_KEY': 'SYNTHETIC_ADAPTER_SECRET', 'DEMO_LLM_BASE_URL': ''}
        response = sdk_response(self.questions)
        response['debug'] = {'private': config['DEMO_LLM_API_KEY']}
        request = AsyncMock(return_value=response)
        with patch.object(triage.adapter, '_configuration', return_value=config), \
                patch.object(triage.adapter, '_missing_dependencies', return_value=[]), \
                patch.object(triage.adapter, '_request', request):
            result = triage.infer_call('llm-adapter', self.payload)
        request.assert_awaited_once_with(config, self.payload)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(set(result['answers']), set(self.payload['questions']))
        self.assertTrue(all('confidence' not in answer for answer in result['answers'].values()))
        self.assertNotIn(config['DEMO_LLM_API_KEY'], json.dumps(result))


if __name__ == '__main__':
    unittest.main()
