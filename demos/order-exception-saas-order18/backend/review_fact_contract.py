"""Narrow edit contract for the synthetic two-option, unsent customer preview.

The server produces factual lines from the saved, source-checked proposal.
The human may edit only the question line. This does not assess the meaning of
free prose or authenticate the human actor.
"""

from __future__ import annotations

import re
from source_fixtures import FIXTURE_ID


CONTRACT_VERSION = "locked_preview_facts_v1"
DEFAULT_APPROVED_QUESTIONS = (
    "分納の可否をご返信ください。特急便の追加費用は合成契約に販売者負担と記載され、担当者の原文確認を待っています。",
    "通常便案と特急便案のどちらをご希望か、分納の承諾とあわせてご返信ください。特急費用の販売者負担は担当者が原文を確認します。",
    "分納をご承諾いただけるか、ご回答ください。特急便をご希望の場合、販売者負担の契約原文を担当者が確認します。",
)
DELIVERY_CHANGE_APPROVED_QUESTIONS = (
    "通常便案は希望納期を超える見込みです。分納と納期変更をご了承いただけるか、特急便案と比較してご返信ください。特急便の費用負担先は別途確認します。",
    "通常便案の納期変更に同意いただけるか、特急便案を希望されるかをご返信ください。分納の承諾と費用負担先も別途確認します。",
)
APPROVED_QUESTIONS = (DELIVERY_CHANGE_APPROVED_QUESTIONS
                      if FIXTURE_ID == "oe02-order18-due16-20261111"
                      else DEFAULT_APPROVED_QUESTIONS)


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
