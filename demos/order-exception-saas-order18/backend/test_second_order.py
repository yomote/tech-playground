"""Focused fixed-case checks; no model, Keycloak, or OpenFGA call."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from server import ApiError, OrderStore, ORDER_ID, SUPPORTED_REQUEST_TEXT
from source_fixtures import CARRIER_SOURCES, CONTRACT_SOURCE, FIXTURE_ID, FIXTURE_VERSION


class SecondOrderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "order18.sqlite3"
        self.store = OrderStore(self.db)

    def business_state(self) -> dict:
        with sqlite3.connect(self.db) as db:
            order = db.execute("SELECT status,version FROM orders WHERE id=?", (ORDER_ID,)).fetchone()
            stock = dict(db.execute("SELECT warehouse_id,available FROM inventory"))
            counts = {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                      for name in ("customer_responses", "source_review_requests", "approvals",
                                   "reservations", "shipment_instructions", "operations")}
        return {"order": order, "stock": stock, "counts": counts}

    def test_fresh_case_has_distinct_order_and_source_facts(self) -> None:
        detail = self.store.get_detail(ORDER_ID)
        order = detail["order"]
        self.assertEqual((order["id"], order["customerName"], order["sku"], order["version"]),
                         ("SO-24018", "北浜精機（合成顧客）", "JOINT-40", 2))
        self.assertEqual((order["dueAt"], FIXTURE_ID, FIXTURE_VERSION),
                         ("2026-11-16T10:00:00+09:00", "oe02-order18-due16-20261111", "5.0"))
        self.assertEqual(CONTRACT_SOURCE["sourceId"], "contract-split-v5")
        self.assertIn("SO-24018", CONTRACT_SOURCE["clause"])
        self.assertIn("販売者が負担", CONTRACT_SOURCE["clause"])
        self.assertIn("SO-24018", SUPPORTED_REQUEST_TEXT)
        self.assertIn("通常便", SUPPORTED_REQUEST_TEXT)
        self.assertIn("特急便", SUPPORTED_REQUEST_TEXT)
        self.assertGreater(CARRIER_SOURCES["carrier-b-normal-v5"]["earliestDeliveryAt"], order["dueAt"])
        self.assertEqual(CARRIER_SOURCES["carrier-b-express-v5"]["earliestDeliveryAt"], order["dueAt"])
        self.assertEqual(self.business_state()["counts"]["customer_responses"], 0)
        self.assertEqual(len(self.store.list_runs(ORDER_ID)["agentRuns"]), 0)

    def test_fixed_late_normal_response_rejected_without_write(self) -> None:
        option = self.store.get_options(ORDER_ID)["options"][0]
        before = self.business_state()
        body = {"selectedOptionId": option["id"],
                "acknowledgedTerms": self.store._option_terms(option),
                "acceptedSplit": True, "acceptedExpressCost": False,
                "expectedVersion": 2, "operationId": str(uuid4()),
                "actorRole": "order_clerk"}
        with self.assertRaises(ApiError) as caught:
            self.store.record_customer_response(ORDER_ID, body)
        self.assertEqual((caught.exception.status, caught.exception.code),
                         (422, "late_delivery_consent_required"))
        self.assertEqual(self.business_state(), before)

    def test_agent_late_normal_response_rejected_without_write(self) -> None:
        run, created = self.store.create_run(
            ORDER_ID, str(uuid4()), route="test-double", model_id="none",
            execution_mode="test_double", request_text=SUPPORTED_REQUEST_TEXT,
            expected_version=2)
        self.assertTrue(created)
        self.assertTrue(self.store.start_run(run["id"]))
        snapshot = self.store.get_detail(ORDER_ID)
        normal = {"id": f"{run['id']}:normal", "runId": run["id"],
                  "additionalCostJpy": 0,
                  "shipments": [
                      {"warehouseId": "warehouse-a", "quantityBoxes": 25,
                       "carrierSourceId": "carrier-a-normal-v5",
                       "deliveryAt": CARRIER_SOURCES["carrier-a-normal-v5"]["earliestDeliveryAt"]},
                      {"warehouseId": "warehouse-b", "quantityBoxes": 15,
                       "carrierSourceId": "carrier-b-normal-v5",
                       "deliveryAt": CARRIER_SOURCES["carrier-b-normal-v5"]["earliestDeliveryAt"]}],
                  "sourceRefs": ["contract-split-v5"]}
        self.assertTrue(self.store.finish_run(run["id"], "completed",
                                              draft={"plan": {"proposals": [normal]}},
                                              source_snapshot=snapshot))
        before = self.business_state()
        body = {"runId": run["id"], "selectedOptionId": normal["id"],
                "acknowledgedTerms": self.store._agent_terms(normal),
                "acceptedSplit": True, "acceptedExpressCost": False,
                "acceptedLateDelivery": False,
                "expectedVersion": 2, "operationId": str(uuid4()),
                "actorRole": "order_clerk"}
        with self.assertRaises(ApiError) as caught:
            self.store.record_agent_customer_response(ORDER_ID, body)
        self.assertEqual((caught.exception.status, caught.exception.code),
                         (422, "late_delivery_consent_required"))
        self.assertEqual(self.business_state(), before)

    def test_express_due_date_is_supported_by_date_gate(self) -> None:
        option = self.store.get_options(ORDER_ID)["options"][1]
        terms = self.store._option_terms(option)
        self.assertIsNone(self.store._require_late_delivery_consent(
            terms, "2026-11-16T10:00:00+09:00", False, run_id=None,
            option_id=option["id"], trial_id="trial-test"))
        self.assertEqual(max(item["deliveryAt"] for item in terms["allocations"]),
                         "2026-11-16T10:00:00+09:00")

    def test_express_cannot_record_unrelated_late_consent(self) -> None:
        option = self.store.get_options(ORDER_ID)["options"][1]
        before = self.business_state()
        with self.assertRaises(ApiError) as caught:
            self.store.record_customer_response(ORDER_ID, {
                "selectedOptionId": option["id"],
                "acknowledgedTerms": self.store._option_terms(option),
                "acceptedSplit": True, "acceptedExpressCost": True,
                "acceptedLateDelivery": True, "expectedVersion": 2,
                "operationId": str(uuid4()), "actorRole": "order_clerk"})
        self.assertEqual((caught.exception.status, caught.exception.code),
                         (422, "unrelated_late_delivery_consent"))
        self.assertEqual(self.business_state(), before)

    def test_fixed_express_keeps_existing_response_payload(self) -> None:
        option = self.store.get_options(ORDER_ID)["options"][1]
        response = self.store.record_customer_response(ORDER_ID, {
            "selectedOptionId": option["id"],
            "acknowledgedTerms": self.store._option_terms(option),
            "acceptedSplit": True, "acceptedExpressCost": True,
            "expectedVersion": 2, "operationId": str(uuid4()),
            "actorRole": "order_clerk"})
        self.assertEqual(response["order"]["status"], "awaiting_approval")
        self.assertIs(response["customerResponse"]["acceptedLateDelivery"], False)
        self.assertIsNone(response["customerResponse"]["lateDeliveryConsent"])

    def agent_normal_draft(self) -> tuple[dict, dict]:
        run, _ = self.store.create_run(
            ORDER_ID, str(uuid4()), route="test-double", model_id="none",
            execution_mode="test_double", request_text=SUPPORTED_REQUEST_TEXT,
            expected_version=2)
        self.assertTrue(self.store.start_run(run["id"]))
        snapshot = self.store.get_detail(ORDER_ID)
        normal = {"id": f"{run['id']}:normal", "runId": run["id"],
                  "additionalCostJpy": 0,
                  "shipments": [
                      {"warehouseId": "warehouse-a", "quantityBoxes": 25,
                       "carrierSourceId": "carrier-a-normal-v5",
                       "deliveryAt": CARRIER_SOURCES["carrier-a-normal-v5"]["earliestDeliveryAt"]},
                      {"warehouseId": "warehouse-b", "quantityBoxes": 15,
                       "carrierSourceId": "carrier-b-normal-v5",
                       "deliveryAt": CARRIER_SOURCES["carrier-b-normal-v5"]["earliestDeliveryAt"]}],
                  "sourceRefs": ["contract-split-v5"]}
        self.assertTrue(self.store.finish_run(run["id"], "completed",
                                              draft={"plan": {"proposals": [normal]}},
                                              source_snapshot=snapshot))
        return run, normal

    def test_agent_express_keeps_existing_response_payload(self) -> None:
        run, _ = self.store.create_run(
            ORDER_ID, str(uuid4()), route="test-double", model_id="none",
            execution_mode="test_double", request_text=SUPPORTED_REQUEST_TEXT,
            expected_version=2)
        self.assertTrue(self.store.start_run(run["id"]))
        snapshot = self.store.get_detail(ORDER_ID)
        express = {"id": f"{run['id']}:express", "runId": run["id"],
                   "additionalCostJpy": 78000,
                   "shipments": [
                       {"warehouseId": "warehouse-a", "quantityBoxes": 25,
                        "carrierSourceId": "carrier-a-normal-v5",
                        "deliveryAt": CARRIER_SOURCES["carrier-a-normal-v5"]["earliestDeliveryAt"]},
                       {"warehouseId": "warehouse-b", "quantityBoxes": 15,
                        "carrierSourceId": "carrier-b-express-v5",
                        "deliveryAt": CARRIER_SOURCES["carrier-b-express-v5"]["earliestDeliveryAt"]}],
                   "sourceRefs": ["contract-split-v5", "carrier-b-express-v5"]}
        self.assertTrue(self.store.finish_run(run["id"], "completed",
                                              draft={"plan": {"proposals": [express]}},
                                              source_snapshot=snapshot))
        response = self.store.record_agent_customer_response(ORDER_ID, {
            "runId": run["id"], "selectedOptionId": express["id"],
            "acknowledgedTerms": self.store._agent_terms(express),
            "acceptedSplit": True, "acceptedExpressCost": True,
            "expectedVersion": 2, "operationId": str(uuid4()),
            "actorRole": "order_clerk"})
        self.assertEqual(response["order"]["status"], "awaiting_approval")
        self.assertIs(response["customerResponse"]["acceptedLateDelivery"], False)
        self.assertIsNone(response["customerResponse"]["lateDeliveryConsent"])

    def test_late_consent_saves_original_due_and_bob_rechecks_before_shipping(self) -> None:
        run, normal = self.agent_normal_draft()
        response = self.store.record_agent_customer_response(ORDER_ID, {
            "runId": run["id"], "selectedOptionId": normal["id"],
            "acknowledgedTerms": self.store._agent_terms(normal),
            "acceptedSplit": True, "acceptedExpressCost": False,
            "acceptedLateDelivery": True, "expectedVersion": 2,
            "operationId": str(uuid4()), "actorRole": "order_clerk"})
        consent = response["customerResponse"]["lateDeliveryConsent"]
        self.assertEqual(response["order"]["dueAt"], "2026-11-16T10:00:00+09:00")
        self.assertEqual((consent["requestedDueAt"], consent["delayedDeliveryAt"]),
                         ("2026-11-16T10:00:00+09:00", "2026-11-17T10:00:00+09:00"))
        self.assertEqual((consent["runId"], consent["optionId"], consent["trialId"]),
                         (run["id"], normal["id"], response["order"]["currentTrialId"]))
        decision = {"runId": run["id"], "optionId": normal["id"],
                    "reason": "合成顧客の納期変更同意と資料を確認",
                    "expectedVersion": response["order"]["version"],
                    "operationId": str(uuid4()), "actorRole": "shipping_manager",
                    "authenticatedSubject": "synthetic-bob", "authenticatedIssuer": "synthetic",
                    "authenticatedVirtualUserId": "virtual-bob"}
        approved = self.store.decide_agent_proposal(ORDER_ID, decision)
        self.assertEqual(approved["order"]["dueAt"], "2026-11-16T10:00:00+09:00")
        self.assertEqual(approved["order"]["status"], "shipment_instructed")
        self.assertEqual([(item["warehouseId"], item["deliveryAt"])
                          for item in approved["shipment"]["instructions"]], [
                              ("warehouse-a", "2026-11-16T10:00:00+09:00"),
                              ("warehouse-b", "2026-11-17T10:00:00+09:00")])
        self.assertEqual(self.business_state()["stock"], {"warehouse-a": 0, "warehouse-b": 5})

    def test_late_consent_does_not_replace_split_consent(self) -> None:
        run, normal = self.agent_normal_draft()
        response = self.store.record_agent_customer_response(ORDER_ID, {
            "runId": run["id"], "selectedOptionId": normal["id"],
            "acknowledgedTerms": self.store._agent_terms(normal),
            "acceptedSplit": False, "acceptedExpressCost": False,
            "acceptedLateDelivery": True, "expectedVersion": 2,
            "operationId": str(uuid4()), "actorRole": "order_clerk"})
        before = self.business_state()
        with self.assertRaises(ApiError) as caught:
            self.store.decide_agent_proposal(ORDER_ID, {
                "runId": run["id"], "optionId": normal["id"],
                "reason": "分納承諾がない場合の確認",
                "expectedVersion": response["order"]["version"],
                "operationId": str(uuid4()), "actorRole": "shipping_manager",
                "authenticatedSubject": "synthetic-bob", "authenticatedIssuer": "synthetic",
                "authenticatedVirtualUserId": "virtual-bob"})
        self.assertEqual((caught.exception.status, caught.exception.code),
                         (422, "consent_required"))
        self.assertEqual(self.business_state(), before)

    def test_tampered_saved_consent_stops_bob_without_business_write(self) -> None:
        run, normal = self.agent_normal_draft()
        response = self.store.record_agent_customer_response(ORDER_ID, {
            "runId": run["id"], "selectedOptionId": normal["id"],
            "acknowledgedTerms": self.store._agent_terms(normal),
            "acceptedSplit": True, "acceptedExpressCost": False,
            "acceptedLateDelivery": True, "expectedVersion": 2,
            "operationId": str(uuid4()), "actorRole": "order_clerk"})
        with sqlite3.connect(self.db) as connection:
            data = response["customerResponse"]["lateDeliveryConsent"]
            data["delayedDeliveryAt"] = "2026-11-16T10:00:00+09:00"
            connection.execute("UPDATE customer_responses SET late_delivery_consent_json=? WHERE order_id=?",
                               (json.dumps(data, sort_keys=True, separators=(",", ":")), ORDER_ID))
        before = self.business_state()
        with self.assertRaises(ApiError) as caught:
            self.store.decide_agent_proposal(ORDER_ID, {
                "runId": run["id"], "optionId": normal["id"], "reason": "承認試験",
                "expectedVersion": response["order"]["version"],
                "operationId": str(uuid4()), "actorRole": "shipping_manager",
                "authenticatedSubject": "synthetic-bob", "authenticatedIssuer": "synthetic",
                "authenticatedVirtualUserId": "virtual-bob"})
        self.assertEqual((caught.exception.status, caught.exception.code),
                         (422, "late_delivery_consent_mismatch"))
        self.assertEqual(self.business_state(), before)

    def test_other_run_cannot_use_saved_late_consent(self) -> None:
        old_run, old_normal = self.agent_normal_draft()
        run, normal = self.agent_normal_draft()
        response = self.store.record_agent_customer_response(ORDER_ID, {
            "runId": run["id"], "selectedOptionId": normal["id"],
            "acknowledgedTerms": self.store._agent_terms(normal),
            "acceptedSplit": True, "acceptedExpressCost": False,
            "acceptedLateDelivery": True, "expectedVersion": 2,
            "operationId": str(uuid4()), "actorRole": "order_clerk"})
        before = self.business_state()
        with self.assertRaises(ApiError) as caught:
            self.store.decide_agent_proposal(ORDER_ID, {
                "runId": old_run["id"], "optionId": old_normal["id"],
                "reason": "旧runからの承認試験", "expectedVersion": response["order"]["version"],
                "operationId": str(uuid4()), "actorRole": "shipping_manager",
                "authenticatedSubject": "synthetic-bob", "authenticatedIssuer": "synthetic",
                "authenticatedVirtualUserId": "virtual-bob"})
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "stale_run"))
        self.assertEqual(self.business_state(), before)
        with self.assertRaises(ApiError) as caught:
            self.store.record_agent_customer_response(ORDER_ID, {
                "runId": old_run["id"], "selectedOptionId": old_normal["id"],
                "acknowledgedTerms": self.store._agent_terms(old_normal),
                "acceptedSplit": True, "acceptedExpressCost": False,
                "acceptedLateDelivery": True, "expectedVersion": response["order"]["version"],
                "operationId": str(uuid4()), "actorRole": "order_clerk"})
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "stale_run"))
        self.assertEqual(self.business_state(), before)


if __name__ == "__main__":
    unittest.main()
