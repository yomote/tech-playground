"""Focused tests for the local registry and its admission boundary."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from registry import AgentRegistry, RegistryError


def git(checkout: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(checkout), *args], capture_output=True, text=True,
        check=True, timeout=5,
    ).stdout.strip()


def definition(family: str, revision: str, *, path: str | None = None) -> dict:
    return {
        "apiVersion": "tech-playground.dev/agent/v0", "kind": "AgentDefinition",
        "metadata": {
            "agentId": f"{family}-trial", "version": "0.1.0", "ownerRef": "team/security",
            "useCaseId": "registration-trial",
        },
        "spec": {
            "purpose": "隔離した台帳と事前判定の試行",
            "implementation": {
                "family": family, "repository": "trial/source",
                "revision": revision, "path": path or f"agents/{family}",
            },
            "requestedCapabilities": {
                "modelRoutes": ["trial-model"], "tools": ["trial.read"],
            },
        },
    }


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkout = self.root / "source"
        self.checkout.mkdir()
        git(self.checkout, "init", "-q")
        for family in ("goose", "maf"):
            source = self.checkout / "agents" / family
            source.mkdir(parents=True)
            (source / "README.txt").write_text(f"{family} trial artifact\n", encoding="utf-8")
        git(self.checkout, "add", "agents")
        git(self.checkout, "-c", "user.name=Trial", "-c", "user.email=trial@example.invalid",
            "commit", "-qm", "Pin trial artifacts")
        self.revision = git(self.checkout, "rev-parse", "HEAD")
        self.db = self.root / "registry.sqlite3"
        self.registry = AgentRegistry(self.db, repository_name="trial/source", checkout=self.checkout)

    def manifest(self, family: str, *, revision: str | None = None,
                 path: str | None = None, document: dict | None = None) -> Path:
        doc = document or definition(family, revision or self.revision, path=path)
        destination = self.root / f"{family}.yaml"
        destination.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
        return destination

    def approve(self, family: str) -> str:
        name = f"{family}-trial"
        self.registry.register(self.manifest(family))
        self.registry.change_definition_state(name, "0.1.0", "tested",
                                              actor_ref="trial/operator-unverified", reason="local check")
        self.registry.change_definition_state(name, "0.1.0", "approved",
                                              actor_ref="trial/operator-unverified", reason="local trial")
        return name

    def test_both_families_persist_with_pinned_artifacts(self) -> None:
        for family in ("goose", "maf"):
            registered = self.registry.register(self.manifest(family))
            self.assertEqual("draft", registered["state"])
            self.assertEqual(self.revision, registered["revision"])
            self.assertEqual("tree", registered["artifact_type"])
        reopened = AgentRegistry(self.db, repository_name="trial/source", checkout=self.checkout)
        self.assertEqual("goose", reopened.definition("goose-trial", "0.1.0")["family"])
        self.assertEqual("maf", reopened.definition("maf-trial", "0.1.0")["family"])
        self.assertEqual(["goose-trial", "maf-trial"], [
            row["agent_id"] for row in reopened.list_definitions(use_case_id="registration-trial")
        ])
        self.assertEqual([], reopened.list_definitions(use_case_id="other-trial"))

    def test_immutable_version_and_missing_artifact_are_rejected(self) -> None:
        self.registry.register(self.manifest("goose"))
        changed = definition("goose", self.revision)
        changed["spec"]["purpose"] = "A different meaning for the same version"
        with self.assertRaisesRegex(RegistryError, "immutable"):
            self.registry.register(self.manifest("goose", document=changed))
        with self.assertRaises(RegistryError):
            self.registry.register(self.manifest("maf", revision="0" * 40))
        with self.assertRaises(RegistryError):
            self.registry.register(self.manifest("maf", path="agents/missing"))

    def test_symlink_artifact_is_rejected(self) -> None:
        link = self.checkout / "agents" / "linked"
        link.symlink_to("goose", target_is_directory=True)
        git(self.checkout, "add", "agents/linked")
        git(self.checkout, "-c", "user.name=Trial", "-c", "user.email=trial@example.invalid",
            "commit", "-qm", "Add symlink")
        bad = definition("goose", git(self.checkout, "rev-parse", "HEAD"), path="agents/linked")
        with self.assertRaises(RegistryError):
            self.registry.register(self.manifest("goose", document=bad))

    def test_binding_is_narrower_than_request_and_suspend_denies(self) -> None:
        self.registry.register(self.manifest("goose"))
        with self.assertRaisesRegex(RegistryError, "approved"):
            self.registry.create_binding("trial-goose", "goose-trial", "0.1.0",
                                         environment="trial", adapter="goose",
                                         principal_ref="trial/requester",
                                         allowed_models=["trial-model"], allowed_tools=["trial.read"])
        self.registry.change_definition_state("goose-trial", "0.1.0", "tested",
                                              actor_ref="trial/operator", reason="test")
        self.registry.change_definition_state("goose-trial", "0.1.0", "approved",
                                              actor_ref="trial/operator", reason="test")
        with self.assertRaisesRegex(RegistryError, "Tool permission"):
            self.registry.create_binding("too-broad", "goose-trial", "0.1.0",
                                         environment="trial", adapter="goose",
                                         principal_ref="trial/requester",
                                         allowed_models=["trial-model"], allowed_tools=["trial.write"])
        with self.assertRaisesRegex(RegistryError, "adapter"):
            self.registry.create_binding("wrong-adapter", "goose-trial", "0.1.0",
                                         environment="trial", adapter="maf",
                                         principal_ref="trial/requester",
                                         allowed_models=["trial-model"], allowed_tools=["trial.read"])
        self.registry.create_binding("trial-goose", "goose-trial", "0.1.0",
                                     environment="trial", adapter="goose",
                                     principal_ref="trial/requester",
                                     allowed_models=["trial-model"], allowed_tools=["trial.read"])
        self.assertEqual("denied", self.registry.admit_request(
            "trial-goose", requester_ref="trial/requester", model_route="trial-model",
            tools=["trial.read"])["decision"])
        self.registry.change_binding_state("trial-goose", "active",
                                           actor_ref="trial/operator", reason="test")
        self.assertEqual("allowed", self.registry.admit_request(
            "trial-goose", requester_ref="trial/requester", model_route="trial-model",
            tools=["trial.read"])["decision"])
        self.assertEqual("tool_not_allowed", self.registry.admit_request(
            "trial-goose", requester_ref="trial/requester", model_route="trial-model",
            tools=["trial.write"])["reason"])
        self.assertEqual("requester_not_assigned", self.registry.admit_request(
            "trial-goose", requester_ref="trial/other", model_route="trial-model",
            tools=["trial.read"])["reason"])
        self.registry.change_binding_state("trial-goose", "suspended",
                                           actor_ref="trial/operator", reason="stop")
        reopened = AgentRegistry(self.db, repository_name="trial/source", checkout=self.checkout)
        self.assertEqual("deployment_not_active", reopened.admit_request(
            "trial-goose", requester_ref="trial/requester", model_route="trial-model",
            tools=["trial.read"])["reason"])
        reopened.change_binding_state("trial-goose", "retired",
                                      actor_ref="trial/operator", reason="revoke")
        self.assertEqual("deployment_not_active", reopened.admit_request(
            "trial-goose", requester_ref="trial/requester", model_route="trial-model",
            tools=["trial.read"])["reason"])
        with self.assertRaisesRegex(RegistryError, "invalid deployment"):
            reopened.change_binding_state("trial-goose", "active",
                                          actor_ref="trial/operator", reason="reopen")
        self.assertEqual(6, len(reopened.admissions()))


if __name__ == "__main__":
    unittest.main()
