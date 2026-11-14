#!/usr/bin/env python3
"""Run one local Blue Team case through JWT Agentgateway and inspect OTLP.

This is an isolated model-path experiment. The receiver is not Langfuse, and
Goose's local Developer extension does not pass through Agentgateway. No raw
prompts, patches, model responses, credentials, or spans are saved as evidence.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
from threading import Lock, Thread
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest, ExportTraceServiceResponse,
)


GATEWAY_SHA256 = "496ed27e6a96b55a9ad7cabe5c2be7172511f44a3cb113e533d22bf93a7f079d"
GOOSE_SHA256 = "2419b124b0b1eb4e02b19cbc2373a91e8927010c8ed2902680c7a2864a49785f"
SCANNER_SHA256 = "160c6253e2922e9e0a6a9771c5c4c40a4b3c6090b34a3cdd5e80c96cc2e4218b"
ISSUER = "urn:playground:blue-goose:trial"
AUDIENCE = "playground-blue-goose-trial"
MODELS = {"propose": "qwen3:8b", "review": "qwen3.5:35b"}
OTLP_PATH = "/api/public/otel/v1/traces"
MAX_OTLP_BYTES = 4 * 1024 * 1024
CASE_ID = re.compile(r"^BT-[0-9a-f]{16}$")
KNOWN_CONTENT_PREFIXES = (
    "gen_ai.input.", "gen_ai.output.", "gen_ai.prompt", "gen_ai.completion",
    "llm.input_messages", "llm.output_messages", "langfuse.observation.input",
    "langfuse.observation.output", "http.request.body", "http.response.body",
    "request.body", "response.body",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_private(path: Path, data: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(data)


def b64url(number: int) -> str:
    return base64.urlsafe_b64encode(number.to_bytes(32, "big")).rstrip(b"=").decode("ascii")


def available(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def await_port(port: int, process: subprocess.Popen, seconds: int = 15) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def stop_group(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def attribute_value(attribute):
    value = attribute.value
    kind = value.WhichOneof("value")
    return getattr(value, kind) if kind else None


def project_span(span) -> dict:
    """Keep only correlation, model, status and usage; discard raw span data."""
    selected = {attribute.key: attribute_value(attribute) for attribute in span.attributes
                if attribute.key in (
                    "gen_ai.request.model", "gen_ai.response.model",
                    "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
                    "http.response.status_code", "http.status_code",
                )}
    names = {attribute.key for attribute in span.attributes}
    names.update(attribute.key for event in span.events for attribute in event.attributes)
    return {
        "traceId": span.trace_id.hex(), "spanId": span.span_id.hex(),
        "parentSpanId": span.parent_span_id.hex(), "attributes": selected,
        "knownContentAttributeNameSeen": any(name.startswith(KNOWN_CONTENT_PREFIXES) for name in names),
    }


class OtlpReceiver(ThreadingHTTPServer):
    def __init__(self, port: int, expected_auth: str):
        super().__init__(("127.0.0.1", port), OtlpHandler)
        self.expected_auth = expected_auth
        self.lock = Lock()
        self.requests = 0
        self.auth_valid = True
        self.ingestion_v4 = True
        self.parse_errors = 0
        self.spans: list[dict] = []


class OtlpHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        if self.path != OTLP_PATH:
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("content-length", "0"))
        except ValueError:
            size = 0
        if not 0 < size <= MAX_OTLP_BYTES:
            self.send_error(413)
            return
        auth_valid = self.headers.get("authorization") == self.server.expected_auth
        ingestion_v4 = self.headers.get("x-langfuse-ingestion-version") == "4"
        if not auth_valid or not ingestion_v4:
            self.send_error(401)
            return
        batch = ExportTraceServiceRequest()
        try:
            batch.ParseFromString(self.rfile.read(size))
            spans = [project_span(span) for resource in batch.resource_spans
                     for scope in resource.scope_spans for span in scope.spans]
        except Exception:
            with self.server.lock:
                self.server.parse_errors += 1
            self.send_error(400)
            return
        with self.server.lock:
            self.server.requests += 1
            self.server.spans.extend(spans)
        payload = ExportTraceServiceResponse().SerializeToString()
        self.send_response(200)
        self.send_header("content-type", "application/x-protobuf")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except BrokenPipeError:
            # The isolated exporter may close while the trial shuts down.
            pass

    def log_message(self, *_args):
        return


def gateway_config(jwks: Path, model_port: int, otlp_port: int, admin_port: int,
                   otlp_auth: str) -> str:
    return f'''config:
  adminAddr: 127.0.0.1:{admin_port}
  statsAddr: "off"
  readinessAddr: "off"
frontendPolicies:
  tracing:
    host: 127.0.0.1:{otlp_port}
    protocol: http
    path: {OTLP_PATH}
    randomSampling: true
    clientSampling: true
    policies:
      requestHeaderModifier:
        set:
          Authorization: "{otlp_auth}"
          x-langfuse-ingestion-version: "4"
gateways:
  model:
    port: {model_port}
    bindAddress: 127.0.0.1
llm:
  gateways: [model]
  policies:
    jwtAuth:
      mode: strict
      issuer: {ISSUER}
      audiences: [{AUDIENCE}]
      jwks:
        file: {jwks}
    localRateLimit:
      - maxTokens: 50
        tokensPerFill: 50
        fillInterval: 1h
        type: requests
        key: jwt.sub
  models:
    - name: {MODELS['propose']}
      provider: ollama
      params:
        model: {MODELS['propose']}
        baseUrl: http://127.0.0.1:11434/v1
      authorization:
        rules:
          - require: 'jwt.sub == "blue-goose-propose"'
    - name: {MODELS['review']}
      provider: ollama
      params:
        model: {MODELS['review']}
        baseUrl: http://127.0.0.1:11434/v1
      authorization:
        rules:
          - require: 'jwt.sub == "blue-goose-review"'
'''


def http_status(port: int, model: str, token: str | None) -> int | None:
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "probe"}],
                       "max_tokens": 1}).encode()
    request = Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=body, headers=headers)
    try:
        with urlopen(request, timeout=8) as response:
            response.read(1024)
            return response.status
    except HTTPError as error:
        error.read(1024)
        return error.code
    except (URLError, TimeoutError):
        return None


def visible_models(port: int, token: str) -> tuple[int | None, list[str]]:
    request = Request(f"http://127.0.0.1:{port}/v1/models",
                      headers={"authorization": f"Bearer {token}"})
    try:
        with urlopen(request, timeout=8) as response:
            body = json.loads(response.read(64 * 1024))
            names = [item.get("id") for item in body.get("data", [])]
            return response.status, sorted(name for name in names if name in MODELS.values())
    except HTTPError as error:
        error.read(1024)
        return error.code, []
    except (URLError, TimeoutError, ValueError, TypeError):
        return None, []


def gateway_counts(path: Path) -> dict:
    """Extract identities and request counts, never raw log lines."""
    result = {"propose": {"requests": 0, "successes": 0, "statusCodes": [], "modelNames": []},
              "review": {"requests": 0, "successes": 0, "statusCodes": [], "modelNames": []}}
    for line in path.read_text(errors="replace").splitlines():
        if "request gateway=" not in line or "listener=model" not in line:
            continue
        subject = re.search(r"jwt\.sub=blue-goose-(propose|review)(?:\s|$)", line)
        if not subject:
            continue
        stage = subject.group(1)
        status = re.search(r"http\.status=(\d+)", line)
        model = re.search(r"gen_ai\.request\.model=([^\s]+)", line)
        result[stage]["requests"] += 1
        result[stage]["successes"] += bool(status and status.group(1) == "200")
        if status:
            result[stage]["statusCodes"].append(int(status.group(1)))
        if model and model.group(1) in MODELS.values() and model.group(1) not in result[stage]["modelNames"]:
            result[stage]["modelNames"].append(model.group(1))
    return result


def stage_env(model: str, token: str, port: int, trace_id: str, parent_id: str,
              case_id: str) -> dict[str, str]:
    # Run ID is for correlation only. JWT claims, not this header, identify caller.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": str(Path.home()), "LANG": "C.UTF-8",
           "GOOSE_PROVIDER": "openai", "GOOSE_MODEL": model,
           "OPENAI_HOST": f"http://127.0.0.1:{port}",
           "OPENAI_BASE_PATH": "v1/chat/completions", "OPENAI_API_KEY": token,
           "OPENAI_CUSTOM_HEADERS": f"X-Playground-Run-Id={case_id},traceparent=00-{trace_id}-{parent_id}-01",
           "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    return env


def run_stage(argv: list[str], env: dict[str, str], out: Path) -> dict:
    started = time.monotonic()
    # security_loop owns timeouts and teardown for its detached Goose/check
    # processes. An outer subprocess timeout would kill only security_loop and
    # leave its process sessions running without worktree cleanup.
    process = subprocess.run(argv, env=env, capture_output=True)
    code = process.returncode
    timed_out = False
    doc = json.loads(out.read_text()) if out.is_file() else {}
    result = {"exitCode": code, "timedOut": timed_out,
              "durationMs": round((time.monotonic() - started) * 1000),
              "status": doc.get("status", "missing_result")}
    if isinstance(doc.get("reason"), str):
        # An untrusted reviewer can put patch or prompt text in a free-form reason.
        result["reason"] = (doc["reason"] if re.fullmatch(r"[a-z0-9_]{1,80}", doc["reason"])
                            else "unclassified_stage_failure")
    if doc.get("producer"):
        result["producer"] = doc["producer"]
    if doc.get("patchSha256"):
        result["patchSha256"] = doc["patchSha256"]
    if doc.get("rescan"):
        result["targetFindingRows"] = doc["rescan"].get("targetFindingRows")
    if doc.get("reviewer"):
        result["reviewerVerdict"] = doc["reviewer"].get("verdict")
    return result


def stage_trace(spans: list[dict], trace_id: str, parent_id: str, model: str) -> dict:
    selected = [span for span in spans if span["traceId"] == trace_id]
    model_spans = [span for span in selected
                   if span["attributes"].get("gen_ai.request.model") == model]
    return {"traceId": trace_id, "spanCount": len(selected),
            "parentLinked": any(span["parentSpanId"] == parent_id for span in selected),
            "expectedModelObserved": bool(model_spans),
            "knownContentAttributeNameSeen": any(
                span["knownContentAttributeNameSeen"] for span in selected)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--goose", type=Path, required=True)
    parser.add_argument("--gateway", type=Path, required=True)
    parser.add_argument("--scanner", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-port", type=int, default=16520)
    parser.add_argument("--otlp-port", type=int, default=16522)
    parser.add_argument("--admin-port", type=int, default=16523)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--authorization-only", action="store_true",
                        help="check Gateway JWT/model policy without running Goose")
    args = parser.parse_args()
    if not 60 <= args.timeout <= 600:
        parser.error("--timeout must be 60..600")
    if len({args.model_port, args.otlp_port, args.admin_port}) != 3 or not all(
            1024 <= p <= 65535 and available(p)
            for p in (args.model_port, args.otlp_port, args.admin_port)):
        parser.error("isolated trial ports must be distinct and free")
    for name, path, expected in (("goose", args.goose, GOOSE_SHA256),
                                 ("gateway", args.gateway, GATEWAY_SHA256),
                                 ("scanner", args.scanner, SCANNER_SHA256)):
        if not path.is_file() or sha256(path) != expected:
            parser.error(f"{name} binary SHA-256 does not match pinned release")
    case = json.loads(args.case.read_text())
    if not CASE_ID.fullmatch(case.get("caseId", "")):
        parser.error("invalid case ID")
    if args.output.exists():
        parser.error("output exists; choose a new path")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = {"startedAtUtc": now_utc(), "caseId": case["caseId"],
              "caseSha256": sha256(args.case), "baseSha": case["baseSha"],
              "gatewayVersion": "1.6.0", "gooseVersion": "1.54.0",
              "modelCatalog": list(MODELS.values()),
              "otlpRecipient": "local_inspection_receiver",
              "receiverIsLangfuse": False, "langfuseReadbackConfirmed": False,
              "developerToolGovernedByGateway": False,
              "runIdHeaderIsAuthorization": False,
              "allOtlpAttributesProvenSafe": False, "publishable": False,
              "checks": {}, "stages": {}, "traces": {}}
    receiver = gateway = None
    try:
        with tempfile.TemporaryDirectory(prefix="blue-goose-gateway-") as directory:
            temp = Path(directory)
            key = ec.generate_private_key(ec.SECP256R1())
            public = key.public_key().public_numbers()
            jwks = {"keys": [{"use": "sig", "kty": "EC", "kid": "blue-goose-trial",
                              "crv": "P-256", "alg": "ES256", "x": b64url(public.x),
                              "y": b64url(public.y)}]}
            jwks_file = temp / "public.jwks"
            write_private(jwks_file, json.dumps(jwks))
            now = datetime.now(timezone.utc)
            tokens = {stage: jwt.encode({"iss": ISSUER, "aud": AUDIENCE,
                                         "sub": f"blue-goose-{stage}", "iat": now,
                                         "exp": now + timedelta(minutes=45)}, key,
                                        algorithm="ES256", headers={"kid": "blue-goose-trial"})
                      for stage in MODELS}
            otlp_auth = "Basic " + base64.b64encode(
                f"synthetic-{secrets.token_hex(8)}:{secrets.token_hex(16)}".encode()).decode()
            config_file = temp / "gateway.yaml"
            write_private(config_file, gateway_config(jwks_file, args.model_port,
                                                      args.otlp_port, args.admin_port, otlp_auth))
            valid = subprocess.run([str(args.gateway), "--validate-only", "-f", str(config_file)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            result["checks"]["gatewayConfigValid"] = valid.returncode == 0
            if valid.returncode:
                return finish(args.output, result)
            receiver = OtlpReceiver(args.otlp_port, otlp_auth)
            Thread(target=receiver.serve_forever, daemon=True).start()
            log_file = temp / "gateway.log"
            with log_file.open("wb") as log:
                gateway = subprocess.Popen([str(args.gateway), "-f", str(config_file)],
                                           stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                if not await_port(args.model_port, gateway):
                    result["checks"]["gatewayStarted"] = False
                    return finish(args.output, result)
                result["checks"]["gatewayStarted"] = True
                result["checks"]["withoutJwtHttpStatus"] = http_status(
                    args.model_port, "not-allowed", None)
                result["checks"]["unknownModelHttpStatus"] = http_status(
                    args.model_port, "not-allowed", tokens["propose"])
                result["checks"]["proposeTokenReviewModelStatus"] = http_status(
                    args.model_port, MODELS["review"], tokens["propose"])
                result["checks"]["reviewTokenProposeModelStatus"] = http_status(
                    args.model_port, MODELS["propose"], tokens["review"])
                for stage, token in tokens.items():
                    status, models = visible_models(args.model_port, token)
                    result["checks"][f"{stage}VisibleModelsStatus"] = status
                    result["checks"][f"{stage}VisibleModels"] = models
                result["checks"]["subjectModelIsolationConfirmed"] = (
                    result["checks"]["proposeTokenReviewModelStatus"] == 403
                    and result["checks"]["reviewTokenProposeModelStatus"] == 403
                    and all(result["checks"][f"{stage}VisibleModelsStatus"] == 200
                            and result["checks"][f"{stage}VisibleModels"] == [model]
                            for stage, model in MODELS.items()))
                if (result["checks"]["withoutJwtHttpStatus"] != 401
                        or result["checks"]["unknownModelHttpStatus"] != 404
                        or not result["checks"]["subjectModelIsolationConfirmed"]):
                    return finish(args.output, result)
                if args.authorization_only:
                    result["authorizationChecksPassed"] = True
                    return finish(args.output, result)
                trace_meta = {stage: (secrets.token_hex(16), secrets.token_hex(8))
                              for stage in MODELS}
                loop = Path(__file__).with_name("security_loop.py")
                proposal, verification, review = (temp / f"{name}.json" for name in
                                                  ("proposal", "verification", "review"))
                propose_cmd = [sys.executable, str(loop), "propose", "--repo", str(args.repo),
                               "--case", str(args.case), "--goose", str(args.goose),
                               "--timeout", str(args.timeout), "--out", str(proposal)]
                result["stages"]["propose"] = run_stage(
                    propose_cmd,
                    stage_env(MODELS["propose"], tokens["propose"], args.model_port,
                              *trace_meta["propose"], case["caseId"]), proposal)
                if result["stages"]["propose"]["status"] == "proposed":
                    checks = ['["pnpm","install","--frozen-lockfile"]',
                              '["pnpm","typecheck"]', '["pnpm","test"]']
                    verify_cmd = [sys.executable, str(loop), "verify", "--repo", str(args.repo),
                                  "--case", str(args.case), "--proposal", str(proposal),
                                  "--scanner", str(args.scanner), "--timeout", str(args.timeout),
                                  "--out", str(verification)]
                    for check in checks:
                        verify_cmd.extend(("--check", check))
                    result["stages"]["verify"] = run_stage(
                        verify_cmd, stage_env(MODELS["propose"], tokens["propose"],
                                              args.model_port, *trace_meta["propose"],
                                              case["caseId"]), verification)
                if result["stages"].get("verify", {}).get("status") == "verified":
                    review_cmd = [sys.executable, str(loop), "review", "--proposal", str(proposal),
                                  "--verification", str(verification), "--goose", str(args.goose),
                                  "--timeout", str(args.timeout), "--out", str(review)]
                    result["stages"]["review"] = run_stage(
                        review_cmd, stage_env(MODELS["review"], tokens["review"],
                                              args.model_port, *trace_meta["review"],
                                              case["caseId"]), review)
                time.sleep(2)  # Allow the local asynchronous OTLP exporter to flush.
                stop_group(gateway)
            result["checks"]["gatewayModelRequests"] = gateway_counts(log_file)
            with receiver.lock:
                spans = list(receiver.spans)
                result["checks"]["otlpHttpRequests"] = receiver.requests
                result["checks"]["otlpParseErrors"] = receiver.parse_errors
            result["checks"]["anyKnownContentAttributeNameSeen"] = any(
                span["knownContentAttributeNameSeen"] for span in spans)
            for stage in MODELS:
                if stage in result["stages"]:
                    result["traces"][stage] = stage_trace(
                        spans, *trace_meta[stage], MODELS[stage])
            counts = result["checks"]["gatewayModelRequests"]
            result["trialPassed"] = all((
                result["stages"].get("propose", {}).get("status") == "proposed",
                result["stages"].get("propose", {}).get("producer") == "goose",
                result["stages"].get("verify", {}).get("status") == "verified",
                result["stages"].get("review", {}).get("status") == "reviewed",
                counts["propose"]["successes"] > 0, counts["review"]["successes"] > 0,
                all(result["traces"].get(stage, {}).get("parentLinked")
                    and result["traces"][stage]["expectedModelObserved"]
                    and not result["traces"][stage]["knownContentAttributeNameSeen"]
                    for stage in MODELS),
                receiver.requests > 0, receiver.parse_errors == 0,
                not result["checks"]["anyKnownContentAttributeNameSeen"],
            ))
    finally:
        stop_group(gateway)
        if receiver:
            receiver.shutdown()
            receiver.server_close()
    return finish(args.output, result)


def finish(path: Path, result: dict) -> int:
    result["completedAtUtc"] = now_utc()
    write_private(path, json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"trialPassed": result.get("trialPassed", False),
                      "authorizationChecksPassed": result.get("authorizationChecksPassed", False),
                      "result": str(path)}))
    return 0 if result.get("trialPassed") or result.get("authorizationChecksPassed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
