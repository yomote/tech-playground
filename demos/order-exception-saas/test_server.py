"""Contract tests for the synthetic order exception API and its side effects."""

from __future__ import annotations

from contextlib import closing
import asyncio
from http.client import HTTPConnection
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
from types import SimpleNamespace
import unittest
from uuid import uuid4

from server import ORDER_ID, OrderStore, RunController, make_server


def _reply(port: int, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    connection.request(method, path, _encode(body).encode("utf-8") if body is not None else None, headers)
    response = connection.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    connection.close()
    return response.status, payload


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


class OrderApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "orders.sqlite3"
        self.server = make_server(0, self.db_path)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def detail(self) -> dict:
        status, value = _reply(self.port, "GET", f"/api/orders/{ORDER_ID}")
        self.assertEqual(status, 200)
        return value

    def response_body(self, expected_version: int = 1, accepted: bool = True) -> dict:
        return {
            "acceptedSplit": accepted, "note": "顧客に電話確認済み",
            "expectedVersion": expected_version, "operationId": str(uuid4()),
            "actorRole": "order_clerk",
        }

    def decision_body(self, expected_version: int = 1, option_id: str = "split-delivery") -> dict:
        return {
            "optionId": option_id, "reason": "顧客の回答と納品日を確認した",
            "expectedVersion": expected_version, "operationId": str(uuid4()),
            "actorRole": "shipping_manager",
        }

    def test_initial_fixture_and_options_are_not_ai_output(self) -> None:
        status, listed = _reply(self.port, "GET", "/api/orders")
        self.assertEqual(status, 200)
        self.assertEqual(listed["orders"][0]["id"], ORDER_ID)
        self.assertEqual(listed["orders"][0]["status"], "exception_detected")
        detail = self.detail()
        self.assertIsNone(detail["customerResponse"])
        self.assertIsNone(detail["approval"])
        self.assertIsNone(detail["shipment"])
        self.assertEqual(detail["order"]["quantity"], 40)
        status, options = _reply(self.port, "GET", f"/api/orders/{ORDER_ID}/options")
        self.assertEqual(status, 200)
        self.assertEqual({item["id"] for item in options["options"]},
                         {"split-delivery", "express-consolidated"})
        self.assertTrue(all(item["generationMode"] == "fixed_fixture" for item in options["options"]))
        self.assertTrue(all(item["dataMode"] == "synthetic_fixture" for item in options["options"]))

    def test_source_facts_are_versioned_and_do_not_contain_completed_options(self) -> None:
        status, response = _reply(self.port, "GET", f"/api/orders/{ORDER_ID}/sources")
        self.assertEqual(status, 200)
        self.assertNotIn("options", response)
        sources = response["sources"]
        self.assertEqual(sources["order"]["quantityBoxes"], 40)
        self.assertEqual(sources["order"]["orderVersion"], 1)
        self.assertEqual(sum(item["availableBoxes"] for item in sources["inventory"]), 45)
        self.assertTrue(all(item["fixtureVersion"] == "1.0" for item in sources["inventory"]))
        self.assertEqual({item["sourceId"] for item in sources["carriers"]}, {
            "carrier-a-normal-v1", "carrier-b-normal-v1", "carrier-b-express-v1"})
        self.assertEqual(sources["carriers"][2]["additionalCostJpy"], 78000)
        self.assertEqual(sources["contract"]["sourceId"], "contract-split-v1")
        self.assertEqual(sources["contract"]["fixtureVersion"], "1.0")
        available_ids = {sources["order"]["sourceId"], sources["contract"]["sourceId"]}
        available_ids.update(item["sourceId"] for item in sources["inventory"])
        available_ids.update(item["sourceId"] for item in sources["carriers"])
        for option in self.detail()["options"]:
            self.assertTrue(set(option["sourceRefs"]).issubset(available_ids))

    def test_unconnected_model_returns_503_without_saving_a_run(self) -> None:
        status, error = _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/agent-runs",
                               {"operationId": str(uuid4())})
        self.assertEqual((status, error["error"]), (503, "agent_unavailable"))
        status, history = _reply(self.port, "GET", f"/api/orders/{ORDER_ID}/agent-runs")
        self.assertEqual(status, 200)
        self.assertEqual(history["agentRuns"], [])

    def test_restart_recovers_queued_and_running_runs_and_keeps_events(self) -> None:
        store = OrderStore(self.db_path)
        queued, _ = store.create_run(ORDER_ID, str(uuid4()), route="direct",
                                     model_id="scripted-test-model", execution_mode="test_double")
        running, _ = store.create_run(ORDER_ID, str(uuid4()), route="direct",
                                      model_id="scripted-test-model", execution_mode="test_double")
        completed, _ = store.create_run(ORDER_ID, str(uuid4()), route="direct",
                                        model_id="scripted-test-model", execution_mode="test_double")
        self.assertTrue(store.start_run(running["id"]))
        store.append_run_event(running["id"], kind="source_read", actor="DispatchPlanner",
                               source_id="carrier-b-normal-v1", message="合成便を読み取りました。")
        self.assertTrue(store.start_run(completed["id"]))
        self.assertTrue(store.finish_run(completed["id"], "completed", draft={"businessReviewStatus": "unverified"}))

        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = make_server(0, self.db_path, recover_interrupted=True)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

        for run in (queued, running):
            status, saved = _reply(self.port, "GET", f"/api/agent-runs/{run['id']}")
            self.assertEqual(status, 200)
            self.assertEqual(saved["run"]["status"], "failed")
            self.assertEqual(saved["run"]["errorCode"], "server_restarted")
            self.assertIsNotNone(saved["run"]["endedAt"])
        self.assertEqual(OrderStore(self.db_path).get_run(running["id"])["events"][0]["sourceId"],
                         "carrier-b-normal-v1")
        self.assertEqual(OrderStore(self.db_path).get_run(completed["id"])["status"], "completed")
        self.assertEqual(OrderStore(self.db_path).recover_interrupted_runs(), 0)

    def test_split_requires_recorded_customer_consent_and_approver_role(self) -> None:
        status, rejected = _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/decision", self.decision_body())
        self.assertEqual((status, rejected["error"]), (422, "consent_required"))
        body = self.response_body(accepted=False)
        status, saved = _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/customer-response", body)
        self.assertEqual(status, 200)
        self.assertFalse(saved["customerResponse"]["acceptedSplit"])
        status, rejected = _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/decision", self.decision_body(2))
        self.assertEqual((status, rejected["error"]), (422, "consent_required"))
        invalid_role = self.decision_body(2)
        invalid_role["actorRole"] = "order_clerk"
        status, rejected = _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/decision", invalid_role)
        self.assertEqual((status, rejected["error"]), (403, "forbidden"))
        self.assertEqual(self.detail()["order"]["version"], 2)
        self.assertIsNone(self.detail()["shipment"])

    def test_version_conflict_and_operation_id_replay(self) -> None:
        response = self.response_body()
        status, first = _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/customer-response", response)
        self.assertEqual(status, 200)
        self.assertEqual(first["order"]["version"], 2)
        status, replay = _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/customer-response", response)
        self.assertEqual(status, 200)
        self.assertEqual(first, replay)
        stale = self.response_body(expected_version=1)
        status, error = _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/customer-response", stale)
        self.assertEqual((status, error["error"]), (409, "version_conflict"))
        changed = dict(response, note="別の回答")
        status, error = _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/customer-response", changed)
        self.assertEqual((status, error["error"]), (409, "operation_conflict"))
        self.assertEqual(self.detail()["order"]["version"], 2)

    def test_approval_atomically_saves_reservations_and_instructions(self) -> None:
        status, _ = _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/customer-response", self.response_body())
        self.assertEqual(status, 200)
        decision = self.decision_body(expected_version=2)
        status, approved = _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/decision", decision)
        self.assertEqual(status, 200)
        self.assertEqual(approved["order"]["status"], "shipment_instructed")
        self.assertEqual(approved["order"]["version"], 3)
        self.assertEqual(approved["approval"]["authorizationMode"], "declared_role_fixture")
        self.assertEqual(sum(x["quantity"] for x in approved["shipment"]["reservations"]), 40)
        self.assertEqual(sum(x["quantity"] for x in approved["shipment"]["instructions"]), 40)
        self.assertEqual(approved["inventory"], [
            {"warehouseId": "warehouse-a", "available": 0},
            {"warehouseId": "warehouse-b", "available": 5},
        ])
        status, replay = _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/decision", decision)
        self.assertEqual(status, 200)
        self.assertEqual(replay, approved)
        self.assertEqual(self.detail()["inventory"], approved["inventory"])
        self.assertEqual(OrderStore(self.db_path).get_detail(ORDER_ID)["shipment"], approved["shipment"])
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reservations").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM shipment_instructions").fetchone()[0], 2)

    def test_inventory_recheck_rolls_back_decision(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            with connection:
                connection.execute("UPDATE inventory SET available = 10 WHERE warehouse_id = 'warehouse-b'")
        status, error = _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/decision",
                               self.decision_body(option_id="express-consolidated"))
        self.assertEqual((status, error["error"]), (409, "insufficient_inventory"))
        detail = self.detail()
        self.assertEqual(detail["order"]["status"], "exception_detected")
        self.assertIsNone(detail["approval"])
        self.assertIsNone(detail["shipment"])
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reservations").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM shipment_instructions").fetchone()[0], 0)

    def test_express_option_can_be_approved_without_split_response(self) -> None:
        status, approved = _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/decision",
                                  self.decision_body(option_id="express-consolidated"))
        self.assertEqual(status, 200)
        self.assertEqual(approved["approval"]["optionId"], "express-consolidated")
        self.assertIsNone(approved["customerResponse"])
        self.assertEqual(approved["order"]["nextActor"], "倉庫担当者")


class OrderRunApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "runs.sqlite3"
        self.started = Event()
        self.release = Event()
        self.fail = False

        async def scripted_runner(snapshot: dict, _config: object, *, run_id: str,
                                  on_event: object) -> dict:
            on_event(kind="manager_selection", actor="ShipmentManager",
                     message="DispatchPlannerを選択しました。")
            on_event(kind="source_read", actor="DispatchPlanner",
                     source_id="carrier-b-express-v1", message="合成便を読み取りました。")
            self.started.set()
            await asyncio.to_thread(self.release.wait, 2)
            if self.fail:
                error = RuntimeError("provider raw text must not be returned")
                error.code = "provider_timeout"
                raise error
            return {
                "runId": run_id, "orderId": snapshot["order"]["id"],
                "generationMode": "test_double", "shipmentChanged": False,
                "requiresHumanApproval": True, "draftBody": "テスト用の案",
                "draftPlan": {
                    "recommendation": "分納案を確認する", "alternatives": "専用便案を比較する",
                    "customerQuestion": "分納を承諾できますか。",
                },
                "contractReviewStatus": "not_evaluated",
                "sourceReads": [{"sourceId": "carrier-b-express-v1"}],
                "managerEvents": [{"nextSpeaker": "DispatchPlanner"}],
            }

        config = SimpleNamespace(route="direct", model_id="scripted-test-model")
        self.server = make_server(0, self.db_path, runs_factory=lambda store: RunController(
            store, config_loader=lambda: config, runner=scripted_runner,
            execution_mode="test_double", timeout_seconds=3,
        ))
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def _start(self, operation_id: str | None = None) -> tuple[int, dict]:
        return _reply(self.port, "POST", f"/api/orders/{ORDER_ID}/agent-runs",
                      {"operationId": operation_id or str(uuid4())})

    def _until(self, run_id: str, expected: str) -> dict:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, response = _reply(self.port, "GET", f"/api/agent-runs/{run_id}")
            self.assertEqual(status, 200)
            if response["run"]["status"] == expected:
                return response["run"]
            time.sleep(0.01)
        self.fail(f"run did not reach {expected}")

    def test_start_events_completion_replay_and_reopen(self) -> None:
        operation_id = str(uuid4())
        status, response = self._start(operation_id)
        self.assertEqual(status, 200)
        run_id = response["run"]["id"]
        self.assertEqual(response["run"]["executionMode"], "test_double")
        self.assertTrue(self.started.wait(2))
        running = self._until(run_id, "running")
        self.assertEqual([event["kind"] for event in running["events"]],
                         ["status", "manager_selection", "source_read"])
        status, replay = self._start(operation_id)
        self.assertEqual(status, 200)
        self.assertEqual(replay["run"]["id"], run_id)
        self.release.set()
        completed = self._until(run_id, "completed")
        self.assertEqual(completed["draft"]["businessReviewStatus"], "unverified")
        self.assertEqual(completed["draft"]["contractReviewStatus"], "not_evaluated")
        self.assertEqual(completed["draft"]["generationMode"], "test_double")
        self.assertEqual(completed["draft"]["plan"]["customerQuestion"], "分納を承諾できますか。")
        self.assertIsNone(OrderStore(self.db_path).get_detail(ORDER_ID)["shipment"])
        self.assertEqual(OrderStore(self.db_path).get_run(run_id)["draft"], completed["draft"])
        status, history = _reply(self.port, "GET", f"/api/orders/{ORDER_ID}/agent-runs")
        self.assertEqual(status, 200)
        self.assertEqual(len(history["agentRuns"]), 1)
        self.assertEqual(history["agentRuns"][0]["id"], run_id)

    def test_failure_is_persisted_without_provider_text(self) -> None:
        self.fail = True
        status, response = self._start()
        self.assertEqual(status, 200)
        run_id = response["run"]["id"]
        self.assertTrue(self.started.wait(2))
        self.release.set()
        failed = self._until(run_id, "failed")
        self.assertEqual(failed["errorCode"], "provider_timeout")
        self.assertNotIn("draft", failed)
        self.assertNotIn("provider raw text", json.dumps(failed))
        self.assertEqual(OrderStore(self.db_path).get_run(run_id)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
