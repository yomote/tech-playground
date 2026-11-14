"""Focused checks for safe OTLP evidence and Gateway identity attribution."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

from opentelemetry.proto.trace.v1.trace_pb2 import Span

sys.path.insert(0, str(Path(__file__).parent))
import gateway_trial as trial  # noqa: E402


class GatewayTrialEvidenceTests(unittest.TestCase):
    def test_otlp_projection_discards_content_and_preserves_trace_relation(self):
        span = Span(trace_id=bytes.fromhex("11" * 16),
                    span_id=bytes.fromhex("22" * 8),
                    parent_span_id=bytes.fromhex("33" * 8))
        attr = span.attributes.add()
        attr.key = "gen_ai.request.model"
        attr.value.string_value = "qwen3:8b"
        secret = span.attributes.add()
        secret.key = "gen_ai.input.messages"
        secret.value.string_value = "private prompt and patch"
        projected = trial.project_span(span)
        self.assertTrue(projected["knownContentAttributeNameSeen"])
        self.assertNotIn("private prompt and patch", json.dumps(projected))
        self.assertEqual(
            trial.stage_trace([projected], "11" * 16, "33" * 8, "qwen3:8b"),
            {"traceId": "11" * 16, "spanCount": 1, "parentLinked": True,
             "expectedModelObserved": True, "knownContentAttributeNameSeen": True},
        )

    def test_gateway_log_counts_only_expected_subject_and_allowlisted_models(self):
        lines = (
            "request gateway=x listener=model jwt.sub=blue-goose-propose "
            "http.status=200 gen_ai.request.model=qwen3:8b\n"
            "request gateway=x listener=model jwt.sub=other "
            "http.status=200 gen_ai.request.model=qwen3:8b\n"
            "request gateway=x listener=model jwt.sub=blue-goose-review "
            "http.status=404 gen_ai.request.model=not-allowed\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.log"
            path.write_text(lines)
            counts = trial.gateway_counts(path)
        self.assertEqual(counts["propose"],
                         {"requests": 1, "successes": 1, "statusCodes": [200],
                          "modelNames": ["qwen3:8b"]})
        self.assertEqual(counts["review"],
                         {"requests": 1, "successes": 0, "statusCodes": [404],
                          "modelNames": []})


if __name__ == "__main__":
    unittest.main()
