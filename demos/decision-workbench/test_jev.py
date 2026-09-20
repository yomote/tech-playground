"""Direct Jev boundary tests with synthetic config/SDK; no credentials or network."""
import asyncio
from copy import deepcopy
import json
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import jev
from server import fixtures


def response():
    return {
        'model': 'jev-fixture-version',
        'answers': {'decision': {
            'type': 'choice', 'choice': 'violates',
            'probabilities': {'meets': 0.1, 'violates': 0.8, 'insufficient': 0.1},
            'confidence': 0.65,
        }},
        'usage': {'input_tokens_total': 42, 'output_tokens_total': 7, 'latency': 0.123, 'n_retries': 0},
    }


class JevConfigurationTests(unittest.TestCase):
    def configuration(self, environment):
        with patch.dict(jev.os.environ, environment, clear=True), \
                patch.object(jev, '_ENV_PATH', Mock(is_file=Mock(return_value=False))):
            return jev._configuration()

    def test_only_dedicated_typesafe_key_is_used_without_codex_or_other_provider_fallback(self):
        unrelated = {
            'DEMO_LLM_PROVIDER': 'codex', 'DEMO_LLM_API_KEY': 'OTHER_PROVIDER_TEST_KEY',
            'OPENAI_API_KEY': 'OPENAI_TEST_KEY', 'ANTHROPIC_API_KEY': 'ANTHROPIC_TEST_KEY',
            'CODEX_API_KEY': 'CODEX_TEST_KEY', 'DEMO_LLM_MODEL': 'unrelated-model',
        }
        self.assertEqual(self.configuration(unrelated), {'api_key': '', 'model': 'jev-latest'})
        configured = self.configuration({**unrelated, 'TYPESAFE_API_KEY': ' TYPESAFE_TEST_KEY ', 'TYPESAFE_MODEL': ' jev-test '})
        self.assertEqual(configured, {'api_key': 'TYPESAFE_TEST_KEY', 'model': 'jev-test'})

    def test_missing_key_or_dependencies_disable_status_and_never_make_requests(self):
        for config, dependency in (({'api_key': '', 'model': 'jev-latest'}, object()),
                                   ({'api_key': 'TEST_KEY', 'model': 'jev-latest'}, None),
                                   ({'api_key': 'TEST_KEY', 'model': 'invalid model!'}, object())):
            with self.subTest(config=config, dependency=bool(dependency)), \
                    patch.object(jev, '_configuration', return_value=config), \
                    patch.object(jev.importlib.util, 'find_spec', return_value=dependency), \
                    patch.object(jev, '_request', new_callable=AsyncMock) as request:
                self.assertFalse(jev.jev_status()['available'])
                result = jev.infer_jev(fixtures()[0])
            request.assert_not_awaited()
            self.assertEqual(result['status'], 'error')
            self.assertIsNone(result['selectedOptionId'])
            self.assertEqual(result['scores'], [])


