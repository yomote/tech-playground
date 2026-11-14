"""Focused checks for safe OTLP evidence and Gateway identity attribution."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sys
import tempfile
from threading import Lock, Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.trace.v1.trace_pb2 import Span

sys.path.insert(0, str(Path(__file__).parent))
import gateway_trial as trial  # noqa: E402


class GatewayTrialEvidenceTests(unittest.TestCase):
    def test_waits_for_executed_stage_model_spans_and_fails_closed(self):
        receiver = SimpleNamespace(lock=Lock(), spans=[])
        traces = {"propose": ("11" * 16, "22" * 8),
                  "review": ("33" * 16, "44" * 8)}

        def delayed_review_span():
            time.sleep(0.05)
            with receiver.lock:
                receiver.spans.append({"traceId": traces["review"][0],
                                       "attributes": {"gen_ai.request.model": "qwen3.5:35b"}})

        receiver.spans.append({"traceId": traces["propose"][0],
                               "attributes": {"gen_ai.request.model": "qwen3:8b"}})
        worker = Thread(target=delayed_review_span)
        worker.start()
        observed, waited_ms = trial.await_model_spans(
            receiver, traces, {"propose", "verify", "review"}, seconds=0.5)
        worker.join(timeout=1)
        self.assertEqual(observed, {"propose": True, "review": True})
        self.assertLess(waited_ms, 1000)

        with receiver.lock:
            receiver.spans = receiver.spans[:1]
        observed, waited_ms = trial.await_model_spans(
            receiver, traces, {"propose", "review"}, seconds=0.05)
        self.assertEqual(observed, {"propose": True, "review": False})
        self.assertGreaterEqual(waited_ms, 40)
        self.assertLess(waited_ms, 1000)

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

    def test_langfuse_relay_sends_only_allowlisted_metadata(self):
        raw = Span(trace_id=bytes.fromhex("11" * 16),
                   span_id=bytes.fromhex("22" * 8),
                   parent_span_id=bytes.fromhex("33" * 8),
                   name="private prompt", start_time_unix_nano=100,
                   end_time_unix_nano=200)
        for key, value in (("gen_ai.request.model", "qwen3:8b"),
                           ("gen_ai.input.messages", "private prompt and patch"),
                           ("http.request.body", "secret request"),
                           ("gen_ai.response.model", "untrusted model string")):
            attr = raw.attributes.add()
            attr.key = key
            attr.value.string_value = value
        event = raw.events.add()
        event.name = "secret tool output"
        link = raw.links.add()
        link.trace_id = bytes.fromhex("44" * 16)
        clean = trial.safe_span(raw, "BT-1234567890abcdef", "propose")
        batch = trial.safe_batch([clean], raw.trace_id, raw.parent_span_id,
                                 "BT-1234567890abcdef", "propose")
        wire = batch.SerializeToString()
        for secret in (b"private prompt", b"secret request", b"secret tool output",
                       b"untrusted model string"):
            self.assertNotIn(secret, wire)
        self.assertNotIn(raw.links[0].trace_id, wire)
        self.assertEqual(len(batch.resource_spans[0].scope_spans[0].spans), 2)

        received = []

        class FakeLangfuse(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                received.append((self.path, self.headers,
                                 self.rfile.read(int(self.headers["content-length"]))))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLangfuse)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            relay = trial.LangfuseRelay(f"http://127.0.0.1:{server.server_port}",
                                        "pk-test", "sk-test")
            self.assertEqual(relay.send(batch), 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(len(received), 1)
        path, headers, payload = received[0]
        self.assertEqual(path, trial.OTLP_PATH)
        self.assertEqual(headers["x-langfuse-ingestion-version"], "4")
        self.assertEqual(headers["Authorization"],
                         "Basic cGstdGVzdDpzay10ZXN0")
        self.assertEqual(payload, wire)
        parsed = ExportTraceServiceRequest()
        parsed.ParseFromString(payload)
        self.assertEqual(parsed.resource_spans[0].scope_spans[0].spans[0].name,
                         "blue-security-propose")

    def test_langfuse_relay_rejects_plain_http_remote_origin(self):
        with self.assertRaises(ValueError):
            trial.LangfuseRelay("http://example.org", "pk-test", "sk-test")

    def test_public_reason_classifies_only_fixed_proposal_timeout(self):
        reason = "goose_proposal_failed:exit=-15:timedOut=true:stdoutBytes=0:stderrBytes=42"
        self.assertEqual(trial.public_stage_reason(reason),
                         ("goose_proposal_timeout", True))
        review_reason = "review_goose_failed:exit=-9:timedOut=true:stdoutBytes=0:stderrBytes=1"
        self.assertEqual(trial.public_stage_reason(review_reason),
                         ("review_goose_timeout", True))
        self.assertEqual(trial.public_stage_reason(reason + ":private prompt"),
                         ("unclassified_stage_failure", False))
        self.assertEqual(trial.public_stage_reason("goose_proposal_failed:exit=1:timedOut=false:stdoutBytes=0:stderrBytes=42"),
                         ("goose_proposal_failed", False))

    def test_gateway_child_env_does_not_inherit_project_keys(self):
        with patch.dict(os.environ, {"LANGFUSE_SECRET_KEY": "private",
                                     "LANGFUSE_PUBLIC_KEY": "public",
                                     "GITHUB_TOKEN": "private"}):
            child = trial.gateway_child_env()
        self.assertNotIn("LANGFUSE_SECRET_KEY", child)
        self.assertNotIn("LANGFUSE_PUBLIC_KEY", child)
        self.assertNotIn("GITHUB_TOKEN", child)

    def test_langfuse_http_acceptance_requires_both_roles(self):
        self.assertFalse(trial.langfuse_http_accepted({"propose": 200}))
        self.assertFalse(trial.langfuse_http_accepted({"propose": 200, "review": 401}))
        self.assertTrue(trial.langfuse_http_accepted({"propose": 200, "review": 202}))

    def test_local_receiver_keeps_only_sanitized_allowed_trace(self):
        incoming = ExportTraceServiceRequest()
        resource = incoming.resource_spans.add()
        secret_resource = resource.resource.attributes.add()
        secret_resource.key = "secret.resource"
        secret_resource.value.string_value = "resource credential"
        scope = resource.scope_spans.add()
        for trace_hex in ("11" * 16, "44" * 16):
            span = scope.spans.add()
            span.trace_id = bytes.fromhex(trace_hex)
            span.span_id = bytes.fromhex("22" * 8)
            span.parent_span_id = bytes.fromhex("33" * 8)
            span.start_time_unix_nano = 100
            span.end_time_unix_nano = 200
            secret = span.attributes.add()
            secret.key = "gen_ai.input.messages"
            secret.value.string_value = "private prompt and patch"
        receiver = trial.OtlpReceiver(0, "Basic test-only")
        receiver.allowed_traces = {
            bytes.fromhex("11" * 16): ("BT-1234567890abcdef", "propose",
                                     bytes.fromhex("33" * 8))}
        thread = Thread(target=receiver.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(f"http://127.0.0.1:{receiver.server_port}{trial.OTLP_PATH}",
                              data=incoming.SerializeToString(), method="POST",
                              headers={"Authorization": "Basic test-only",
                                       "x-langfuse-ingestion-version": "4"})
            with urlopen(request, timeout=2) as response:
                self.assertEqual(response.status, 200)
            with receiver.lock:
                self.assertEqual(len(receiver.spans), 2)
                self.assertEqual(len(receiver.safe_spans), 1)
                cleaned = receiver.safe_spans[0].SerializeToString()
            self.assertNotIn(b"private prompt and patch", cleaned)
            self.assertNotIn(b"resource credential", cleaned)
        finally:
            receiver.shutdown()
            receiver.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
