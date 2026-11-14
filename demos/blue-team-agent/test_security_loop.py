"""Contract and failure-boundary checks for the one-case Goose runner."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import security_loop as loop


RULE = "CVE-2026-111181"
GHSA = "GHSA-27r3-jxmh-p828"
FINDING = {
    "ruleId": RULE,
    "message": {"text": f"Package 'body-parser@2.3.0' is vulnerable to '{RULE}' (also known as '{GHSA}')."},
}
LOCKFILE = """lockfileVersion: '9.0'

settings:
  autoInstallPeers: true

importers:

  .:
    dependencies:
      express:
        version: 5.2.1

packages:

  body-parser@2.3.0:
    resolution: {integrity: sha512-old}

snapshots:

  body-parser@2.3.0:
    dependencies:
      bytes: 3.1.2

  express@5.2.1:
    dependencies:
      body-parser: 2.3.0
"""
FIXED_LOCKFILE = LOCKFILE.replace("body-parser@2.3.0", "body-parser@2.4.0").replace(
    "body-parser: 2.3.0", "body-parser: 2.4.0").replace(
    "importers:", "overrides:\n  body-parser: 2.4.0\n\nimporters:")


def put(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def command(*argv: str, cwd: Path) -> str:
    result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=True)
    return result.stdout.strip()


class SecurityLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        registry = patch.object(loop, "registry_version", return_value={
            "published": True, "url": "https://registry.npmjs.org/body-parser/2.4.0",
            "metadataSha256": "a" * 64,
        })
        registry.start()
        self.addCleanup(registry.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        command("git", "init", "-q", cwd=self.repo)
        command("git", "config", "user.name", "Security Test", cwd=self.repo)
        command("git", "config", "user.email", "security-test@example.invalid", cwd=self.repo)
        command("git", "remote", "add", "origin", "https://github.com/yomote/tech-playground.git", cwd=self.repo)
        (self.repo / "pnpm-lock.yaml").write_text(LOCKFILE, encoding="utf-8")
        (self.repo / "pnpm-workspace.yaml").write_text("packages: []\n", encoding="utf-8")
        command("git", "add", ".", cwd=self.repo)
        command("git", "commit", "-qm", "fixture", cwd=self.repo)
        self.head = command("git", "rev-parse", "HEAD", cwd=self.repo)
        self.sarif = self.root / "results.sarif"
        put(self.sarif, {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "osv-scanner"}},
                                                       "results": [FINDING]}]})
        self.evidence = self.root / "evidence.json"
        put(self.evidence, {
            "schemaVersion": 1, "commitSha": self.head,
            "scanner": {"version": "2.7.0", "sha256": loop.SCANNER_SHA256, "exitCode": 1},
            "input": {"path": "pnpm-lock.yaml", "sha256": loop.file_digest(self.repo / "pnpm-lock.yaml")},
            "output": {"path": ".artifacts/cve-watch/results.sarif",
                       "sha256": loop.file_digest(self.sarif), "sarifFindingRows": 1},
            "triageStatus": "unreviewed",
        })
        self.alerts = self.root / "alerts.json"
        put(self.alerts, {"complete": True, "repository": "yomote/tech-playground", "alerts": [{
            "number": 1, "state": "open",
            "dependency": {"package": {"ecosystem": "npm", "name": "body-parser"},
                           "manifest_path": "pnpm-lock.yaml"},
            "security_advisory": {"ghsa_id": GHSA, "cve_id": RULE},
            "security_vulnerability": {
                "package": {"ecosystem": "npm", "name": "body-parser"},
                "severity": "high", "first_patched_version": {"identifier": "2.4.0"}},
        }]})
        self.prs = self.root / "prs.json"
        put(self.prs, {"complete": True, "repository": "yomote/tech-playground", "pulls": []})

    def select(self, out: str = "case.json") -> tuple[int, dict]:
        path = self.root / out
        code = loop.main([
            "select", "--repo", str(self.repo), "--sarif", str(self.sarif),
            "--evidence", str(self.evidence), "--dependabot-alerts", str(self.alerts),
            "--open-prs", str(self.prs), "--out", str(path),
        ])
        return code, json.loads(path.read_text())

    def test_select_matching_osv_and_dependabot(self) -> None:
        code, result = self.select()
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["ruleId"], RULE)
        self.assertEqual(result["packageName"], "body-parser")
        self.assertEqual(result["fixedVersion"], "2.4.0")
        self.assertEqual(result["baseSha"], self.head)
        self.assertFalse(result["publishable"])

    def test_duplicate_open_pr_blocks_candidate(self) -> None:
        put(self.prs, {"complete": True, "repository": "yomote/tech-playground", "pulls": [
            {"number": 12, "state": "open", "title": f"fix: body-parser {GHSA}"},
        ]})
        code, result = self.select()
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"], "no_scoped_fixable_case")

    def test_missing_fixed_version_is_not_invented(self) -> None:
        alerts = json.loads(self.alerts.read_text())
        alerts["alerts"][0]["security_vulnerability"]["first_patched_version"] = None
        put(self.alerts, alerts)
        code, result = self.select()
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"], "no_fixed_version_or_unique_case")

    def test_unpublished_fixed_version_blocks_candidate(self) -> None:
        with patch.object(loop, "registry_version", return_value={"published": False}):
            code, result = self.select()
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"], "no_published_fixed_version")

    def test_unpublished_first_candidate_uses_next_published_one(self) -> None:
        sarif = json.loads(self.sarif.read_text())
        sarif["runs"][0]["results"].append({
            "ruleId": "CVE-2026-9999",
            "message": {"text": "Package 'other-package@1.0.0' is vulnerable to 'CVE-2026-9999' (also known as 'GHSA-abcd-efgh-ijkl')."},
        })
        put(self.sarif, sarif)
        evidence = json.loads(self.evidence.read_text())
        evidence["output"]["sha256"] = loop.file_digest(self.sarif)
        evidence["output"]["sarifFindingRows"] = 2
        put(self.evidence, evidence)
        alerts = json.loads(self.alerts.read_text())
        alerts["alerts"].append({
            "number": 2, "state": "open",
            "dependency": {"package": {"ecosystem": "npm", "name": "other-package"},
                           "manifest_path": "pnpm-lock.yaml"},
            "security_advisory": {"ghsa_id": "GHSA-abcd-efgh-ijkl", "cve_id": "CVE-2026-9999"},
            "security_vulnerability": {"package": {"ecosystem": "npm", "name": "other-package"},
                                       "severity": "medium", "first_patched_version": {"identifier": "1.0.1"}},
        })
        put(self.alerts, alerts)
        def registry(package: str, version: str) -> dict:
            return {"published": package == "other-package", "url": "https://registry.npmjs.org/" + package}
        with patch.object(loop, "registry_version", side_effect=registry):
            code, result = self.select()
        self.assertEqual(code, 0)
        self.assertEqual(result["packageName"], "other-package")

    def test_hash_mismatch_and_incomplete_pr_page_block(self) -> None:
        evidence = json.loads(self.evidence.read_text())
        evidence["input"]["sha256"] = "0" * 64
        put(self.evidence, evidence)
        code, result = self.select()
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"], "scan_lockfile_hash_mismatch")
        evidence["input"]["sha256"] = loop.file_digest(self.repo / "pnpm-lock.yaml")
        put(self.evidence, evidence)
        put(self.prs, {"complete": False, "repository": "yomote/tech-playground", "pulls": []})
        code, result = self.select("case2.json")
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"], "open_prs_incomplete")

    def test_repository_identity_mismatch_blocks_selection(self) -> None:
        put(self.prs, {"complete": True, "repository": "other/repository", "pulls": []})
        code, result = self.select()
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"], "open_prs_repository_mismatch")
        put(self.prs, {"complete": True, "repository": "yomote/tech-playground", "pulls": []})
        command("git", "remote", "set-url", "origin", "https://github.com/other/repository.git", cwd=self.repo)
        code, result = self.select("case2.json")
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"], "source_repository_mismatch")

    def test_process_allows_agent_state_file_but_limits_captured_output(self) -> None:
        state = self.root / "agent-state.bin"
        run = loop.process(
            ["python3", "-c", "from pathlib import Path; Path('agent-state.bin').write_bytes(b'x' * 2097152); print('ok')"],
            cwd=self.root, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")}, timeout=10,
        )
        self.assertEqual(run["exitCode"], 0)
        self.assertEqual(state.stat().st_size, 2097152)
        self.assertLess(run["stdoutBytes"], 100)

    def test_goose_override_command_preserves_existing_workspace(self) -> None:
        example = loop.override_commands("body-parser", "2.4.0")
        script = example.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        workspace = self.root / "pnpm-workspace.yaml"
        for before in (
            "packages:\n  - apps/*\nallowBuilds:\n  esbuild: true\n",
            "packages:\n  - apps/*\noverrides:\n  qs: 6.16.1\nallowBuilds:\n  esbuild: true\n",
        ):
            workspace.write_text(before, encoding="utf-8")
            subprocess.run(["python3", "-c", script], cwd=self.root, check=True, capture_output=True)
            after = workspace.read_text(encoding="utf-8")
            loop.check_workspace_change(before, after, "body-parser", "2.4.0")
            self.assertIn("  - apps/*", after)
            self.assertIn("  esbuild: true", after)

    def test_propose_verify_review_separate_processes(self) -> None:
        code, case = self.select()
        self.assertEqual(code, 0)
        goose = self.root / "fake-goose"
        goose.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "assert 'GITHUB_TOKEN' not in os.environ and 'GH_TOKEN' not in os.environ\n"
            "assert os.environ['HOME'] != str(pathlib.Path.cwd())\n"
            "if '--with-builtin' in sys.argv:\n"
            "  assert 'developer' in sys.argv\n"
            "  assert os.environ['GOOSE_MODE'] == 'auto'\n"
            "  pathlib.Path('pnpm-workspace.yaml').write_text('packages: []\\noverrides:\\n  body-parser: 2.4.0\\n')\n"
            "  p=pathlib.Path('pnpm-lock.yaml')\n"
            "  s=p.read_text().replace('body-parser@2.3.0', 'body-parser@2.4.0').replace('body-parser: 2.3.0', 'body-parser: 2.4.0')\n"
            "  p.write_text(s.replace('importers:', 'overrides:\\n  body-parser: 2.4.0\\n\\nimporters:'))\n"
            "else:\n"
            "  assert 'GOOSE_MODE' not in os.environ\n"
            "  print(json.dumps({'verdict':'APPROVE','reason':'Scoped dependency change; checks passed.'}))\n",
            encoding="utf-8",
        )
        goose.chmod(0o700)
        scanner = self.root / "fake-scanner"
        scanner.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "assert 'OPENAI_API_KEY' not in os.environ and 'GITHUB_TOKEN' not in os.environ\n"
            "p=pathlib.Path(sys.argv[sys.argv.index('--output-file')+1])\n"
            "p.write_text(json.dumps({'version':'2.1.0','runs':[{'tool':{'driver':{'name':'osv-scanner'}},'results':[]}]}))\n",
            encoding="utf-8",
        )
        scanner.chmod(0o700)
        proposal_file = self.root / "proposal.json"
        verify_file = self.root / "verification.json"
        review_file = self.root / "review.json"
        with patch.object(loop, "GOOSE_SHA256", loop.file_digest(goose)), patch.object(
            loop, "SCANNER_SHA256", loop.file_digest(scanner)
        ), patch.object(loop, "regenerate_lockfile", return_value=FIXED_LOCKFILE
        ), patch.dict(os.environ, {"GITHUB_TOKEN": "must-not-leak", "GH_TOKEN": "must-not-leak",
                                   "OPENAI_API_KEY": "model-secret"}):
            code = loop.main(["propose", "--repo", str(self.repo), "--case", str(self.root / "case.json"),
                              "--goose", str(goose), "--out", str(proposal_file)])
            self.assertEqual(code, 0)
            proposal = json.loads(proposal_file.read_text())
            self.assertEqual(proposal["changedPaths"], ["pnpm-lock.yaml", "pnpm-workspace.yaml"])
            self.assertEqual(proposal["producer"], "goose")
            self.assertFalse(proposal["publishable"])
            code = loop.main([
                "verify", "--repo", str(self.repo), "--case", str(self.root / "case.json"),
                "--proposal", str(proposal_file), "--scanner", str(scanner),
                "--check", '["python3","-c","import os; assert \\"OPENAI_API_KEY\\" not in os.environ"]',
                "--out", str(verify_file),
            ])
            self.assertEqual(code, 0)
            verification = json.loads(verify_file.read_text())
            self.assertEqual(verification["rescan"]["targetFindingRows"], 0)
            code = loop.main([
                "review", "--proposal", str(proposal_file), "--verification", str(verify_file),
                "--goose", str(goose), "--out", str(review_file),
            ])
            self.assertEqual(code, 0)
            reviewed = json.loads(review_file.read_text())
            self.assertEqual(reviewed["reviewer"]["kind"], "separate_read_only_goose")
            self.assertFalse(reviewed["publishable"])
        self.assertEqual(command("git", "status", "--porcelain", cwd=self.repo), "")
        self.assertEqual(command("git", "worktree", "list", "--porcelain", cwd=self.repo).count("worktree "), 1)

    def test_rejected_goose_patch_uses_clean_deterministic_fallback(self) -> None:
        code, _ = self.select()
        self.assertEqual(code, 0)
        goose = self.root / "bad-goose"
        goose.write_text(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "Path('pnpm-workspace.yaml').write_text('packages: []\\noverrides:\\n  body-parser: 2.4.0\\n')\n"
            "Path('pnpm-lock.yaml').write_text('lockfileVersion: 9\\n')\n",
            encoding="utf-8",
        )
        goose.chmod(0o700)
        proposal_file = self.root / "fallback-proposal.json"
        with patch.object(loop, "GOOSE_SHA256", loop.file_digest(goose)), patch.object(
            loop, "regenerate_lockfile", return_value=FIXED_LOCKFILE
        ):
            code = loop.main(["propose", "--repo", str(self.repo), "--case", str(self.root / "case.json"),
                              "--goose", str(goose), "--out", str(proposal_file)])
        self.assertEqual(code, 0)
        proposal = json.loads(proposal_file.read_text())
        self.assertEqual(proposal["producer"], "deterministic_fallback_after_goose_rejection")
        self.assertEqual(proposal["gooseCandidateRejection"], "lockfile_sections_changed")
        patch_text = Path(proposal["patchPath"]).read_text()
        self.assertIn("body-parser@2.4.0", patch_text)
        self.assertNotIn("lockfileVersion: 9\n", patch_text)
        self.assertEqual(command("git", "worktree", "list", "--porcelain", cwd=self.repo).count("worktree "), 1)

    def test_empty_goose_uses_fallback_but_out_of_scope_change_blocks(self) -> None:
        code, _ = self.select()
        self.assertEqual(code, 0)
        goose = self.root / "empty-goose"
        goose.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        goose.chmod(0o700)
        with patch.object(loop, "GOOSE_SHA256", loop.file_digest(goose)), patch.object(
            loop, "regenerate_lockfile", return_value=FIXED_LOCKFILE
        ):
            code = loop.main(["propose", "--repo", str(self.repo), "--case", str(self.root / "case.json"),
                              "--goose", str(goose), "--out", str(self.root / "empty-proposal.json")])
        self.assertEqual(code, 0)
        result = json.loads((self.root / "empty-proposal.json").read_text())
        self.assertEqual(result["gooseCandidateRejection"], "goose_candidate_empty")
        goose.write_text("#!/usr/bin/env python3\nfrom pathlib import Path\nPath('outside.txt').write_text('x')\n",
                         encoding="utf-8")
        with patch.object(loop, "GOOSE_SHA256", loop.file_digest(goose)):
            code = loop.main(["propose", "--repo", str(self.repo), "--case", str(self.root / "case.json"),
                              "--goose", str(goose), "--out", str(self.root / "outside-proposal.json")])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads((self.root / "outside-proposal.json").read_text())["reason"],
                         "untracked_candidate_files")

    def test_failed_goose_process_never_uses_fallback(self) -> None:
        code, _ = self.select()
        self.assertEqual(code, 0)
        goose = self.root / "failed-goose"
        goose.write_text("#!/usr/bin/env python3\nraise SystemExit(7)\n", encoding="utf-8")
        goose.chmod(0o700)
        with patch.object(loop, "GOOSE_SHA256", loop.file_digest(goose)), patch.object(
            loop, "regenerate_lockfile", side_effect=AssertionError("fallback must not run")
        ):
            code = loop.main(["propose", "--repo", str(self.repo), "--case", str(self.root / "case.json"),
                              "--goose", str(goose), "--out", str(self.root / "failed-proposal.json")])
        self.assertEqual(code, 2)
        result = json.loads((self.root / "failed-proposal.json").read_text())
        self.assertTrue(result["reason"].startswith("goose_proposal_failed:exit=7:"))

    def test_validate_committed_candidate_and_scope(self) -> None:
        code, case = self.select()
        self.assertEqual(code, 0)
        (self.repo / "pnpm-workspace.yaml").write_text(
            "packages: []\noverrides:\n  body-parser: 2.4.0\n", encoding="utf-8")
        (self.repo / "pnpm-lock.yaml").write_text(FIXED_LOCKFILE, encoding="utf-8")
        case_dir = self.repo / ".blue-team"
        case_dir.mkdir()
        put(case_dir / "security-case.json", case)
        command("git", "add", ".", cwd=self.repo)
        command("git", "commit", "-qm", "candidate", cwd=self.repo)
        output = self.root / "validation.json"
        with patch.object(loop, "regenerate_lockfile", return_value=FIXED_LOCKFILE):
            code = loop.main(["validate", "--repo", str(self.repo), "--case",
                              str(case_dir / "security-case.json"), "--out", str(output)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.read_text())["status"], "validated")
        (self.repo / "package.json").write_text('{"scripts":{"test":"true"}}', encoding="utf-8")
        command("git", "add", "package.json", cwd=self.repo)
        command("git", "commit", "-qm", "unrelated change", cwd=self.repo)
        output2 = self.root / "validation2.json"
        code = loop.main(["validate", "--repo", str(self.repo), "--case",
                          str(case_dir / "security-case.json"), "--out", str(output2)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output2.read_text())["reason"], "pr_changed_paths_out_of_scope")

    def test_structure_rejects_workspace_or_unrelated_lockfile_removal(self) -> None:
        safe_workspace = "packages: []\noverrides:\n  body-parser: 2.4.0\n"
        with self.assertRaisesRegex(loop.GateError, "workspace_existing_configuration_changed"):
            loop.check_workspace_change("packages: []\nallowBuilds:\n  esbuild: true\n", safe_workspace,
                                        "body-parser", "2.4.0")
        safe_lock = LOCKFILE.replace("body-parser@2.3.0", "body-parser@2.4.0").replace(
            "body-parser: 2.3.0", "body-parser: 2.4.0").replace(
            "importers:", "overrides:\n  body-parser: 2.4.0\n\nimporters:")
        with self.assertRaisesRegex(loop.GateError, "lockfile_importers_changed"):
            loop.check_lockfile_structure(LOCKFILE, safe_lock.replace("  .:\n", ""),
                                          "body-parser", "2.4.0")
        with self.assertRaisesRegex(loop.GateError, "lockfile_differs_from_clean_pnpm_regeneration"):
            changed = safe_lock.replace("  body-parser@2.4.0:",
                                        "  unrelated@1.0.0:\n    resolution: {integrity: sha512-other}\n\n  body-parser@2.4.0:", 1)
            loop.compare_canonical_lockfile(changed, safe_lock)


if __name__ == "__main__":
    unittest.main()
