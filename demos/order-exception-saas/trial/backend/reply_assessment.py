"""Narrow, read-only checks of model prose against this run's saved facts.

This is a domain presentation check, not a natural-language verifier. Unknown
wording is left unassessed and the received model reply remains unchanged.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from typing import Any


_CARRIER_DATE = re.compile(
    # A carrier ID followed by its own parenthesized date is one-to-one. A
    # sentence naming multiple carriers is ambiguous and must be skipped.
    r"(?P<carrier>carrier-[a-z0-9-]+-v\d+)\s*[（(]\s*"
    r"(?P<label>到着予定|到着日時|出荷日|出荷日時)\s*[:：]\s*"
    r"(?P<date>20\d\d-\d\d-\d\d(?:[ T]\d\d:\d\d(?::\d\d)?)?"
    r"(?:[+-]\d\d:\d\d)?)"
)
_UNSAVED_ASSERTION = re.compile(r"(?:保存前です|未保存です|保存していません|まだ保存されていません)[。.]?\s*$")
_FUTURE_SAVE_ASSERTION = re.compile(
    r"(?:今後|これから)[、, ]*業務API(?:での|による|の)最終照合[・と]保存"
    r"(?:が行われます|を行います|します|されます|の予定です)[。.]?\s*$"
)
_PAST_SAVE_CONTEXT = re.compile(r"(?:生成時点|生成した時点|提出時点|提出した時点|当時|保存前だった)")
_CURRENT_PROPOSAL_SUBJECT = re.compile(r"(?:この案|提出案|現行案|現在の案|業務API(?:の|での|による)最終照合)")
_OTHER_SAVE_SUBJECT = re.compile(r"(?:顧客回答|旧案|過去案|前の案|承認|出荷指示)")


def _quoted_or_negated(line: str, start: int, end: int) -> bool:
    """Avoid treating a quoted or explicitly rejected phrase as a model claim."""
    prefix, suffix = line[:start], line[end:]
    if (prefix.count("「") > prefix.count("」") or
            prefix.count("『") > prefix.count("』")):
        return True
    return re.search(r"(?:ではない|ではなく|は誤り|と誤記|は誤記)", suffix[:28]) is not None


def _saved_carriers(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Use only byte-identical, digest-verified Tool evidence frozen in this run."""
    snapshot = run.get("sourceSnapshot")
    actual = run.get("toolResponses")
    if not isinstance(snapshot, dict) or not isinstance(actual, list):
        return {}
    responses = snapshot.get("toolResponses")
    if not isinstance(responses, list) or responses != actual:
        return {}
    carrier_responses = [item for item in responses
                         if isinstance(item, dict) and item.get("toolName") == "read_carriers_contract"]
    if len(carrier_responses) != 1:
        return {}
    item = carrier_responses[0]
    text, digest = item.get("responseText"), item.get("responseSha256")
    if not isinstance(text, str) or not isinstance(digest, str):
        return {}
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
        return {}
    try:
        payload = json.loads(text)
        carriers = payload["carriers"]
    except (ValueError, KeyError, TypeError):
        return {}
    if not isinstance(carriers, list):
        return {}
    source_ids = item.get("sourceIds")
    if not isinstance(source_ids, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for carrier in carriers:
        if (not isinstance(carrier, dict) or
                not isinstance(carrier.get("sourceId"), str) or
                not isinstance(carrier.get("earliestDeliveryAt"), str) or
                carrier["sourceId"] not in source_ids):
            return {}
        if carrier["sourceId"] in result:
            return {}
        result[carrier["sourceId"]] = carrier
    return result


def _wall_date_matches(claimed: str, saved: str) -> tuple[bool, bool] | None:
    """Compare the date/time as written; don't invent a timezone for bare prose."""
    try:
        expected = datetime.fromisoformat(saved)
        stated = datetime.fromisoformat(claimed.replace(" ", "T"))
    except ValueError:
        return None
    if len(claimed) == 10:
        return stated.date() == expected.date(), False
    if stated.tzinfo is None:
        return stated.replace(tzinfo=None) == expected.replace(tzinfo=None), True
    return stated == expected, True


def assess_saved_reply(run: dict[str, Any]) -> dict[str, Any]:
    """Assess only explicit save-state and carrier-date claims in a saved reply."""
    checked_against = "このrunの保存済み構造化案・保存状態・保存済み便Tool応答"
    limitations = [
        "原文全体、契約適合、追加費用の負担先、実際の集荷・出荷日時は判定していません。",
        "日時値が一致しても、その便を実際に利用できることや顧客への回答の正確性は保証しません。",
        "モデルが文章を生成した時点の保存状態と、最終発話の厳密な帰属は確認していません。",
    ]
    result: dict[str, Any] = {"status": "not_assessed", "checkedAgainst": checked_against,
                              "claims": [], "limitations": limitations}
    prose = run.get("modelFinalReply")
    if (run.get("status") != "completed" or run.get("executionMode") != "live_model" or
            not isinstance(prose, str) or not prose.strip()):
        result["limitations"].insert(0, "完了した実モデルrunの保存原文がないため、返答を判定できません。")
        return result

    draft = run.get("draft")
    proposals = draft.get("plan", {}).get("proposals") if isinstance(draft, dict) else None
    saved_proposals = proposals if isinstance(proposals, list) else []
    for line in prose.splitlines():
        match = _UNSAVED_ASSERTION.search(line) or _FUTURE_SAVE_ASSERTION.search(line)
        if (match and saved_proposals and
                not _quoted_or_negated(line, match.start(), match.end()) and
                _PAST_SAVE_CONTEXT.search(line[:match.start()]) is None and
                (_CURRENT_PROPOSAL_SUBJECT.search(line[:match.start()]) is not None or
                 _CURRENT_PROPOSAL_SUBJECT.search(match.group()) is not None) and
                _OTHER_SAVE_SUBJECT.search(line[:match.start()]) is None):
            result["claims"].append({
                "id": "save_state", "label": "案の保存状態", "verdict": "contradicted",
                "modelQuote": line.strip(),
                "savedValue": f"このrunは完了し、構造化案{len(saved_proposals)}件を業務APIに保存済みです。",
                "reason": "保存run画面で表示する時点の保存状態と不一致です。文章生成時点の状態は未判定です。",
            })
            break

    carriers = _saved_carriers(run)
    shipment_dates: dict[str, set[str]] = {}
    for option in saved_proposals:
        if not isinstance(option, dict):
            continue
        for shipment in option.get("shipments", []):
            if isinstance(shipment, dict) and isinstance(shipment.get("carrierSourceId"), str) and isinstance(shipment.get("deliveryAt"), str):
                shipment_dates.setdefault(shipment["carrierSourceId"], set()).add(shipment["deliveryAt"])

    observed: dict[str, list[tuple[str, str, str]]] = {}
    for line in prose.splitlines():
        for match in _CARRIER_DATE.finditer(line):
            if _quoted_or_negated(line, match.start(), match.end()):
                continue
            carrier_id = match.group("carrier")
            if carrier_id in carriers and carrier_id in shipment_dates:
                observed.setdefault(carrier_id, []).append((match.group("label"), match.group("date"), line.strip()))

    incorrect_label: tuple[str, str] | None = None
    for carrier_id, claims in observed.items():
        source_date = carriers[carrier_id]["earliestDeliveryAt"]
        if shipment_dates[carrier_id] != {source_date}:
            # An inconsistent frozen run is not a basis for judging prose.
            continue
        classified: list[tuple[str, str, str, bool, bool]] = []
        for label, claimed_date, line in claims:
            comparison = _wall_date_matches(claimed_date, source_date)
            if comparison is None:
                continue
            matches, has_time = comparison
            classified.append((label, claimed_date, line, matches, has_time))
            if label in {"出荷日", "出荷日時"} and incorrect_label is None:
                incorrect_label = (line, source_date)
        if not classified:
            continue
        # A later wrong statement must not be hidden by an earlier correct one.
        selected = next((item for item in classified if not item[3]), None)
        if selected is None:
            selected = next((item for item in classified if not item[4]), classified[0])
        _, _, line, matches, has_time = selected
        verdict = "contradicted" if not matches else "consistent" if has_time else "not_assessed"
        reason = ("原文の日時値が、このrunの便Tool応答と保存案の到着予定に一致しません。"
                  if not matches else
                  "原文の日付は一致しますが、時刻は記されていないため未判定です。"
                  if not has_time else
                  "この日時値は一致します。日時の意味は別に判定します。")
        result["claims"].append({
            "id": f"carrier_date:{carrier_id}", "label": f"{carrier_id}の日時値",
            "verdict": verdict, "modelQuote": line,
            "savedValue": f"保存案の到着予定は{source_date}です。",
            "reason": reason,
        })
    if incorrect_label:
        line, source_date = incorrect_label
        result["claims"].insert(0, {
            "id": "carrier_date_meaning", "label": "便日時の意味",
            "verdict": "contradicted", "modelQuote": line,
            "savedValue": f"便資料のearliestDeliveryAtは到着予定（例：{source_date}）です。出荷日時の資料はありません。",
            "reason": "到着予定の日時を出荷日と呼んでいます。日時値の一致とは別の誤りです。",
        })
    if not carriers:
        limitations.insert(0, "保存済み便Tool応答の同一性・SHA-256を確認できず、便日時の原文は判定していません。")
    if not observed:
        limitations.insert(0, "便IDと日時を一対一で読める記述がなく、便日時の原文は判定していません。")
    verdicts = {item["verdict"] for item in result["claims"]}
    result["status"] = ("contradiction_found" if "contradicted" in verdicts else
                        "no_contradiction_found" if "consistent" in verdicts else "not_assessed")
    return result