class JevInferenceTests(unittest.TestCase):
    def setUp(self):
        self.task = deepcopy(fixtures()[0])
        self.config = {'api_key': 'TEST_ONLY_TYPESAFE_SECRET', 'model': 'jev-requested-model'}

    def execute(self, request):
        with patch.object(jev, '_configuration', return_value=self.config), \
                patch.object(jev.importlib.util, 'find_spec', return_value=object()), \
                patch.object(jev, '_request', request):
            return jev.infer_jev(self.task)

    def assert_failed(self, result):
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['selectedOptionId'])
        self.assertEqual(result['scores'], [])
        self.assertNotIn('confidence', result)
        self.assertNotIn('usage', result)
        self.assertNotIn(self.config['api_key'], json.dumps(result))

    def test_valid_response_preserves_separate_confidence_probabilities_model_and_usage(self):
        answer = response()
        answer['usage'].update(authorization=self.config['api_key'], invalid=float('nan'))
        answer['debug'] = {'secret': self.config['api_key']}
        request = AsyncMock(return_value=answer)
        result = self.execute(request)
        request.assert_awaited_once()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['model'], 'jev')
        self.assertEqual(result['provider'], 'typesafe')
        self.assertEqual(result['requestedModel'], self.config['model'])
        self.assertEqual(result['providerModel'], 'jev-fixture-version')
        self.assertEqual(result['selectedOptionId'], 'violates')
        self.assertEqual(result['confidence'], 0.65)
        self.assertEqual([item['score'] for item in result['scores']], [0.1, 0.8, 0.1])
        self.assertTrue(all(item['rawScore'] is None for item in result['scores']))
        self.assertEqual(result['scoreKind'], 'jev-probabilities')
        self.assertEqual(result['usage'], response()['usage'])
        self.assertGreaterEqual(result['inferenceMs'], 0)
        self.assertNotIn(self.config['api_key'], json.dumps(result))
        self.assertNotIn('debug', result)

    def test_human_expected_answer_and_rationale_never_enter_direct_jev_payload(self):
        self.task.update(expectedOptionId='PRIVATE_EXPECTED', rationale='PRIVATE_RATIONALE', fixtureId='PRIVATE_FIXTURE')
        request = AsyncMock(return_value=response())
        self.assertEqual(self.execute(request)['status'], 'ok')
        config, payload = request.await_args.args
        self.assertEqual(config, self.config)
        self.assertEqual(payload['state'], self.task['text'])
        self.assertIn(self.task['question'], payload['instructions'])
        self.assertIn(self.task['criteria'], payload['instructions'])
        self.assertEqual(list(payload['criteria']), [item['id'] for item in self.task['options']])
        for item in self.task['options']:
            self.assertIn(item['label'], payload['criteria'][item['id']])
            self.assertIn(item['description'], payload['criteria'][item['id']])
        for marker in ('PRIVATE_EXPECTED', 'PRIVATE_RATIONALE', 'PRIVATE_FIXTURE', self.config['api_key']):
            self.assertNotIn(marker, json.dumps(payload))

    def test_errors_and_timeouts_are_sanitized_without_retry_or_fallback(self):
        for error in (RuntimeError(self.config['api_key']), TimeoutError(self.config['api_key'])):
            request = AsyncMock(side_effect=error)
            result = self.execute(request)
            request.assert_awaited_once()
            self.assert_failed(result)
            self.assertIn('No retry or fallback', result['error'])

    def test_actual_timeout_cancels_the_request(self):
        cancelled = []
        async def pending(*_args):
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)
        request = AsyncMock(side_effect=pending)
        with patch.object(jev, '_TIMEOUT_SECONDS', 0.01):
            result = self.execute(request)
        self.assert_failed(result)
        self.assertEqual(cancelled, [True])
        request.assert_awaited_once()

    def test_invalid_confidence_model_and_choice_data_never_become_success(self):
        invalid = [None, {}, {'error': self.config['api_key']}]
        for value in (None, True, '0.65', -0.1, 1.1, float('nan'), float('inf')):
            answer = response()
            answer['answers']['decision']['confidence'] = value
            invalid.append(answer)
        for model in (None, 7, '', 'unexpected model!', self.config['api_key']):
            answer = response()
            answer['model'] = model
            invalid.append(answer)
        for field, value in (('choice', 'unknown'), ('probabilities', {'violates': 1}),
                             ('probabilities', {'meets': 0.1, 'violates': float('nan'), 'insufficient': 0.1})):
            answer = response()
            answer['answers']['decision'][field] = value
            invalid.append(answer)
        for answer in invalid:
            with self.subTest(answer=answer):
                self.assert_failed(self.execute(AsyncMock(return_value=answer)))


class JevSdkBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_sdk_receives_dedicated_key_fixed_endpoint_choice_and_zero_retries(self):
        sdk = ModuleType('typesafe_sdk')
        answer = response()
        client = SimpleNamespace(system_one=AsyncMock(return_value=SimpleNamespace(model_dump=lambda: answer)))
        context = AsyncMock()
        context.__aenter__.return_value = client
        sdk.AsyncTypeSafeClient = Mock(return_value=context)
        sdk.Choice = Mock(side_effect=lambda **kwargs: SimpleNamespace(**kwargs))
        sdk.RetryPolicy = Mock(side_effect=lambda **kwargs: SimpleNamespace(**kwargs))
        config = {'api_key': 'SYNTHETIC_TYPESAFE_KEY', 'model': 'jev-test'}
        payload = jev.make_choice_input(fixtures()[0])
        with patch.dict(sys.modules, {'typesafe_sdk': sdk}):
            result = await jev._request(config, payload)
        self.assertEqual(result, answer)
        kwargs = sdk.AsyncTypeSafeClient.call_args.kwargs
        self.assertEqual(kwargs['api_key'], config['api_key'])
        self.assertEqual(kwargs['model'], config['model'])
        self.assertEqual(kwargs['base_url'], 'https://api.typesafe.ai')
        self.assertEqual(kwargs['timeout'], 30)
        self.assertEqual(kwargs['retry'].max_retries, 0)
        sdk.Choice.assert_called_once_with(instructions=payload['instructions'], criteria=payload['criteria'])
        client.system_one.assert_awaited_once()
        self.assertEqual(client.system_one.await_args.kwargs['state'], payload['state'])
        self.assertEqual(set(client.system_one.await_args.kwargs['questions']), {'decision'})
        context.__aexit__.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
