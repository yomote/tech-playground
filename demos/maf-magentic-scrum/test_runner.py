import json
import threading
import time
import unittest
from runner import Run


class RunnerTests(unittest.TestCase):
    def test_mock_scenarios_use_actual_tests(self):
        for scenario in ('happy-path', 'review-failure', 'test-failure'):
            run = Run(scenario=scenario)
            run.execute()
            self.assertEqual(run.status, 'completed', run.events)
            tests = [event for event in run.events if event['message'] == 'run_tests']
            self.assertEqual(tests[-1]['result']['exitCode'], 0)
            self.assertEqual(run.replans, 1 if scenario == 'test-failure' else 0)
            if scenario == 'test-failure':
                self.assertNotEqual(tests[0]['result']['exitCode'], 0)
            lines = (run.folder / 'events.jsonl').read_text(encoding='utf-8').splitlines()
            self.assertEqual(len(lines), len(run.events))
            self.assertEqual(json.loads(lines[-1])['kind'], 'final')

    def test_approval_blocks_before_developer_and_rejects(self):
        run = Run(approval=True)
        worker = threading.Thread(target=run.execute)
        worker.start()
        for _ in range(100):
            if run.pending:
                break
            time.sleep(.01)
        self.assertEqual(run.pending, 'plan')
        self.assertFalse(any(event['message'] == 'write_solution' for event in run.events))
        run.respond('reject')
        worker.join(2)
        self.assertEqual(run.status, 'failed')

    def test_fixture_rejects_arbitrary_commands(self):
        run = Run()
        with self.assertRaises(ValueError):
            run.write_solution('import os\ndef clamp(value, low, high):\n    return os.system("bad")')


if __name__ == '__main__':
    unittest.main()
