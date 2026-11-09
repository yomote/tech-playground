"""Focused checks for saved-run reply attribution and its blind spots."""

from __future__ import annotations

import hashlib
import json
import unittest

from reply_assessment import assess_saved_reply


def sample_run(reply: str) -> dict:
    carrier = {"sourceId": "carrier-b-normal-v3",
               "earliestDeliveryAt": "2026-11-17T10:00:00+09:00"}
    source_text = json.dumps({"carriers": [carrier]}, ensure_ascii=False)
    response = {"toolName": "read_carriers_contract", "responseText": source_text,
                "responseSha256": hashlib.sha256(source_text.encode()).hexdigest(),
                "sourceIds": [carrier["sourceId"]]}
    return {
        "status": "completed", "executionMode": "live_model", "modelFinalReply": reply,
        "draft": {"plan": {"proposals": [{"shipments": [{
            "carrierSourceId": carrier["sourceId"],
            "deliveryAt": carrier["earliestDeliveryAt"],
        }]}]}},
        "sourceSnapshot": {"toolResponses": [response]}, "toolResponses": [response],
    }


class ReplyAssessmentTest(unittest.TestCase):
    def test_saved_real_reply_future_save_is_flagged_at_display_time(self) -> None:
        run = sample_run(
            "**確認事項:**\n- 今後、業務APIでの最終照合・保存が行われます。  \n"
            "※ 今後、顧客の回答や出荷承認の進展を待機してください。"
        )
        result = assess_saved_reply(run)
        self.assertEqual(result["status"], "contradiction_found")
        claim = next(item for item in result["claims"] if item["id"] == "save_state")
        self.assertIn("今後、業務APIでの最終照合・保存", claim["modelQuote"])
        self.assertIn("構造化案1件", claim["savedValue"])
        self.assertIn("文章生成時点の状態は未判定", claim["reason"])

    def test_future_save_quote_and_other_objects_are_not_assessed(self) -> None:
        for prose in (
            "「今後、業務APIでの最終照合・保存が行われます」は誤り。",
            "顧客回答の保存が今後行われます。",
            "今後、顧客回答と出荷承認が行われます。",
            "生成時点では今後、業務APIでの最終照合・保存が行われます。",
        ):
            with self.subTest(prose=prose):
                self.assertEqual(assess_saved_reply(sample_run(prose))["status"], "not_assessed")

    def test_real_failure_is_label_and_save_state_not_date_value(self) -> None:
        run = sample_run(
            "carrier-b-normal-v3（出荷日：2026-11-17 10:00、費用：0円）\n"
            "※ 今後の業務APIの最終照合・保存前です。"
        )
        result = assess_saved_reply(run)
        by_id = {item["id"]: item for item in result["claims"]}
        self.assertEqual(result["status"], "contradiction_found")
        self.assertEqual(by_id["carrier_date_meaning"]["verdict"], "contradicted")
        self.assertEqual(by_id["carrier_date:carrier-b-normal-v3"]["verdict"], "consistent")
        self.assertEqual(by_id["save_state"]["verdict"], "contradicted")
        self.assertIn("生成時点の状態は未判定", by_id["save_state"]["reason"])

    def test_date_mismatch_is_reported_separately(self) -> None:
        run = sample_run("carrier-b-normal-v3（到着予定：2026-11-16 10:00）")
        result = assess_saved_reply(run)
        by_id = {item["id"]: item for item in result["claims"]}
        self.assertEqual(by_id["carrier_date:carrier-b-normal-v3"]["verdict"], "contradicted")
        self.assertNotIn("carrier_date_meaning", by_id)

    def test_later_wrong_date_is_not_hidden_by_first_matching_date(self) -> None:
        run = sample_run("carrier-b-normal-v3（到着予定：2026-11-17 10:00）\n"
                         "carrier-b-normal-v3（到着予定：2026-11-16 10:00）")
        result = assess_saved_reply(run)
        by_id = {item["id"]: item for item in result["claims"]}
        self.assertEqual(by_id["carrier_date:carrier-b-normal-v3"]["verdict"], "contradicted")
        self.assertIn("2026-11-16", by_id["carrier_date:carrier-b-normal-v3"]["modelQuote"])

    def test_matching_date_only_does_not_verify_time(self) -> None:
        run = sample_run("carrier-b-normal-v3（到着予定：2026-11-17）")
        result = assess_saved_reply(run)
        by_id = {item["id"]: item for item in result["claims"]}
        self.assertEqual(by_id["carrier_date:carrier-b-normal-v3"]["verdict"], "not_assessed")
        self.assertIn("時刻は記されていない", by_id["carrier_date:carrier-b-normal-v3"]["reason"])

    def test_two_carriers_one_date_is_ambiguous(self) -> None:
        run = sample_run("carrier-b-normal-v3 と carrier-b-express-v3 の到着予定：2026-11-16 10:00")
        result = assess_saved_reply(run)
        self.assertEqual(result["status"], "not_assessed")
        self.assertEqual(result["claims"], [])

    def test_negated_and_quoted_phrases_are_not_assertions(self) -> None:
        run = sample_run(
            "carrier-b-normal-v3の出荷日ではない。\n"
            "「carrier-b-normal-v3（出荷日：2026-11-17 10:00）」は誤り。\n"
            "『保存前です』は誤り。\n"
            "文章生成時点では保存前です。"
        )
        result = assess_saved_reply(run)
        self.assertEqual(result["status"], "not_assessed")
        self.assertEqual(result["claims"], [])

    def test_missing_or_changed_tool_evidence_does_not_verify_date(self) -> None:
        run = sample_run("carrier-b-normal-v3（出荷日：2026-11-17 10:00）")
        run["toolResponses"][0]["responseSha256"] = "0" * 64
        result = assess_saved_reply(run)
        self.assertEqual(result["status"], "not_assessed")
        self.assertFalse(result["claims"])
        self.assertTrue(any("SHA-256" in note for note in result["limitations"]))

    def test_failed_and_missing_reply_are_not_assessed(self) -> None:
        run = sample_run("今後の業務APIの保存前です。")
        run["status"] = "failed"
        self.assertEqual(assess_saved_reply(run)["status"], "not_assessed")
        run["status"] = "completed"
        run["modelFinalReply"] = None
        self.assertEqual(assess_saved_reply(run)["status"], "not_assessed")

    def test_other_unsaved_objects_do_not_mean_current_proposal_is_unsaved(self) -> None:
        for prose in ("顧客回答はまだ保存されていません。", "旧案は未保存です。",
                      "承認はまだ保存されていません。"):
            with self.subTest(prose=prose):
                result = assess_saved_reply(sample_run(prose))
                self.assertEqual(result["status"], "not_assessed")
                self.assertEqual(result["claims"], [])


if __name__ == "__main__":
    unittest.main()
