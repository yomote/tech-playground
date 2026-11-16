#!/usr/bin/env python3
"""Require a named person's case- and head-bound impact decision before merge."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


MARKER = "BLUE_IMPACT_V1\n"
FIELDS = {"caseId", "caseSha256", "prHeadSha", "impact", "decision", "evidenceUrl", "rationale"}
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
CASE_ID = re.compile(r"BT-[0-9a-f]{16}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")


class GateError(ValueError):
    pass


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise GateError(f"duplicate impact field: {key}")
        result[key] = value
    return result


def check_environment(environment: object, assessor: str) -> None:
    if not isinstance(environment, dict) or environment.get("name") != "blue-security-impact":
        raise GateError("impact environment missing")
    rules = environment.get("protection_rules")
    if not isinstance(rules, list):
        raise GateError("impact environment protection is unreadable")
    reviewers = [rule for rule in rules if isinstance(rule, dict) and rule.get("type") == "required_reviewers"]
    if (len(reviewers) != 1 or
        not isinstance(reviewers[0].get("prevent_self_review"), bool)):
        raise GateError("impact environment needs one readable reviewer rule")
    actors = reviewers[0].get("reviewers")
    if (not isinstance(actors, list) or len(actors) != 1 or
        actors[0].get("type") != "User" or
        actors[0].get("reviewer", {}).get("login", "").lower() != assessor.lower()):
        raise GateError("impact environment reviewer differs from the named assessor")


def check_comments(comments: object, *, case_id: str, case_sha256: str,
                   head_sha: str, assessor: str, pr_created_at: str) -> dict[str, object]:
    if not isinstance(comments, list):
        raise GateError("impact comments are incomplete")
    matches = []
    for comment in comments:
        if not isinstance(comment, dict):
            raise GateError("invalid impact comment")
        user = comment.get("user")
        if not isinstance(user, dict) or user.get("login", "").lower() != assessor.lower():
            continue
        if user.get("type") != "User":
            raise GateError("impact assessor is not a GitHub user")
        body = comment.get("body")
        if not isinstance(body, str) or not body.startswith(MARKER):
            continue
        # A pasted PR template may have one trailing newline. No other extra
        # lines or Markdown fences are part of the signed decision body.
        if (len(body) > 8192 or body.count("\n") not in (1, 2) or
            (body.count("\n") == 2 and not body.endswith("\n"))):
            raise GateError("impact comment format is invalid")
        try:
            payload = json.loads(body[len(MARKER):], object_pairs_hook=unique_object)
        except (json.JSONDecodeError, GateError) as exc:
            raise GateError("impact comment JSON is invalid") from exc
        if not isinstance(payload, dict) or set(payload) != FIELDS:
            raise GateError("impact comment fields are invalid")
        # Earlier heads remain in the PR discussion. Only this exact case and
        # commit can authorize the current merge.
        if (payload["caseId"], payload["caseSha256"], payload["prHeadSha"]) != (
            case_id, case_sha256, head_sha):
            continue
        if payload["impact"] not in ("confirmed", "potential") or payload["decision"] != "fix_required":
            raise GateError("human impact decision does not authorize this fix")
        evidence = payload["evidenceUrl"]
        parsed = urlsplit(evidence) if isinstance(evidence, str) else None
        if (not parsed or parsed.scheme != "https" or not parsed.hostname or
            parsed.username or parsed.password or not parsed.path or parsed.path == "/"):
            raise GateError("impact evidence URL is absent or invalid")
        rationale = payload["rationale"]
        if not isinstance(rationale, str) or len(rationale.strip()) < 24:
            raise GateError("human impact rationale is missing")
        created, updated = comment.get("created_at"), comment.get("updated_at")
        if not isinstance(created, str) or created < pr_created_at or updated != created:
            raise GateError("impact comment predates the PR or was edited")
        ident, link = comment.get("id"), comment.get("html_url")
        if not isinstance(ident, int) or ident <= 0 or not isinstance(link, str):
            raise GateError("impact comment GitHub identity is invalid")
        matches.append({"commentId": ident, "commentUrl": link,
                        "bodySha256": hashlib.sha256(body.encode()).hexdigest()})
    if len(matches) != 1:
        raise GateError("exactly one current-head human impact decision is required")
    return matches[0]


def github_get(path: str, token: str, *, include_link: bool = False) -> object:
    request = urllib.request.Request("https://api.github.com/" + path, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise GateError(f"GitHub API status {response.status}")
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise GateError("GitHub API response is too large")
        payload = json.loads(raw)
        if include_link:
            return payload, response.headers.get("Link", "")
        return payload


def all_comments(repo: str, pr: int, token: str) -> list[object]:
    comments = []
    for page in range(1, 101):
        rows, links = github_get(
            f"repos/{repo}/issues/{pr}/comments?per_page=100&page={page}",
            token, include_link=True)
        if not isinstance(rows, list) or len(rows) > 100:
            raise GateError("GitHub comment page is invalid")
        comments.extend(rows)
        next_urls = re.findall(r'<([^>]+)>;\s*rel="next"', links)
        if not next_urls:
            return comments
        if len(next_urls) != 1:
            raise GateError("GitHub comment pagination is ambiguous")
        next_page = urlsplit(next_urls[0])
        if (next_page.scheme != "https" or next_page.netloc != "api.github.com" or
            next_page.path != f"/repos/{repo}/issues/{pr}/comments" or
            parse_qs(next_page.query) != {"per_page": ["100"], "page": [str(page + 1)]}):
            raise GateError("GitHub comment pagination changed destination")
    raise GateError("GitHub comment pagination exceeds limit")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--case-sha256", required=True)
    parser.add_argument("--pr-head", required=True)
    parser.add_argument("--assessor-login", required=True)
    parser.add_argument("--bot-login", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expect", type=Path)
    args = parser.parse_args()
    assessor = args.assessor_login
    if (args.repository != "yomote/tech-playground" or args.pr <= 0 or
        not CASE_ID.fullmatch(args.case_id) or not SHA256.fullmatch(args.case_sha256) or
        not SHA.fullmatch(args.pr_head) or not LOGIN.fullmatch(assessor) or
        assessor.lower() == args.bot_login.lower() or assessor.lower().endswith("[bot]")):
        raise GateError("impact gate identifiers or assessor configuration are invalid")
    read_token, policy_token, reviewer_token = (
        os.environ.get("GH_TOKEN", ""), os.environ.get("POLICY_TOKEN", ""),
        os.environ.get("REVIEWER_TOKEN", ""))
    if not read_token or not policy_token or not reviewer_token:
        raise GateError("impact gate credentials are unavailable")
    reviewer = github_get("user", reviewer_token)
    if (not isinstance(reviewer, dict) or not reviewer.get("login") or
        reviewer["login"].lower() == assessor.lower()):
        raise GateError("impact assessor must differ from the automated reviewer")
    environment = github_get(f"repos/{args.repository}/environments/blue-security-impact", policy_token)
    check_environment(environment, assessor)
    pr = github_get(f"repos/{args.repository}/pulls/{args.pr}", read_token)
    if (not isinstance(pr, dict) or pr.get("state") != "open" or
        pr.get("head", {}).get("sha") != args.pr_head or
        pr.get("base", {}).get("ref") != "main" or
        not isinstance(pr.get("created_at"), str)):
        raise GateError("PR head, base, or state changed")
    evidence = check_comments(all_comments(args.repository, args.pr, read_token),
                              case_id=args.case_id, case_sha256=args.case_sha256,
                              head_sha=args.pr_head, assessor=assessor,
                              pr_created_at=pr["created_at"])
    if args.expect:
        previous = json.loads(args.expect.read_text())
        if previous != evidence:
            raise GateError("human impact decision changed before merge")
    else:
        args.out.write_text(json.dumps(evidence, sort_keys=True) + "\n")
    print(f"Human impact decision confirmed at comment {evidence['commentId']}")


if __name__ == "__main__":
    main()
