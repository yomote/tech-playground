"""Optional installed-SDK contract smoke test; no network or real credentials."""
import os
import unittest
from unittest.mock import patch
from runner import Run


class SdkContractTests(unittest.TestCase):
    def test_current_sdk_builds_real_magentic_graph_without_network(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'offline-test-not-a-real-key'}):
            for approval in (False, True):
                workflow = Run(mode='live', approval=approval).build_live_workflow()
                self.assertIsNotNone(workflow)

    def test_credentials_missing_is_a_local_run_failure(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            run = Run(mode='live')
            run.execute()
            self.assertEqual(run.status, 'failed')
            self.assertIn('OPENAI_API_KEY', run.events[-1]['message'])


if __name__ == '__main__':
    unittest.main()
