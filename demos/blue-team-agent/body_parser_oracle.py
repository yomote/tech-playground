#!/usr/bin/env python3
"""Compare the local OpenFGA demo HTTP boundary before and after a dependency fix.

The result is a reproducible local observation, not an impact assessment, PR
review, or merge decision. Both checkouts must already have frozen dependencies
installed. The script never contacts OpenFGA or GitHub and writes no repository
files; all output belongs under a new, ignored .artifacts directory.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request


PORT = 5176  # demos/openfga-sharing-playground/server.ts binds this fixed port.
QUERY = {"user": "user:alice", "relation": "viewer", "object": "document:1"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True,
                            capture_output=True, text=True, timeout=15)
    return result.stdout.strip()


def installed_versions(demo: Path, node: Path) -> dict[str, str]:
    script = """
const fs=require('node:fs'), path=require('node:path');
const version=p=>{let d=path.dirname(p);while(!fs.existsSync(path.join(d,'package.json'))) d=path.dirname(d);return JSON.parse(fs.readFileSync(path.join(d,'package.json'))).version};
const express=require.resolve('express',{paths:[process.cwd()]});
const parser=require.resolve('body-parser',{paths:[path.dirname(express)]});
console.log(JSON.stringify({express:version(express),bodyParser:version(parser)}));
"""
    result = subprocess.run([str(node), "-e", script], cwd=demo, check=True,
                            capture_output=True, text=True, timeout=15)
    return json.loads(result.stdout)


def state() -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/state", timeout=4) as response:
        return {"http": response.status, "body": json.loads(response.read())}


def post(payload: bytes, *, compressed: bool = False) -> dict:
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=4)
    headers = {"Content-Type": "application/json"}
    if compressed:
        headers["Content-Encoding"] = "gzip"
    started = time.monotonic()
    try:
        connection.request("POST", "/api/check", payload, headers)
        response = connection.getresponse()
        body = json.loads(response.read())
        return {"http": response.status, "mode": body.get("mode"),
                "allowed": body.get("allowed"),
                "elapsedMs": round((time.monotonic() - started) * 1000, 2)}
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return {"transportError": type(exc).__name__,
                "elapsedMs": round((time.monotonic() - started) * 1000, 2)}
    finally:
        connection.close()


def run_variant(repo: Path, label: str, output: Path, node: Path,
                plain: bytes, valid_gzip: bytes, malformed_gzip: bytes) -> dict:
    if git(repo, "status", "--porcelain=v1", "--untracked-files=normal"):
        raise ValueError(f"{label}: checkout is dirty")
    demo = repo / "demos/openfga-sharing-playground"
    tsx = repo / "node_modules/.bin/tsx"
    if not tsx.exists():
        raise ValueError(f"{label}: frozen dependencies are not installed")
    result = {"head": git(repo, "rev-parse", "HEAD"),
              "sourceSha256": sha256(demo / "server.ts"),
              "lockfileSha256": sha256(repo / "pnpm-lock.yaml"),
              "installed": installed_versions(demo, node)}
    env = {"HOME": str(Path.home()),
           "PATH": str(node.parent) + ":/usr/local/bin:/usr/bin:/bin"}
    log_path = output / f"{label}-server.log"
    with os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as log:
        process = subprocess.Popen([str(tsx), "server.ts"], cwd=demo, env=env,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError(f"{label}: server exited before readiness")
            try:
                before = state()
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        else:
            raise RuntimeError(f"{label}: server readiness timed out")
        if before["body"].get("mode") != "mock":
            raise RuntimeError(f"{label}: OpenFGA demo is not in mock mode")
        result["normal"] = post(plain)
        result["normalGzip"] = post(valid_gzip, compressed=True)
        result["malformedGzip"] = post(malformed_gzip, compressed=True)
        deadline = time.monotonic() + 3
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        result["afterMalformed"] = {"alive": process.poll() is None,
                                    "exitCode": process.poll()}
        if process.poll() is None:
            result["normalAfterMalformed"] = post(plain)
            result["mockStateUnchanged"] = before == state()
        else:
            result["normalAfterMalformed"] = None
            result["mockStateUnchanged"] = None
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    result["serverLogSha256"] = sha256(log_path)
    result["uncaughtZDataError"] = "Z_DATA_ERROR" in log_path.read_text(errors="replace")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-repo", type=Path, required=True)
    parser.add_argument("--candidate-repo", type=Path, required=True)
    parser.add_argument("--baseline-head", required=True)
    parser.add_argument("--candidate-head", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--node", type=Path, default=Path(shutil.which("node") or
                        "/home/omote/.nvm/versions/node/v22.23.3/bin/node"))
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", PORT)) == 0:
            raise RuntimeError(f"port {PORT} is occupied; no server was started")
    plain = json.dumps(QUERY, separators=(",", ":")).encode()
    valid = gzip.compress(plain, mtime=0)
    malformed = valid + b"\x00" + b"X" * (100 * 1024)
    result = {"observedAtUtc": datetime.now(timezone.utc).isoformat(),
              "case": "GitHub_PR_16_body_parser",
              "scope": "local_HTTP_OpenFGA_mock_exact_checkouts",
              "input": {"normalSha256": hashlib.sha256(plain).hexdigest(),
                        "malformedSha256": hashlib.sha256(malformed).hexdigest(),
                        "malformedBytes": len(malformed)},
              "variants": {}, "acceptedScope": None,
              "notProven": ["deployed_impact", "real_OpenFGA_behavior", "formal_PR_review",
                            "merge", "post_merge_rescan"]}
    try:
        result["variants"]["baseline"] = run_variant(
            args.baseline_repo.resolve(), "baseline", output, args.node,
            plain, valid, malformed)
        result["variants"]["candidate"] = run_variant(
            args.candidate_repo.resolve(), "candidate", output, args.node,
            plain, valid, malformed)
        base, fixed = result["variants"]["baseline"], result["variants"]["candidate"]
        conditions = {
            "heads_match": base["head"] == args.baseline_head and fixed["head"] == args.candidate_head,
            "same_http_source": base["sourceSha256"] == fixed["sourceSha256"],
            "same_express_version": base["installed"]["express"] == fixed["installed"]["express"],
            "dependency_pair": base["installed"]["bodyParser"] == "2.3.0" and
                               fixed["installed"]["bodyParser"] == "2.4.0",
            "normal_requests": all(v[k].get("http") == 200 and v[k].get("mode") == "mock"
                                   and v[k].get("allowed") is True
                                   for v in (base, fixed) for k in ("normal", "normalGzip")),
            "malformed_reaches_route": all(v["malformedGzip"].get("http") == 200
                                           and v["malformedGzip"].get("mode") == "mock"
                                           for v in (base, fixed)),
            "baseline_crash": base["afterMalformed"]["exitCode"] == 1 and base["uncaughtZDataError"],
            "candidate_survives": fixed["afterMalformed"]["alive"] and
                                  (fixed["normalAfterMalformed"] or {}).get("http") == 200 and
                                  (fixed["normalAfterMalformed"] or {}).get("allowed") is True and
                                  fixed["mockStateUnchanged"] and not fixed["uncaughtZDataError"],
        }
        result["conditions"] = conditions
        if all(conditions.values()):
            result["acceptedScope"] = "same_local_HTTP_payload_crashes_baseline_only"
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)
    report = output / "result.json"
    with os.fdopen(os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                   "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"result": result["acceptedScope"], "report": str(report),
                      "conditions": result.get("conditions"), "error": result.get("error")},
                     ensure_ascii=False))
    return 0 if result["acceptedScope"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
