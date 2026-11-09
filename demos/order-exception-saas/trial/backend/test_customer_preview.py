"""Focused saved-run checks for the read-only customer wording projection."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from customer_preview import customer_preview
from server import ApiError, ORDER_ID, OrderStore, SUPPORTED_REQUEST_TEXT
from source_fixtures import CARRIER_SOURCES, CONTRACT_SOURCE, FIXTURE_VERSION
from review_fact_contract import APPROVED_QUESTIONS


class CustomerPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.validity = patch.object(OrderStore, "fixture_validity", return_value={"newRunAvailable": True})
        self.validity.start()
        self.store = OrderStore(Path(self.temp.name) / "orders.sqlite3")

    def tearDown(self) -> None:
        self.validity.stop()
        self.temp.cleanup()

    def saved_run(self, *, status: str = "completed", reply: str | None = "契約に準拠。出荷日は11月16日。") -> dict:
        snapshot = self.store.get_detail(ORDER_ID)
        order = snapshot["order"]
        run, created = self.store.create_run(
            ORDER_ID, str(uuid4()), route="direct", model_id="test-model",
            execution_mode="live_model", request_text=SUPPORTED_REQUEST_TEXT,
            expected_version=order["version"],
        )
        self.assertTrue(created)
        run_id = run["id"]
        self.assertTrue(self.store.start_run(run_id))
        order_source_id = f"order-{ORDER_ID}-v{order['version']}"
        stock_ids = [f"stock-{warehouse}-snapshot-order-v{order['version']}"
                     for warehouse in ("warehouse-a", "warehouse-b")]
        order_response = {
            "dataMode": "synthetic_fixture", "orderSourceId": order_source_id,
            "orderVersion": order["version"], "quantityBoxes": order["quantity"],
            "dueAt": order["dueAt"], "detectedAt": snapshot["exception"]["detectedAt"],
            "warehouses": [{"sourceId": source_id, "warehouseId": warehouse,
                            "availableBoxes": next(item["available"] for item in snapshot["inventory"]
                                                   if item["warehouseId"] == warehouse)}
                           for warehouse, source_id in zip(("warehouse-a", "warehouse-b"), stock_ids)],
        }
        carriers_response = {"dataMode": "synthetic_fixture", "carriers": list(CARRIER_SOURCES.values()),
                             "contract": CONTRACT_SOURCE}
        for name, value, ids in (
            ("read_order_inventory", order_response, [order_source_id, *stock_ids]),
            ("read_carriers_contract", carriers_response, [*CARRIER_SOURCES, CONTRACT_SOURCE["sourceId"]]),
        ):
            response_text = json.dumps(value, ensure_ascii=False)
            self.store.capture_tool_response(
                run_id, tool_name=name, response_text=response_text,
                response_sha256=hashlib.sha256(response_text.encode()).hexdigest(),
                source_ids=ids, fixture_version=FIXTURE_VERSION,
            )
        snapshot["toolResponses"] = self.store.get_tool_responses(run_id)
        proposals = []
        for suffix, b_carrier in (("normal", "carrier-b-normal-v3"),
                                  ("express", "carrier-b-express-v3")):
            carrier_ids = ["carrier-a-normal-v3", b_carrier]
            shipments = [{"warehouseId": warehouse, "quantityBoxes": quantity,
                          "carrierSourceId": carrier_id,
                          "deliveryAt": CARRIER_SOURCES[carrier_id]["earliestDeliveryAt"]}
                         for warehouse, quantity, carrier_id in zip(
                             ("warehouse-a", "warehouse-b"), (25, 15), carrier_ids)]
            proposals.append({
                "id": f"{run_id}:{suffix}", "runId": run_id,
                "deliveryPattern": "separate_warehouse_shipments", "shipments": shipments,
                "additionalCostJpy": sum(CARRIER_SOURCES[item]["additionalCostJpy"] for item in carrier_ids),
                "sourceRefs": sorted([order_source_id, *stock_ids, *carrier_ids,
                                      CONTRACT_SOURCE["sourceId"]]),
            })
        draft = {"planBusinessValidation": "structured_source_consistency_checked",
                 "projectionMode": "api_from_model_structured_data",
                 "plan": {"validation": "structured_source_consistency_checked", "proposals": proposals}}
        self.assertTrue(self.store.finish_run(
            run_id, status, draft=draft, source_snapshot=snapshot,
            model_final_reply=reply if status == "completed" else None,
        ))
        return self.store.get_run(run_id)

    def test_get_uses_saved_structured_facts_and_never_model_prose(self) -> None:
        run = self.saved_run()
        preview = run["customerPreview"]
        self.assertEqual(preview["status"], "available")
        self.assertEqual(preview["basis"], "saved_structured_draft")
        self.assertIs(preview["sendable"], False)
        self.assertIn("倉庫Aから25箱", preview["text"])
        self.assertIn("倉庫Bから15箱", preview["text"])
        self.assertIn("2026-11-17 10:00", preview["text"])
        self.assertIn("78,000円", preview["text"])
        self.assertIn("未送信", preview["text"])
        self.assertIn("負担先は別途確認", preview["text"])
        self.assertNotIn("契約に準拠", preview["text"])
        self.assertNotIn("出荷日は", preview["text"])
        self.assertEqual(run["modelFinalReply"], "契約に準拠。出荷日は11月16日。")

    def test_draft_ready_is_usable_only_while_source_still_current(self) -> None:
        run = self.saved_run(status="draft_ready", reply=None)
        self.assertEqual(run["customerPreview"]["status"], "available")
        with sqlite3.connect(self.store.db_path) as connection:
            connection.execute("UPDATE orders SET version = version + 1 WHERE id = ?", (ORDER_ID,))
        later = self.store.get_run(run["id"])["customerPreview"]
        self.assertEqual(later["status"], "not_available")
        self.assertTrue(later["historical"])

    def test_old_run_even_same_order_version_is_unavailable(self) -> None:
        old = self.saved_run()
        self.saved_run()
        preview = self.store.get_run(old["id"])["customerPreview"]
        self.assertEqual(preview["status"], "not_available")
        self.assertTrue(preview["historical"])
        self.assertNotIn("text", preview)

    def test_source_mismatch_missing_plan_and_expired_pickup_fail_closed(self) -> None:
        run = self.saved_run()
        order = self.store.get_detail(ORDER_ID)["order"]
        inventory = self.store.get_detail(ORDER_ID)["inventory"]
        run["draft"]["plan"]["proposals"][0]["shipments"][0]["deliveryAt"] = "2026-11-15T10:00:00+09:00"
        bad = customer_preview(run, current_order=order, current_inventory=inventory,
                               latest_run_id=run["id"])
        self.assertEqual(bad["status"], "not_available")
        run["draft"]["plan"]["proposals"][0]["shipments"][0]["deliveryAt"] = CARRIER_SOURCES["carrier-a-normal-v3"]["earliestDeliveryAt"]
        run["sourceSnapshot"]["toolResponses"][0]["responseSha256"] = "0" * 64
        bad = customer_preview(run, current_order=order, current_inventory=inventory,
                               latest_run_id=run["id"])
        self.assertEqual(bad["status"], "not_available")
        run["sourceSnapshot"]["toolResponses"][0]["responseSha256"] = run["toolResponses"][0]["responseSha256"]
        expired = customer_preview(run, current_order=order, current_inventory=inventory,
                                   latest_run_id=run["id"],
                                   now=datetime.fromisoformat("2026-11-15T16:01:00+09:00"))
        self.assertEqual(expired["status"], "not_available")
        self.assertIn("集荷期限", expired["reason"])
        run.pop("draft")
        self.assertEqual(customer_preview(run, current_order=order, current_inventory=inventory,
                                          latest_run_id=run["id"])["status"], "not_available")

    def test_changed_next_pickup_is_not_a_current_source(self) -> None:
        run = self.saved_run()
        response = next(item for item in run["toolResponses"]
                        if item["toolName"] == "read_carriers_contract")
        changed = json.loads(response["responseText"])
        normal_b = next(item for item in changed["carriers"]
                        if item["sourceId"] == "carrier-b-normal-v3")
        normal_b["nextPickupAt"] = "2026-11-16T16:00:00+09:00"
        new_text = json.dumps(changed, ensure_ascii=False)
        for records in (run["toolResponses"], run["sourceSnapshot"]["toolResponses"]):
            item = next(item for item in records if item["toolName"] == "read_carriers_contract")
            item["responseText"] = new_text
            item["responseSha256"] = hashlib.sha256(new_text.encode()).hexdigest()
        detail = self.store.get_detail(ORDER_ID)
        preview = customer_preview(run, current_order=detail["order"],
                                   current_inventory=detail["inventory"], latest_run_id=run["id"])
        self.assertEqual(preview["status"], "not_available")

    def test_mock_mode_and_swapped_option_identity_are_unavailable(self) -> None:
        run = self.saved_run()
        detail = self.store.get_detail(ORDER_ID)
        args = {"current_order": detail["order"], "current_inventory": detail["inventory"],
                "latest_run_id": run["id"]}
        run["executionMode"] = "test_double"
        self.assertEqual(customer_preview(run, **args)["status"], "not_available")
        run["executionMode"] = "live_model"
        run["draft"]["plan"]["proposals"][0]["id"] = f"{run['id']}:express"
        self.assertEqual(customer_preview(run, **args)["status"], "not_available")

    def test_failed_run_has_no_preview(self) -> None:
        snapshot = self.store.get_detail(ORDER_ID)
        run, _ = self.store.create_run(
            ORDER_ID, str(uuid4()), route="direct", model_id="test-model",
            execution_mode="live_model", request_text=SUPPORTED_REQUEST_TEXT,
            expected_version=snapshot["order"]["version"],
        )
        self.assertTrue(self.store.start_run(run["id"]))
        self.assertTrue(self.store.finish_run(run["id"], "failed", error_code="test_failure"))
        preview = self.store.get_run(run["id"])["customerPreview"]
        self.assertEqual(preview["status"], "not_available")
        self.assertNotIn("text", preview)

    def test_review_rejects_changed_facts_and_preserves_one_saved_choice(self) -> None:
        run = self.saved_run()
        original = run["customerPreview"]["text"]
        lines = original.split("\n")
        body = {
            "runId": run["id"], "trialId": run["trialId"],
            "expectedVersion": self.store.get_detail(ORDER_ID)["order"]["version"],
            "expectedPreviewSha256": run["customerPreview"]["reviewBasisSha256"],
            "reason": "顧客に分納承諾を明確に尋ねるため",
            "editedText": "\n".join([*lines[:3], APPROVED_QUESTIONS[0], lines[4]]),
            "operationId": str(uuid4()), "actorRole": "order_clerk",
        }
        changes = {
            "quantity": ("\n".join([lines[0], lines[1].replace("25箱", "26箱", 1), *lines[2:]]),
                         "review_facts_mismatch"),
            "arrival": ("\n".join([lines[0], lines[1].replace("2026-11-17", "2026-11-18", 1), *lines[2:]]),
                        "review_facts_mismatch"),
            "cost": ("\n".join([lines[0], lines[1], lines[2].replace("78,000円", "79,000円"), *lines[3:]]),
                     "review_facts_mismatch"),
            "unsupported_claim": ("\n".join([*lines[:3], "特急費用は当社負担です。", lines[4]]),
                                  "review_question_not_approved"),
        }
        for name, (edited, error_code) in changes.items():
            with self.subTest(name=name), self.assertRaises(ApiError) as caught:
                self.store.record_customer_preview_review(
                    ORDER_ID, {**body, "editedText": edited, "operationId": str(uuid4())})
            self.assertEqual((caught.exception.status, caught.exception.code), (422, error_code))
        with sqlite3.connect(self.store.db_path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM customer_preview_reviews").fetchone()[0], 0)
        saved = self.store.record_customer_preview_review(ORDER_ID, body)["run"]["customerPreview"]["review"]
        self.assertEqual(saved["factCheckStatus"], "protected_facts_unchanged")
        self.assertFalse(saved["verified"] or saved["sendable"])
        self.assertEqual(self.store.get_run(run["id"])["customerPreview"]["review"]["id"], saved["id"])
        self.assertEqual(self.store.record_customer_preview_review(ORDER_ID, body)["run"]
                         ["customerPreview"]["review"]["id"], saved["id"])
        with sqlite3.connect(self.store.db_path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM customer_preview_reviews").fetchone()[0], 1)
            for table in ("customer_responses", "approvals", "reservations", "shipment_instructions"):
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
            connection.execute(
                "UPDATE customer_preview_reviews SET review_basis_sha256 = ? WHERE id = ?",
                ("0" * 64, saved["id"]),
            )
        mismatched = self.store.get_run(run["id"])["customerPreview"]
        self.assertEqual(mismatched["status"], "not_available")
        self.assertFalse(mismatched["historical"])
        self.assertNotIn("text", mismatched)
        self.assertEqual(mismatched["historicalReview"]["id"], saved["id"])
        self.assertFalse(mismatched["historicalReview"]["sendable"])

    def test_saved_review_remains_read_only_after_customer_response(self) -> None:
        run = self.saved_run()
        preview = run["customerPreview"]
        lines = preview["text"].split("\n")
        reviewed = self.store.record_customer_preview_review(ORDER_ID, {
            "runId": run["id"], "trialId": run["trialId"],
            "expectedVersion": self.store.get_detail(ORDER_ID)["order"]["version"],
            "expectedPreviewSha256": preview["reviewBasisSha256"],
            "reason": "分納を確認するため",
            "editedText": "\n".join([*lines[:3], APPROVED_QUESTIONS[0], lines[4]]),
            "operationId": str(uuid4()), "actorRole": "order_clerk",
        })["run"]["customerPreview"]["review"]
        proposal = run["draft"]["plan"]["proposals"][0]
        self.store.record_agent_customer_response(ORDER_ID, {
            "runId": run["id"], "selectedOptionId": proposal["id"],
            "acknowledgedTerms": self.store._agent_terms(proposal),
            "acceptedSplit": True, "acceptedExpressCost": False,
            "expectedVersion": self.store.get_detail(ORDER_ID)["order"]["version"],
            "operationId": str(uuid4()), "actorRole": "order_clerk",
        })
        later = self.store.get_run(run["id"])["customerPreview"]
        self.assertEqual(later["status"], "not_available")
        self.assertNotIn("text", later)
        self.assertEqual(later["historicalReview"]["id"], reviewed["id"])
        self.assertFalse(later["historicalReview"]["sendable"])
        with self.assertRaises(ApiError) as caught:
            self.store.record_customer_preview_review(ORDER_ID, {
                "runId": run["id"], "trialId": run["trialId"],
                "expectedVersion": self.store.get_detail(ORDER_ID)["order"]["version"],
                "expectedPreviewSha256": preview["reviewBasisSha256"],
                "reason": "再保存", "editedText": reviewed["editedText"],
                "operationId": str(uuid4()), "actorRole": "order_clerk",
            })
        self.assertEqual(caught.exception.status, 409)


if __name__ == "__main__":
    unittest.main()
