"""Codex bridge regressions with fake subprocesses; no CLI, auth, SDK, or network."""
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import codex_provider


@dataclass
class FakeProviderResult:
    text: str
    input_tokens: int
    output_tokens: int


def optional_module_fakes():
    package = ModuleType('system_one_adapter')
    package.__path__ = []
    providers = ModuleType('system_one_adapter.providers')
    providers.ProviderResult = FakeProviderResult
    package.providers = providers
    sdk = ModuleType('typesafe_sdk')
    sdk.TypeSafeError = type('FakeTypeSafeError', (Exception,), {})
    return {'system_one_adapter': package, 'system_one_adapter.providers': providers, 'typesafe_sdk': sdk}


def completed_events(input_tokens=123, output_tokens=17):
    return '\n'.join(json.dumps(event) for event in [
        {'type': 'thread.started', 'thread_id': 'fixture-thread'},
        {'type': 'item.completed', 'item': {'type': 'reasoning', 'text': 'internal fixture'}},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{}'}},
        {'type': 'turn.completed', 'usage': {'input_tokens': input_tokens, 'output_tokens': output_tokens}},
    ])


def module_proxy(module, **overrides):
    """Patch this module's binding without changing Python's global os/asyncio."""
    values = dict(vars(module))
    values.update(overrides)
    return SimpleNamespace(**values)


class CodexCommandAndUsageTests(unittest.TestCase):
    def test_command_uses_isolated_read_only_turn_and_keeps_arguments_separate(self):
        directory = Path('/temporary fixture/with spaces')
        model = 'literal-model;$(do-not-run)'
        args = codex_provider.command_arguments('/fake executable/codex', model, directory)
        self.assertEqual(args[0], '/fake executable/codex')
        self.assertEqual(args[1], 'exec')
        self.assertEqual(args[-1], '-')
        for flag in ('--ignore-user-config', '--ephemeral', '--skip-git-repo-check', '--json'):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index('--model') + 1], model)
        self.assertEqual(args[args.index('--sandbox') + 1], 'read-only')
        self.assertEqual(args[args.index('--cd') + 1], str(directory))
        self.assertEqual(args[args.index('--output-schema') + 1], str(directory / 'schema.json'))
        self.assertEqual(args[args.index('--output-last-message') + 1], str(directory / 'answer.json'))
        config = [args[index + 1] for index, value in enumerate(args) if value == '-c']
        self.assertIn('web_search="disabled"', config)
        self.assertIn('project_doc_max_bytes=0', config)
        self.assertIn('approval_policy="never"', config)
        disabled = {args[index + 1] for index, value in enumerate(args) if value == '--disable'}
        self.assertTrue({'shell_tool', 'unified_exec', 'apps', 'plugins', 'hooks', 'multi_agent',
                         'browser_use', 'computer_use', 'skill_search', 'memories'} <= disabled)

    def test_usage_requires_completed_turn_and_preserves_actual_counts_including_zero(self):
        self.assertEqual(codex_provider.parse_usage(completed_events()), (123, 17))
        self.assertEqual(codex_provider.parse_usage(completed_events(0, 0)), (0, 0))
        with self.assertRaises(ValueError):
            codex_provider.parse_usage(json.dumps({'type': 'thread.started'}))
        with self.assertRaises(ValueError):
            codex_provider.parse_usage('')

    def test_nonfatal_capability_warning_requires_a_later_successful_completed_turn(self):
        warning = json.dumps({'type': 'item.completed', 'item': {
            'type': 'error', 'message': 'Code-mode host disabled for this isolated classification turn.',
        }})
        self.assertEqual(codex_provider.parse_usage(warning + '\n' + completed_events(9462, 31)), (9462, 31))
        with self.assertRaises(ValueError):
            codex_provider.parse_usage(warning)
        with self.assertRaises(RuntimeError):
            codex_provider.parse_usage(warning + '\n' + json.dumps({'type': 'turn.failed'}))

    def test_failed_turn_or_tool_activity_cannot_be_accepted_as_completed_usage(self):
        for event in (
            {'type': 'error', 'message': 'fixture error'},
            {'type': 'turn.failed', 'error': {'message': 'fixture error'}},
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'unexpected'}},
            {'type': 'item.completed', 'item': {'type': 'mcp_tool_call'}},
        ):
            with self.subTest(event=event):
                with self.assertRaises(RuntimeError):
                    codex_provider.parse_usage(json.dumps(event) + '\n' + completed_events())

    def test_invalid_usage_is_not_coerced_into_fabricated_token_counts(self):
        for invalid in (-1, 1.5, '10', True, None, float('nan'), float('inf')):
            for field in ('input_tokens', 'output_tokens'):
                usage = {'input_tokens': 12, 'output_tokens': 3}
                usage[field] = invalid
                with self.subTest(field=field, value=invalid):
                    with self.assertRaises(ValueError):
                        codex_provider.parse_usage(json.dumps({'type': 'turn.completed', 'usage': usage}))
        for usage in (None, {}, {'input_tokens': 12}, {'output_tokens': 3}):
            with self.assertRaises(ValueError):
                codex_provider.parse_usage(json.dumps({'type': 'turn.completed', 'usage': usage}))
        with self.assertRaises(ValueError):
            codex_provider.parse_usage('not JSON')


class CodexRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.modules = patch.dict(sys.modules, optional_module_fakes())
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.provider = codex_provider.CodexProvider('fixture-model')
        self.messages = [
            SimpleNamespace(role='system', content='Return a typed decision.'),
            SimpleNamespace(role='user', content='日本語 document; $(not-a-command) "quoted"\nnew line'),
        ]
        self.schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer']}
        self.args = None
        self.kwargs = None
        self.stdin_payload = None
        self.answer = '{"answer":"fixture"}'
        self.events = completed_events()
        self.returncode = 0

    async def fake_spawn(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        directory = Path(kwargs['cwd'])
        self.assertEqual(json.loads((directory / 'schema.json').read_text(encoding='utf-8')), self.schema)

        async def communicate(data=None):
            self.stdin_payload = data
            if self.answer is not None:
                (directory / 'answer.json').write_text(self.answer, encoding='utf-8')
            return self.events.encode('utf-8'), b'PRIVATE_STDERR_ACCOUNT_OR_INPUT'

        return SimpleNamespace(returncode=self.returncode, communicate=AsyncMock(side_effect=communicate), kill=Mock())

    async def run_request(self):
        with patch.object(codex_provider.shutil, 'which', return_value='/fake/codex'), \
                patch.object(codex_provider.asyncio, 'create_subprocess_exec', side_effect=self.fake_spawn):
            return await self.provider.request(self.messages, schema=self.schema, structured=True)

    async def test_request_passes_ordered_message_json_via_stdin_not_shell_or_argv(self):
        result = await self.run_request()
        self.assertEqual(result.text, self.answer)
        self.assertEqual((result.input_tokens, result.output_tokens), (123, 17))
        self.assertNotIn('shell', self.kwargs)
        self.assertEqual(self.kwargs['stdin'], asyncio.subprocess.PIPE)
        self.assertEqual(self.kwargs['stdout'], asyncio.subprocess.PIPE)
        self.assertEqual(self.kwargs['stderr'], asyncio.subprocess.PIPE)
        for message in self.messages:
            self.assertFalse(any(message.content in value for value in self.args))
        prompt = self.stdin_payload.decode('utf-8')
        payload = json.loads(prompt.split('ordered provider messages:\n', 1)[1])
        self.assertEqual(payload, [{'role': message.role, 'content': message.content} for message in self.messages])
        self.assertFalse(Path(self.kwargs['cwd']).exists(), 'Ephemeral turn files must be cleaned up')

    async def test_platform_spawn_options_isolate_posix_group_and_hide_windows_process(self):
        for platform in ('nt', 'posix'):
            with self.subTest(platform=platform), \
                    patch.object(codex_provider, 'os', module_proxy(codex_provider.os, name=platform)), \
                    patch.object(codex_provider, 'subprocess', module_proxy(codex_provider.subprocess, CREATE_NO_WINDOW=0x08000000)):
                await self.run_request()
            self.assertEqual(self.kwargs['start_new_session'], platform == 'posix')
            self.assertEqual(self.kwargs['creationflags'], 0x08000000 if platform == 'nt' else 0)
            self.assertNotIn('shell', self.kwargs)

    async def test_unstructured_request_or_missing_cli_never_spawns_a_process(self):
        with patch.object(codex_provider.asyncio, 'create_subprocess_exec', new_callable=AsyncMock) as spawn:
            with self.assertRaises(ValueError):
                await self.provider.request(self.messages, schema=self.schema, structured=False)
            with patch.object(codex_provider.shutil, 'which', return_value=None):
                with self.assertRaises(RuntimeError):
                    await self.provider.request(self.messages, schema=self.schema, structured=True)
            spawn.assert_not_awaited()

    async def test_nonzero_cli_exit_does_not_expose_stderr(self):
        self.returncode = 1
        with self.assertRaises(RuntimeError) as caught:
            await self.run_request()
        self.assertNotIn('PRIVATE_STDERR', str(caught.exception))
        self.assertFalse(Path(self.kwargs['cwd']).exists())

    async def test_missing_or_invalid_answer_and_missing_usage_do_not_return_results(self):
        for answer, events, expected_exception in (
            (None, completed_events(), FileNotFoundError),
            ('not JSON', completed_events(), ValueError),
            ('{}', json.dumps({'type': 'thread.started'}), ValueError),
        ):
            self.answer, self.events = answer, events
            with self.subTest(answer=answer, events=events):
                with self.assertRaises(expected_exception):
                    await self.run_request()
                self.assertFalse(Path(self.kwargs['cwd']).exists())

    async def test_cancellation_delegates_tree_cleanup_and_preserves_cancellation(self):
        process = SimpleNamespace(pid=54321, returncode=None, communicate=AsyncMock(side_effect=asyncio.CancelledError()), kill=Mock())
        with patch.object(codex_provider.shutil, 'which', return_value='/fake/codex'), \
                patch.object(codex_provider.asyncio, 'create_subprocess_exec', new_callable=AsyncMock, return_value=process) as spawn, \
                patch.object(codex_provider, '_terminate_process_tree', new_callable=AsyncMock) as cleanup:
            with self.assertRaises(asyncio.CancelledError):
                await self.provider.request(self.messages, schema=self.schema, structured=True)
        cleanup.assert_awaited_once_with(process)
        self.assertEqual(process.communicate.await_count, 1)
        self.assertFalse(Path(spawn.await_args.kwargs['cwd']).exists())

    async def test_error_translation_hides_private_provider_details(self):
        error = self.provider.translate_error(RuntimeError('PRIVATE_ERROR_SECRET_OR_ACCOUNT'))
        self.assertNotIn('PRIVATE_ERROR', str(error))
        self.assertIn('classification request failed', str(error))
        await self.provider.aclose()


class CodexTreeCleanupTests(unittest.IsolatedAsyncioTestCase):
    def process(self, **overrides):
        values = dict(pid=54321, returncode=None, kill=Mock(), communicate=AsyncMock(return_value=(b'', b'')))
        values.update(overrides)
        return SimpleNamespace(**values)

    async def test_windows_terminates_only_owned_tree_without_shell_or_visible_window(self):
        process = self.process()
        run = Mock(return_value=SimpleNamespace(returncode=0))
        with patch.object(codex_provider, 'os', module_proxy(codex_provider.os, name='nt')), \
                patch.object(codex_provider, 'subprocess', module_proxy(codex_provider.subprocess, run=run, CREATE_NO_WINDOW=0x08000000)):
            await codex_provider._terminate_process_tree(process)
        run.assert_called_once_with(
            ['taskkill', '/PID', '54321', '/T', '/F'],
            stdin=codex_provider.subprocess.DEVNULL, stdout=codex_provider.subprocess.DEVNULL,
            stderr=codex_provider.subprocess.DEVNULL, creationflags=0x08000000,
            timeout=3, check=False,
        )
        process.kill.assert_called_once_with()
        process.communicate.assert_awaited_once_with()

    async def test_posix_kills_owned_session_group_even_when_leader_has_exited(self):
        for returncode in (None, 0):
            process = self.process(returncode=returncode)
            killpg = Mock()
            with self.subTest(returncode=returncode), \
                    patch.object(codex_provider, 'os', module_proxy(codex_provider.os, name='posix', killpg=killpg)), \
                    patch.object(codex_provider, 'signal', module_proxy(codex_provider.signal, SIGKILL=9)), \
                    patch.object(codex_provider.subprocess, 'run') as run:
                await codex_provider._terminate_process_tree(process)
            killpg.assert_called_once_with(process.pid, 9)
            run.assert_not_called()
            self.assertEqual(process.kill.call_count, 1 if returncode is None else 0)
            process.communicate.assert_awaited_once_with()

    async def test_cleanup_errors_cannot_replace_the_original_request_failure(self):
        for platform in ('nt', 'posix'):
            process = self.process(
                kill=Mock(side_effect=ProcessLookupError()),
                communicate=AsyncMock(side_effect=OSError('private cleanup error')),
            )
            with self.subTest(platform=platform), \
                    patch.object(codex_provider, 'os', module_proxy(codex_provider.os, name=platform, killpg=Mock(side_effect=ProcessLookupError()))), \
                    patch.object(codex_provider, 'signal', module_proxy(codex_provider.signal, SIGKILL=9)), \
                    patch.object(codex_provider, 'subprocess', module_proxy(codex_provider.subprocess, run=Mock(side_effect=OSError()), CREATE_NO_WINDOW=0x08000000)):
                await codex_provider._terminate_process_tree(process)
            process.kill.assert_called_once_with()
            process.communicate.assert_awaited_once_with()

    async def test_windows_tree_and_reap_deadlines_cancel_hanging_operations(self):
        cancelled = []
        deadlines = []

        async def hang(stage):
            try:
                await asyncio.Future()
            finally:
                cancelled.append(stage)

        async def shortened_wait_for(awaitable, *, timeout):
            deadlines.append(timeout)
            return await asyncio.wait_for(awaitable, timeout=0.01)

        process = self.process()
        async def communicate():
            return await hang('reap')
        process.communicate = AsyncMock(side_effect=communicate)
        async def to_thread(*_args, **_kwargs):
            return await hang('tree')
        with patch.object(codex_provider, 'os', module_proxy(codex_provider.os, name='nt')), \
                patch.object(codex_provider, 'subprocess', module_proxy(codex_provider.subprocess, CREATE_NO_WINDOW=0x08000000)), \
                patch.object(codex_provider, 'asyncio', module_proxy(asyncio, wait_for=shortened_wait_for, to_thread=to_thread)):
            await asyncio.wait_for(codex_provider._terminate_process_tree(process), timeout=1)
        self.assertEqual(deadlines, [3, 2])
        self.assertEqual(cancelled, ['tree', 'reap'])
        process.kill.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
