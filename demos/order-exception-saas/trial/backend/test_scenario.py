"""Focused checks for the restartable synthetic due-date scenario.

Agent runs in this file use a local test double, never a model or a user identity.
"""

from __future__ import annotations

from copy import deepcopy
from contextlib import closing
from datetime import datetime
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from uuid import uuid4

from draft_gate import expected_source_refs_for_proposal, validate_structured_draft
from server import ORDER_ID, OrderStore, SUPPORTED_REQUEST_TEXT, make_server
from source_fixtures import CARRIER_SOURCES, CONTRACT_SOURCE, FIXTURE_ID, source_facts_sha256


class ScenarioTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "order.sqlite3"
        self.validity = patch.object(OrderStore, "fixture_validity", return_value={
            "newRunAvailable": True, "fixtureId": FIXTURE_ID,
        })
        self.validity.start()
        self.addCleanup(self.validity.stop)

    def assert_start_rejected_without_db_change(self, code: str,
                                                env: dict[str, str] | None = None) -> None:
        before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.db_path)],
            cwd=Path(__file__).resolve().parent,
            env=env, capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("synthetic_fixture_mismatch", result.stderr)
        self.assertEqual(hashlib.sha256(self.db_path.read_bytes()).hexdigest(), before)

    def test_fixture_restart_and_domain_count_readback(self) -> None:
        store = OrderStore(self.db_path)
        initial = store.get_detail(ORDER_ID)
        self.assertEqual((initial["order"]["fixtureId"], initial["order"]["version"],
                          initial["order"]["dueAt"]),
                         (FIXTURE_ID, 2, "2026-11-17T10:00:00+09:00"))
        original_trial = initial["order"]["currentTrialId"]
        self.assertEqual(CARRIER_SOURCES["carrier-a-normal-v3"]["earliestDeliveryAt"],
                         "2026-11-16T10:00:00+09:00")
        self.assertEqual(CARRIER_SOURCES["carrier-b-normal-v3"]["earliestDeliveryAt"],
                         initial["order"]["dueAt"])
        self.assertEqual(CARRIER_SOURCES["carrier-b-express-v3"]["earliestDeliveryAt"],
                         "2026-11-16T10:00:00+09:00")
        with closing(sqlite3.connect(self.db_path)) as connection:
            saved_digest = connection.execute(
                "SELECT source_facts_sha256 FROM fixture_meta WHERE singleton = 1"
            ).fetchone()[0]
        self.assertEqual(saved_digest, source_facts_sha256())

        run, created = store.create_run(
            ORDER_ID, str(uuid4()), route="local-test-double", model_id="no-model",
            execution_mode="test_double", request_text=SUPPORTED_REQUEST_TEXT,
            expected_version=2,
        )
        self.assertTrue(created)
        self.assertTrue(store.start_run(run["id"]))
        sources = store.get_sources(ORDER_ID)["sources"]
        order_source = sources["order"]
        stock_sources = sources["inventory"]
        order_ids = [order_source["sourceId"], *(item["sourceId"] for item in stock_sources)]
        order_tool = {
            "dataMode": "synthetic_fixture", "orderSourceId": order_ids[0],
            "orderVersion": 2, "quantityBoxes": 40, "dueAt": order_source["dueAt"],
            "warehouses": [
                {"sourceId": item["sourceId"], "warehouseId": item["warehouseId"],
                 "availableBoxes": item["availableBoxes"]} for item in stock_sources
            ],
        }
        carrier_ids = [*CARRIER_SOURCES, CONTRACT_SOURCE["sourceId"]]
        for name, payload, ids in (
                ("read_order_inventory", order_tool, order_ids),
                ("read_carriers_contract", {
                    "dataMode": "synthetic_fixture", "carriers": list(CARRIER_SOURCES.values()),
                    "contract": CONTRACT_SOURCE,
                }, carrier_ids)):
            response_text = json.dumps(payload, ensure_ascii=False)
            store.capture_tool_response(
                run["id"], tool_name=name, response_text=response_text,
                response_sha256=hashlib.sha256(response_text.encode()).hexdigest(),
                source_ids=ids, fixture_version="3.0",
            )
        count = store.record_inventory_count(ORDER_ID, {
            "operationId": str(uuid4()), "runId": run["id"], "expectedVersion": 2,
            "warehouseId": "warehouse-b", "expectedAvailableBoxes": 20,
            "availableBoxes": 19, "reason": "合成検査の数量補正",
        }, {"virtualUserId": "synthetic-test-double"})
        self.assertEqual(count["orderVersion"], 3)

        store = OrderStore(self.db_path)
        saved = store.get_detail(ORDER_ID)
        self.assertEqual(saved["order"]["version"], 3)
        self.assertEqual(saved["order"]["currentTrialId"], original_trial)
        self.assertEqual(saved["inventory"][1]["available"], 19)
        self.assertEqual(store.get_sources(ORDER_ID)["sources"]["order"]["sourceId"],
                         "order-SO-24017-v3")
        self.assertIsNone(saved["shipment"])
        server = make_server(0, self.db_path)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with closing(HTTPConnection("127.0.0.1", server.server_port, timeout=3)) as client:
                client.request("GET", "/api/health")
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                health = json.loads(response.read())
            self.assertEqual(health["fixtureId"], FIXTURE_ID)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_changed_scenario_or_source_facts_reject_same_db(self) -> None:
        OrderStore(self.db_path)
        for label, change in (
                ("fixture-id", {"fixtureId": "oe02-other-fixture"}),
                ("due-at", {"orderDueAt": "2026-11-18T10:00:00+09:00"}),
                ("initial-version", {"initialOrderVersion": 3})):
            config = Path(self.temp.name) / f"{label}.json"
            config.write_text(json.dumps({
                "fixtureId": FIXTURE_ID, "orderDueAt": "2026-11-17T10:00:00+09:00",
                "initialOrderVersion": 2, **change,
            }), encoding="utf-8")
            self.assert_start_rejected_without_db_change(
                "from server import OrderStore; import sys; OrderStore(sys.argv[1])",
                {**os.environ, "OE02_SCENARIO_PATH": str(config)},
            )
        for mutation in (
                "s.CARRIER_SOURCES['carrier-b-normal-v3']['additionalCostJpy'] = 1",
                "s.CONTRACT_SOURCE['clause'] = '別の合成条項'",
        ):
            self.assert_start_rejected_without_db_change(
                f"import source_fixtures as s; {mutation}; "
                "from server import OrderStore; import sys; OrderStore(sys.argv[1])",
            )

    def test_previous_fixture_meta_schema_rejected_without_migration(self) -> None:
        OrderStore(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("ALTER TABLE fixture_meta DROP COLUMN source_facts_sha256")
            connection.commit()
        self.assert_start_rejected_without_db_change(
            "from server import OrderStore; import sys; OrderStore(sys.argv[1])"
        )

    def test_partial_database_with_only_fixture_meta_is_rejected(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("CREATE TABLE fixture_meta (singleton INTEGER PRIMARY KEY, fixture_id TEXT)")
            connection.commit()
        self.assert_start_rejected_without_db_change(
            "from server import OrderStore; import sys; OrderStore(sys.argv[1])"
        )

    def test_draft_limitation_uses_carrier_arrival_and_saved_due(self) -> None:
        snapshot = OrderStore(self.db_path).get_detail(ORDER_ID)
        version = snapshot["order"]["version"]
        read_ids = [
            f"order-{ORDER_ID}-v{version}",
            f"stock-warehouse-a-snapshot-order-v{version}",
            f"stock-warehouse-b-snapshot-order-v{version}",
            *CARRIER_SOURCES, CONTRACT_SOURCE["sourceId"],
        ]
        read_events = [{"kind": "source_read", "sourceId": source_id} for source_id in read_ids]

        def proposal(b_carrier: str) -> dict:
            cost = CARRIER_SOURCES[b_carrier]["additionalCostJpy"]
            value = {
                "deliveryPattern": "separate_warehouse_shipments",
                "shipments": [
                    {"warehouseId": "warehouse-a", "quantityBoxes": 25,
                     "carrierSourceId": "carrier-a-normal-v3",
                     "deliveryAt": CARRIER_SOURCES["carrier-a-normal-v3"]["earliestDeliveryAt"]},
                    {"warehouseId": "warehouse-b", "quantityBoxes": 15,
                     "carrierSourceId": b_carrier,
                     "deliveryAt": CARRIER_SOURCES[b_carrier]["earliestDeliveryAt"]},
                ],
                "additionalCostJpy": cost,
                "customerDecisionFields": ["acceptedSplit"] + (["acceptedExpressCost"] if cost else []),
            }
            value["sourceRefs"] = expected_source_refs_for_proposal(snapshot, value)
            return value

        plan = {"proposals": [proposal("carrier-b-normal-v3"),
                              proposal("carrier-b-express-v3")]}

        class SyntheticTrialTime(datetime):
            @classmethod
            def now(cls, tz=None):
                observed = datetime.fromisoformat("2026-11-09T00:00:00+09:00")
                return observed.astimezone(tz) if tz else observed

        with patch("draft_gate.datetime", SyntheticTrialTime):
            checked = validate_structured_draft(snapshot, plan, read_events)
            self.assertIn("希望日時と同時刻", checked["limitations"][0])
            earlier_due = deepcopy(snapshot)
            earlier_due["order"]["dueAt"] = "2026-11-16T10:00:00+09:00"
            earlier = validate_structured_draft(earlier_due, plan, read_events)
            self.assertIn("希望日時より後", earlier["limitations"][0])


if __name__ == "__main__":
    unittest.main()
