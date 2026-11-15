#!/usr/bin/env python3
"""Exercise the local registry with pinned fixture or explicit source files.

The default fixture is self-contained and not a runnable Agent. Optional source
files are copied into an isolated Git repository; neither Agent is started.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from registry import AgentRegistry


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_SOURCES = {
    "goose": HERE / "fixtures/goose-probe.txt",
    "maf": HERE / "fixtures/maf-probe.txt",
}


def git(checkout: Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(checkout), *arguments],
                            capture_output=True, text=True, check=True, timeout=10)
    return result.stdout.strip()


def manifest(family: str, revision: str) -> dict:
    names = {"goose": "goose-gateway-probe", "maf": "maf-scrum-probe"}
    return {
        "apiVersion": "tech-playground.dev/agent/v0",
        "kind": "AgentDefinition",
        "metadata": {
            "agentId": names[family],
            "useCaseId": "registry-admission-trial",
            "version": "0.1.0",
            "ownerRef": "team/security",
        },
        "spec": {
            "purpose": f"{family}方式の隔離した登録・停止の試行用artifact",
            "implementation": {
                "family": family,
                "repository": "trial/local-agent-artifacts",
                "revision": revision,
                "path": f"agents/{family}",
            },
            "requestedCapabilities": {
                "modelRoutes": ["trial-model"],
                "tools": ["trial.read"],
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        help="new isolated output directory; a timestamped path is used by default")
    parser.add_argument("--goose-source", type=Path,
                        help="optional existing Goose source file to pin; no Agent is run")
    parser.add_argument("--maf-source", type=Path,
                        help="optional existing MAF source file to pin; no workflow is run")
    args = parser.parse_args()
    sources = {"goose": args.goose_source or DEFAULT_SOURCES["goose"],
               "maf": args.maf_source or DEFAULT_SOURCES["maf"]}
    for family, source in sources.items():
        if not source.is_file():
            parser.error(f"{family} source file does not exist: {source}")
    output = (args.output_dir or ROOT / ".artifacts" /
              ("agent-registry-trial-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))).resolve()
    if output.exists():
        parser.error("output directory already exists; choose a new isolated directory")
    output.mkdir(parents=True)
    source_repo = output / "source-repo"
    source_repo.mkdir()
    git(source_repo, "init", "-q")
    source_hashes = {}
    for family, source in sources.items():
        destination = source_repo / "agents" / family
        destination.mkdir(parents=True)
        copied = destination / source.name
        shutil.copy2(source, copied)
        source_hashes[family] = hashlib.sha256(copied.read_bytes()).hexdigest()
    git(source_repo, "add", "agents")
    git(source_repo, "-c", "user.name=Tech Playground Trial",
        "-c", "user.email=trial@example.invalid", "commit", "-qm", "Pin local Agent trial artifacts")
    revision = git(source_repo, "rev-parse", "HEAD")

    manifests = output / "manifests"
    manifests.mkdir()
    registry = AgentRegistry(output / "registry.sqlite3",
                             repository_name="trial/local-agent-artifacts", checkout=source_repo)
    definitions = []
    decisions = []
    for family in sources:
        path = manifests / f"{family}.agent.yaml"
        path.write_text(yaml.safe_dump(manifest(family, revision), allow_unicode=True,
                                       sort_keys=False), encoding="utf-8")
        registered = registry.register(path)
        agent_id = registered["agent_id"]
        registry.change_definition_state(agent_id, "0.1.0", "tested",
                                         actor_ref="trial/operator-unverified",
                                         reason="local schema and Git artifact check")
        registry.change_definition_state(agent_id, "0.1.0", "approved",
                                         actor_ref="trial/operator-unverified",
                                         reason="local gate trial only; no human or IdP approval")
        deployment_id = f"trial-{family}"
        registry.create_binding(deployment_id, agent_id, "0.1.0",
                                environment="isolated", adapter=family,
                                principal_ref="trial/requester-unverified",
                                allowed_models=["trial-model"],
                                allowed_tools=["trial.read"])
        registry.change_binding_state(deployment_id, "active",
                                      actor_ref="trial/operator-unverified",
                                      reason="local admission trial")
        definitions.append({"agentId": agent_id, "family": family,
                            "manifestDigest": registered["manifest_digest"],
                            "artifactOid": registered["artifact_oid"],
                            "revision": registered["revision"]})
        decisions.append(registry.admit_request(
            deployment_id, requester_ref="trial/requester-unverified",
            model_route="trial-model", tools=["trial.read"]))
    decisions.append(registry.admit_request(
        "trial-goose", requester_ref="trial/requester-unverified",
        model_route="trial-model", tools=["trial.write"]))
    decisions.append(registry.admit_request(
        "trial-goose", requester_ref="trial/another-requester",
        model_route="trial-model", tools=["trial.read"]))
    registry.change_binding_state("trial-goose", "suspended",
                                  actor_ref="trial/operator-unverified",
                                  reason="local suspension trial")
    decisions.append(registry.admit_request(
        "trial-goose", requester_ref="trial/requester-unverified",
        model_route="trial-model", tools=["trial.read"]))

    registry.change_binding_state("trial-goose", "retired",
                                  actor_ref="trial/operator-unverified",
                                  reason="local irreversible revocation trial")
    decisions.append(registry.admit_request(
        "trial-goose", requester_ref="trial/requester-unverified",
        model_route="trial-model", tools=["trial.read"]))

    expected = [
        ("allowed", "allowed"), ("allowed", "allowed"),
        ("denied", "tool_not_allowed"), ("denied", "requester_not_assigned"),
        ("denied", "deployment_not_active"), ("denied", "deployment_not_active"),
    ]
    observed = [(entry["decision"], entry["reason"]) for entry in decisions]
    if observed != expected:
        raise RuntimeError(f"admission trial failed: {observed!r}")

    reopened = AgentRegistry(output / "registry.sqlite3",
                             repository_name="trial/local-agent-artifacts", checkout=source_repo)
    if (reopened.binding("trial-goose")["state"] != "retired" or
            reopened.binding("trial-maf")["state"] != "active" or
            len(reopened.admissions()) != len(expected)):
        raise RuntimeError("registry readback failed")
    result = {
        "checkedAtUtc": datetime.now(timezone.utc).isoformat(),
        "scope": "isolated_local_registry_and_admission_preflight",
        "sourceKind": {
            "goose": "explicit_source_copy" if args.goose_source else "non_runnable_local_fixture",
            "maf": "explicit_source_copy" if args.maf_source else "non_runnable_local_fixture",
        },
        "capabilityKind": "synthetic_labels_for_local_admission_preflight",
        "sourceSha256": source_hashes,
        "definitions": definitions,
        "decisions": [{key: decision[key] for key in (
            "deployment_id", "decision", "reason", "agent_id", "version",
            "manifest_digest", "revision", "artifact_oid")}
                      for decision in decisions],
        "readbackAfterReopen": {
            "catalog": [(row["agent_id"], row["state"]) for row in
                        reopened.list_definitions(use_case_id="registry-admission-trial")],
            "definitions": [reopened.definition(d["agentId"], "0.1.0")["state"]
                            for d in definitions],
            "gooseBinding": reopened.binding("trial-goose")["state"],
            "mafBinding": reopened.binding("trial-maf")["state"],
            "admissionCount": len(reopened.admissions()),
            "stateEventCount": len(reopened.state_events()),
        },
        "limitations": {
            "agentProcessStarted": False,
            "toolCallExecuted": False,
            "requesterIdentityVerified": False,
            "operatorIdentityVerified": False,
            "runtimeToolEnforcementConnected": False,
            "runningAgentTerminationVerified": False,
            "sharedRunApiImplemented": False,
        },
    }
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    print(json.dumps({"result": str(output / "result.json"),
                      "allowed": sum(d["decision"] == "allowed" for d in decisions),
                      "denied": sum(d["decision"] == "denied" for d in decisions)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
