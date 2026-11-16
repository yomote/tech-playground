import importlib.util
import json
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location("impact_gate", Path(__file__).with_name("impact_gate.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

CASE = "BT-" + "a" * 16
CASE_SHA = "b" * 64
HEAD = "c" * 40


def comment(*, login="human-reviewer", head=HEAD, impact="confirmed",
            decision="fix_required", edited=False, trailing_newline=False):
    body = gate.MARKER + json.dumps({
        "caseId": CASE,
        "caseSha256": CASE_SHA,
        "prHeadSha": head,
        "impact": impact,
        "decision": decision,
        "evidenceUrl": "https://github.com/yomote/tech-playground/issues/42#issuecomment-123",
        "rationale": "The deployed entrypoint uses the affected parser and is reachable.",
    })
    return {"id": 123, "body": body + ("\n" if trailing_newline else ""),
            "html_url": "https://github.com/example/123",
            "user": {"login": login, "type": "User"},
            "created_at": "2026-11-16T07:20:00Z",
            "updated_at": "2026-11-16T07:21:00Z" if edited else "2026-11-16T07:20:00Z"}


def verify(rows):
    return gate.check_comments(rows, case_id=CASE, case_sha256=CASE_SHA,
                               head_sha=HEAD, assessor="human-reviewer",
                               pr_created_at="2026-11-16T07:15:00Z")


class ImpactGateTests(unittest.TestCase):
    def test_accepts_only_named_human_for_current_case_and_head(self):
        result = verify([comment(login="goose-bot"), comment(head="d" * 40), comment()])
        self.assertEqual(result["commentId"], 123)
        self.assertEqual(len(result["bodySha256"]), 64)
        self.assertEqual(verify([comment(trailing_newline=True)])["commentId"], 123)

    def test_missing_or_ambiguous_human_decision_stops_merge(self):
        for rows in ([], [comment(login="goose-bot")], [comment(), comment()]):
            with self.subTest(rows=rows), self.assertRaises(gate.GateError):
                verify(rows)

    def test_old_head_rejection_and_edited_decision_stops_merge(self):
        for rows in ([comment(head="d" * 40)], [comment(edited=True)],
                     [comment(impact="unknown")], [comment(decision="no_fix")]):
            with self.subTest(rows=rows), self.assertRaises(gate.GateError):
                verify(rows)

    def test_environment_requires_exact_named_user_and_readable_review_rule(self):
        env = {"name": "blue-security-impact", "protection_rules": [{
            "type": "required_reviewers", "prevent_self_review": True,
            "reviewers": [{"type": "User", "reviewer": {"login": "human-reviewer"}}],
        }]}
        gate.check_environment(env, "human-reviewer")
        env["protection_rules"][0]["prevent_self_review"] = False
        gate.check_environment(env, "human-reviewer")
        for invalid in ({"name": "blue-security-impact", "protection_rules": []},
                        {"name": "blue-security-impact", "protection_rules": [{
                            "type": "required_reviewers",
                            "reviewers": [{"type": "User", "reviewer": {"login": "human-reviewer"}}],
                        }]},
                        {"name": "blue-security-impact", "protection_rules": [{
                            "type": "required_reviewers", "prevent_self_review": True,
                            "reviewers": [{"type": "User", "reviewer": {"login": "goose-bot"}}],
                        }]}):
            with self.subTest(invalid=invalid), self.assertRaises(gate.GateError):
                gate.check_environment(invalid, "human-reviewer")

    def test_comment_pagination_cannot_switch_api_destination(self):
        original = gate.github_get
        try:
            gate.github_get = lambda path, token, include_link=False: (
                [comment()], '<https://evil.example/comments?page=2>; rel="next"')
            with self.assertRaises(gate.GateError):
                gate.all_comments("yomote/tech-playground", 42, "read-token")
        finally:
            gate.github_get = original


if __name__ == "__main__":
    unittest.main()
