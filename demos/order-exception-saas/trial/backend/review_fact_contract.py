"""Narrow edit contract for the synthetic two-option, unsent customer preview.

The server produces factual lines from the saved, source-checked proposal.
The human may edit only the question line. This does not assess the meaning of
free prose or authenticate the human actor.
"""

from __future__ import annotations

import re


CONTRACT_VERSION = "locked_preview_facts_v1"
APPROVED_QUESTIONS = (
    "分納の可否をご返信ください。特急便の追加費用の負担先は別途確認します。",
    "通常便案と特急便案のどちらをご希望か、分納の承諾とあわせてご返信ください。追加費用の負担先は別途確認します。",
    "分納をご承諾いただけるか、ご回答ください。特急便をご希望の場合、追加費用の負担先は別途確認します。",
)


class ReviewFactError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_review_fact_contract(original: str, edited: str) -> None:
    """Keep generated facts/disclaimer byte-for-byte and allow one prose line.

    The immutable lines bind each warehouse's quantity, planned arrival and
    added cost to its option. Digits in the prose line would introduce a new
    factual number outside the protected source projection, so reject them.
    """
    source_lines = original.split("\n")
    edited_lines = edited.split("\n")
    if len(source_lines) != 5 or len(edited_lines) != 5 or any(
        edited_lines[index] != source_lines[index] for index in (0, 1, 2, 4)
    ):
        raise ReviewFactError(
            "review_facts_mismatch",
            "見出し、配送案の数量・到着予定・費用、未確認事項は変更できません。元の文案を再読込してください。",
        )
    if not edited_lines[3].strip():
        raise ReviewFactError("review_question_required", "顧客への確認文を入力してください。")
    if re.search(r"[0-9０-９]", edited_lines[3]):
        raise ReviewFactError(
            "review_question_numeric_claim",
            "確認文に数値を追加できません。数量・到着予定・費用は上の配送案を参照してください。",
        )
    if edited_lines[3] not in APPROVED_QUESTIONS:
        raise ReviewFactError(
            "review_question_not_approved",
            "確認文は提示された選択肢から選んでください。任意の文は内容確認後に別途扱います。",
        )
