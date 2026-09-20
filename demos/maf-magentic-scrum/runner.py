"""Mock trajectory + real Microsoft Agent Framework Magentic orchestration."""
from __future__ import annotations
import ast
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent
TASK = 'Implement clamp(value, low, high): clamp numbers to bounds, preserve floats, raise ValueError if low > high. Ask Planner for acceptance criteria, Developer for implementation, Reviewer for review, Tester for actual tests; fix failures and report evidence.'
GOOD = 'def clamp(value, low, high):\n    if low > high:\n        raise ValueError("invalid bounds")\n    return min(max(value, low), high)\n'
BUG = 'def clamp(value, low, high):\n    return min(max(value, low), high)\n'


class Run:
    def __init__(self, mode='mock', scenario='review-failure', approval=False):
        self.id = str(uuid.uuid4())
        self.mode, self.scenario, self.approval = mode, scenario, approval
        self.events = []
        self.round = self.replans = 0
        self.current = 'Manager'
        self.status = 'running'
        self.pending = None
        self.feedback = None
        self.condition = threading.Condition()
        self.folder = ROOT / 'runs' / self.id
        self.folder.mkdir(parents=True)
        shutil.copy(ROOT / 'fixture/test_solution.py', self.folder)
        shutil.copy(ROOT / 'fixture/solution.py', self.folder)

    def emit(self, kind, agent='Manager', message='', **data):
        with self.condition:
            event = dict(seq=len(self.events) + 1, time=time.time(), mode=self.mode, kind=kind,
                         agent=agent, message=message, round=self.round, replans=self.replans, **data)
            self.events.append(event)
            with (self.folder / 'events.jsonl').open('a', encoding='utf-8') as output:
                output.write(json.dumps(event, ensure_ascii=False, default=str) + '\n')

    def snapshot(self):
        with self.condition:
            return dict(id=self.id, mode=self.mode, status=self.status, pending=self.pending,
                        round=self.round, replans=self.replans, current=self.current,
                        task=TASK, events=list(self.events))

    def checkpoint(self, stage, message):
        if not self.approval:
            return ''
        with self.condition:
            self.pending = stage
            self.feedback = None
            self.emit('approval', message=message, stage=stage)
            if not self.condition.wait_for(lambda: self.feedback is not None, timeout=900):
                raise TimeoutError('Human checkpoint expired after 15 minutes')
            reply = self.feedback
            self.pending = None
            if reply == 'reject':
                raise RuntimeError('Run rejected by human')
            self.emit('approved', message=stage)
            return reply

    def respond(self, reply):
        with self.condition:
            if self.pending is None:
                raise ValueError('No approval pending')
            if reply not in ('approve', 'reject'):
                raise ValueError('Choose approve or reject')
            self.feedback = reply
            self.condition.notify_all()

    def select(self, agent, instruction):
        self.round += 1
        self.current = agent
        self.emit('decision', message=instruction, nextAgent=agent)
        time.sleep(.2)

    def read_fixture(self):
        """Read the implementation and immutable acceptance tests."""
        result = {name: (self.folder / name).read_text(encoding='utf-8') for name in ('solution.py', 'test_solution.py')}
        self.emit('tool', self.current, 'read_fixture', result=result)
        return json.dumps(result)

    def write_solution(self, source: str):
        """Replace solution.py with one pure clamp function. No imports, file/network access, or arbitrary commands."""
        tree = ast.parse(source)
        allowed = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.If, ast.Compare,
                   ast.Gt, ast.Lt, ast.GtE, ast.LtE, ast.Eq, ast.NotEq, ast.Load, ast.Name, ast.Constant,
                   ast.Raise, ast.Call, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.UnaryOp, ast.USub,
                   ast.BoolOp, ast.And, ast.Or, ast.Expr)
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef) or tree.body[0].name != 'clamp':
            raise ValueError('Expected one clamp function')
        fn = tree.body[0]
        if fn.decorator_list or fn.args.defaults or fn.args.kw_defaults or [a.arg for a in fn.args.args] != ['value', 'low', 'high']:
            raise ValueError('Expected clamp(value, low, high) without decorators or defaults')
        for node in ast.walk(tree):
            if not isinstance(node, allowed):
                raise ValueError(f'Unsupported fixture syntax: {type(node).__name__}')
            if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id not in ('min', 'max', 'ValueError')):
                raise ValueError('Only min, max, and ValueError calls allowed')
            if isinstance(node, ast.Name) and node.id not in ('value', 'low', 'high', 'min', 'max', 'ValueError'):
                raise ValueError('Unknown name')
        (self.folder / 'solution.py').write_text(source, encoding='utf-8')
        self.emit('tool', self.current, 'write_solution', source=source)
        return 'solution.py updated'

    def run_tests(self):
        """Run the fixed fixture unittest suite and return the actual exit code/output."""
        result = subprocess.run([sys.executable, '-m', 'unittest', '-v', 'test_solution'], cwd=self.folder,
                                capture_output=True, text=True, timeout=10, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        output = dict(exitCode=result.returncode, output=result.stdout + result.stderr)
        self.emit('tool', self.current, 'run_tests', result=output)
        return json.dumps(output)

    def run_mock(self):
        self.emit('plan', message='MOCK scripted manager: plan → implement → review → test → summarize. Fixture writes/tests are real.')
        self.checkpoint('plan', TASK)
        self.select('Planner', 'Define acceptance criteria and invalid-bounds behavior')
        self.emit('agent', 'Planner', TASK)
        self.select('Developer', 'Implement the fixture feature')
        self.read_fixture()
        self.write_solution(GOOD if self.scenario == 'happy-path' else BUG)
        self.select('Reviewer', 'Review bounds and numeric behavior')
        self.read_fixture()
        if self.scenario == 'review-failure':
            self.emit('agent', 'Reviewer', 'FAIL: invalid bounds are not rejected.')
            self.select('Developer', 'Reviewer found a defect; correct invalid bounds')
            self.write_solution(GOOD)
            self.select('Reviewer', 'Review the corrected implementation')
        self.emit('agent', 'Reviewer', 'PASS' if self.scenario != 'test-failure' else 'Mock review missed the invalid-bounds bug.')
        self.select('Tester', 'Run the acceptance suite')
        result = json.loads(self.run_tests())
        if result['exitCode']:
            self.emit('stall', message='Tests failed; current plan made insufficient progress')
            self.replans += 1
            self.emit('replan', message='Inspect invalid-bounds failure, repair, review again, rerun tests')
            self.checkpoint('plan', 'Repair invalid bounds and rerun acceptance tests')
            self.select('Developer', 'Repair the failing test case')
            self.write_solution(GOOD)
            self.select('Reviewer', 'Review the repair'); self.read_fixture()
            self.emit('agent', 'Reviewer', 'PASS: explicit ValueError and bounded return')
            self.select('Tester', 'Verify the repaired implementation')
            result = json.loads(self.run_tests())
        if result['exitCode']:
            raise RuntimeError('Acceptance tests failed')
        self.checkpoint('final', 'All five tests passed. Approve final report (no deployment).')
        self.emit('final', message='PASS — five actual fixture tests passed. Manager/agent trajectory was scripted mock data.')

    def build_live_workflow(self):
        # Lazy imports keep credential-free mode independent of the optional SDK.
        from agent_framework import Agent
        from agent_framework.openai import OpenAIChatClient
        from agent_framework.orchestrations import MagenticBuilder
        if not os.getenv('OPENAI_API_KEY'):
            raise ValueError('Live mode requires OPENAI_API_KEY in this demo .env; use mock mode without credentials.')
        client = OpenAIChatClient(model=os.getenv('OPENAI_CHAT_MODEL_ID', 'gpt-4.1-mini'))
        tools = [self.read_fixture, self.write_solution, self.run_tests]
        participants = [Agent(name=name, description=description, instructions=description, client=client, tools=agent_tools)
                        for name, description, agent_tools in [
                            ('Planner', 'Define acceptance criteria for the clamp feature. Read fixture tests.', [tools[0]]),
                            ('Developer', 'Implement clamp by calling write_solution with one pure Python function. Call read_fixture first. Fix reported defects.', tools[:2]),
                            ('Reviewer', 'Review the actual implementation with read_fixture. Report specific defects; do not modify code.', [tools[0]]),
                            ('Tester', 'Call run_tests and report its actual result. Never claim tests passed without a tool result.', [tools[0], tools[2]])]]
        manager = Agent(name='Manager', client=client, instructions='Coordinate software implementation. Delegate to specialists, respond to failures, require review and passing tests before completion.')
        return MagenticBuilder(participants=participants, intermediate_output_from=participants, manager_agent=manager,
                                   max_round_count=12, max_stall_count=2, max_reset_count=2, enable_plan_review=self.approval).build()

    async def run_live(self):
        from agent_framework.orchestrations import MagenticPlanReviewRequest
        workflow = self.build_live_workflow()
        responses = None
        final = None
        while final is None:
            stream = workflow.run(stream=True, responses=responses) if responses else workflow.run(TASK, stream=True)
            pending = None
            async for event in stream:
                if event.type == 'magentic_orchestrator':
                    kind = event.data.event_type.name
                    content = event.data.content
                    payload = content.to_dict() if hasattr(content, 'to_dict') else {'text': str(content)}
                    if kind == 'PROGRESS_LEDGER_UPDATED':
                        self.round += 1
                        self.current = str(payload.get('next_speaker', {}).get('answer', 'Manager'))
                        if payload.get('is_in_loop', {}).get('answer') or not payload.get('is_progress_being_made', {}).get('answer', True):
                            self.emit('stall', message='Progress ledger reports a loop or no progress', ledger=payload)
                    if kind == 'REPLANNED':
                        self.replans += 1
                    self.emit('decision' if kind == 'PROGRESS_LEDGER_UPDATED' else 'replan' if kind == 'REPLANNED' else 'plan', message=kind, ledger=payload)
                elif event.type == 'request_info' and event.request_type is MagenticPlanReviewRequest:
                    pending = event
                elif event.type in ('intermediate', 'output'):
                    self.emit('agent', str(event.executor_id), str(event.data))
                elif event.type in ('error', 'executor_failed'):
                    raise RuntimeError(str(event.data))
                else:
                    self.emit('framework', str(getattr(event, 'executor_id', 'Manager')), str(event.data), eventType=event.type)
            result = await stream.get_final_response()
            outputs = result.get_outputs()
            if outputs:
                final = str(outputs[-1])
            responses = None
            if pending:
                await asyncio.to_thread(self.checkpoint, 'plan', pending.data.plan.text)
                responses = {pending.request_id: pending.data.approve()}
            elif final is None:
                raise RuntimeError('Workflow ended without final output or approval request')
        # Independent verification prevents a model's unsupported success claim.
        self.current = 'Verifier'
        verification = json.loads(self.run_tests())
        if verification['exitCode']:
            raise RuntimeError('Manager finished but independent fixture tests failed. See trajectory.')
        await asyncio.to_thread(self.checkpoint, 'final', final)
        self.emit('final', message=final, verifiedTests=verification)

    def execute(self):
        try:
            if self.mode == 'live':
                asyncio.run(self.run_live())
            else:
                self.run_mock()
            self.status = 'completed'
        except Exception as error:
            self.emit('error', message=str(error))
            self.status = 'failed'
