"""Loopback-only synthetic order exception API.

The fixed options are fixtures, not AI output. The role field is a local trial
stand-in and must not be treated as authentication or an OpenFGA decision.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from contextlib import closing
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Thread, Timer
from typing import Any, Callable
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

from draft_gate import DraftGroundingError, validate_structured_draft
from order_agent import AGENT_VERSION
from source_fixtures import (CARRIER_SOURCES, CONTRACT_SOURCE, FIXTURE_ID,
                             FIXTURE_VERSION, INITIAL_ORDER_VERSION,
                             ORDER_DUE_AT, SCENARIO_TIME, source_facts_sha256)
from inventory_auth import InventoryAccessError, InventoryAuthorizer


ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
DEFAULT_DB = ROOT / ".runtime" / "orders.sqlite3"
ORDER_ID = "SO-24017"
INITIAL_INVENTORY = {"warehouse-a": 25, "warehouse-b": 20}
_SYNTHETIC_REF = re.compile(
    r"(?:order-SO-24017-v[0-9]{1,6}|"
    r"stock-warehouse-[ab]-snapshot-order-v[0-9]{1,6}|"
    r"carrier-[ab]-(?:normal|express)-v[0-9]{1,6}|"
    r"contract-split-v[0-9]{1,6})\Z"
)
# Historical synthetic clause interpretation. Keep old versions reviewable
# when the current fixture changes; added fields or new terms require a new rule.
HISTORICAL_NO_PAYER_CONTRACT_V3 = {
    "sourceId": "contract-split-v3", "version": "3.0",
    "dataMode": "synthetic_fixture",
    "clause": "分納には顧客の承諾が必要です。",
    "scope": "合成受注SO-24017の分納条件",
}
SUPPORTED_REQUEST_TEXT = (
    "合成受注40箱の不足15箱を調べ、在庫・便・契約に根拠がある別送案を示してください。"
    "顧客回答と出荷承認は待ってください。"
)
MAX_MODEL_FINAL_REPLY_BYTES = 8192
_SECRET_IN_REPLY = re.compile(
    r"(?i)(?:\bsk-[A-Za-z0-9_-]{12,}\b|\bBearer\s+\S{12,}|"
    r"\b(?:api[_ -]?key|authorization)\s*[:=]\s*\S{8,})"
)
OPTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "split-delivery",
        "title": "倉庫Aの25箱は11月16日、倉庫Bの15箱は11月17日に納品",
        "summary": "倉庫Aの25箱は11月16日、倉庫Bの15箱は11月17日に到着予定の固定比較案です。分納には顧客の承諾が必要です。",
        "allocations": [
            {"warehouseId": "warehouse-a", "quantity": 25,
             "deliveryAt": CARRIER_SOURCES["carrier-a-normal-v3"]["earliestDeliveryAt"]},
            {"warehouseId": "warehouse-b", "quantity": 15,
             "deliveryAt": CARRIER_SOURCES["carrier-b-normal-v3"]["earliestDeliveryAt"]},
        ],
        "additionalCostJpy": 0,
        "requiresCustomerConsent": True,
        "customerConsentField": "acceptedSplit",
        "sourceRefs": ["stock-warehouse-a", "stock-warehouse-b",
                       "carrier-a-normal-v3", "carrier-b-normal-v3", "contract-split-v3"],
        "generationMode": "fixed_fixture",
        "dataMode": "synthetic_fixture",
    },
    {
        "id": "express-consolidated",
        "title": "一括輸送を想定した固定比較案（集約根拠なし）",
        "summary": "倉庫A/Bそれぞれの便資料はありますが、荷物を一つの便へ集約する根拠はありません。承認不可の固定比較案です。",
        "allocations": [
            {"warehouseId": "warehouse-a", "quantity": 25,
             "deliveryAt": CARRIER_SOURCES["carrier-a-normal-v3"]["earliestDeliveryAt"]},
            {"warehouseId": "warehouse-b", "quantity": 15,
             "deliveryAt": CARRIER_SOURCES["carrier-b-express-v3"]["earliestDeliveryAt"]},
        ],
        "additionalCostJpy": 78000,
        "requiresCustomerConsent": True,
        "customerConsentField": "acceptedExpressCost",
        "customerConsentMeaning": "合成顧客による特急便利用と追加費用発生の確認。費用負担先の確定ではありません。",
        "approvalAvailability": "blocked_missing_consolidation_source",
        "sourceRefs": ["stock-warehouse-a", "stock-warehouse-b",
                       "carrier-a-normal-v3", "carrier-b-express-v3", "contract-split-v3"],
        "generationMode": "fixed_fixture",
        "dataMode": "synthetic_fixture",
    },
)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _valid_model_final_reply(value: object) -> bool:
    """Keep the received prose verbatim only when it fits the saved-view boundary."""
    return (isinstance(value, str) and bool(value.strip()) and
            len(value.encode("utf-8")) <= MAX_MODEL_FINAL_REPLY_BYTES and
            not any(ord(character) < 32 and character not in "\n\r\t" or ord(character) == 127
                    for character in value) and
            _SECRET_IN_REPLY.search(value) is None)


def _operation_id(value: object) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "validation_error", "operationId はUUIDにしてください。")
    try:
        UUID(value)
    except ValueError:
        raise ApiError(400, "validation_error", "operationId はUUIDにしてください。") from None
    return value


def _expected_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ApiError(400, "validation_error", "expectedVersion は1以上の整数にしてください。")
    return value


class OrderStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            # A previous dated fixture has different order terms. Refuse it
            # before schema migration or seed writes; the owner must select a
            # fresh DB for the new synthetic source set.
            existing_tables = {item[0] for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            if existing_tables and "orders" not in existing_tables:
                raise RuntimeError("synthetic_fixture_mismatch: use a fresh DB for this fixture version")
            if "orders" in existing_tables:
                old_order = connection.execute(
                    "SELECT due_at FROM orders WHERE id = ?", (ORDER_ID,)).fetchone()
                has_fixture_meta = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fixture_meta'").fetchone()
                meta_columns = ({item[1] for item in connection.execute("PRAGMA table_info(fixture_meta)")}
                                if has_fixture_meta else set())
                if has_fixture_meta and "source_facts_sha256" not in meta_columns:
                    raise RuntimeError("synthetic_fixture_mismatch: use a fresh DB for this fixture version")
                meta = (connection.execute(
                    "SELECT fixture_id, due_at, initial_order_version, source_facts_sha256 FROM fixture_meta "
                    "WHERE singleton = 1").fetchone() if has_fixture_meta else None)
                if (old_order is None or old_order["due_at"] != ORDER_DUE_AT or
                        meta is None or meta["fixture_id"] != FIXTURE_ID or
                        meta["due_at"] != ORDER_DUE_AT or
                        meta["initial_order_version"] != INITIAL_ORDER_VERSION or
                        meta["source_facts_sha256"] != source_facts_sha256()):
                    raise RuntimeError("synthetic_fixture_mismatch: use a fresh DB for this fixture version")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS fixture_meta (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    fixture_id TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    initial_order_version INTEGER NOT NULL,
                    source_facts_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY, customer_name TEXT NOT NULL,
                    item_name TEXT NOT NULL, sku TEXT NOT NULL,
                    quantity INTEGER NOT NULL, due_at TEXT NOT NULL,
                    status TEXT NOT NULL, version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inventory (
                    warehouse_id TEXT PRIMARY KEY, available INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS customer_responses (
                    order_id TEXT PRIMARY KEY REFERENCES orders(id),
                    run_id TEXT,
                    selected_option_id TEXT,
                    acknowledged_terms_json TEXT,
                    accepted_split INTEGER NOT NULL,
                    accepted_express_cost INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    order_id TEXT PRIMARY KEY REFERENCES orders(id),
                    run_id TEXT,
                    option_id TEXT NOT NULL, reason TEXT NOT NULL,
                    actor_role TEXT NOT NULL, approved_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reservations (
                    order_id TEXT NOT NULL REFERENCES orders(id),
                    warehouse_id TEXT NOT NULL REFERENCES inventory(warehouse_id),
                    quantity INTEGER NOT NULL,
                    PRIMARY KEY (order_id, warehouse_id)
                );
                CREATE TABLE IF NOT EXISTS shipment_instructions (
                    order_id TEXT NOT NULL REFERENCES orders(id),
                    warehouse_id TEXT NOT NULL, quantity INTEGER NOT NULL,
                    delivery_at TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY (order_id, warehouse_id)
                );
                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                    operation_type TEXT NOT NULL, request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL REFERENCES orders(id),
                    operation_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    agent_version TEXT NOT NULL,
                    execution_mode TEXT NOT NULL,
                    route TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    draft_json TEXT,
                    model_final_reply TEXT,
                    error_code TEXT,
                    source_snapshot_json TEXT,
                    request_text TEXT,
                    request_json TEXT,
                    UNIQUE (order_id, operation_id)
                );
                CREATE TABLE IF NOT EXISTS agent_run_events (
                    run_id TEXT NOT NULL REFERENCES agent_runs(id),
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    message TEXT NOT NULL,
                    source_id TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS agent_run_tool_responses (
                    run_id TEXT NOT NULL REFERENCES agent_runs(id),
                    tool_name TEXT NOT NULL,
                    response_text TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    fixture_version TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, tool_name)
                );
                CREATE TABLE IF NOT EXISTS agent_run_source_ref_attempts (
                    run_id TEXT NOT NULL REFERENCES agent_runs(id),
                    attempt_no INTEGER NOT NULL,
                    proposal_index INTEGER NOT NULL,
                    submitted_refs_json TEXT NOT NULL,
                    submitted_shape TEXT NOT NULL,
                    duplicate_refs_json TEXT NOT NULL,
                    expected_refs_json TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    error_code TEXT,
                    field_path TEXT,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, attempt_no, proposal_index)
                );
                CREATE TABLE IF NOT EXISTS trial_archives (
                    id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL REFERENCES orders(id),
                    trial_id TEXT NOT NULL UNIQUE,
                    archived_at TEXT NOT NULL,
                    outcome_json TEXT NOT NULL,
                    run_ids_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_review_requests (
                    id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL REFERENCES orders(id),
                    trial_id TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id),
                    option_id TEXT NOT NULL,
                    requested_by_role TEXT NOT NULL,
                    assigned_to_role TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    source_refs_json TEXT NOT NULL,
                    source_fixture_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    UNIQUE (order_id, trial_id, run_id, option_id)
                );
                CREATE TABLE IF NOT EXISTS source_review_responses (
                    review_id TEXT PRIMARY KEY REFERENCES source_review_requests(id),
                    order_id TEXT NOT NULL REFERENCES orders(id),
                    trial_id TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id),
                    option_id TEXT NOT NULL,
                    actor_role TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    responded_at TEXT NOT NULL
                );
            """)
            order_columns = {item[1] for item in connection.execute("PRAGMA table_info(orders)")}
            if "current_trial_id" not in order_columns:
                connection.execute("ALTER TABLE orders ADD COLUMN current_trial_id TEXT")
            customer_columns = {item[1] for item in connection.execute("PRAGMA table_info(customer_responses)")}
            if "selected_option_id" not in customer_columns:
                connection.execute("ALTER TABLE customer_responses ADD COLUMN selected_option_id TEXT")
            if "acknowledged_terms_json" not in customer_columns:
                connection.execute("ALTER TABLE customer_responses ADD COLUMN acknowledged_terms_json TEXT")
            if "accepted_express_cost" not in customer_columns:
                connection.execute(
                    "ALTER TABLE customer_responses ADD COLUMN accepted_express_cost INTEGER NOT NULL DEFAULT 0"
                )
            if "run_id" not in customer_columns:
                connection.execute("ALTER TABLE customer_responses ADD COLUMN run_id TEXT")
            approval_columns = {item[1] for item in connection.execute("PRAGMA table_info(approvals)")}
            if "run_id" not in approval_columns:
                connection.execute("ALTER TABLE approvals ADD COLUMN run_id TEXT")
            run_columns = {item[1] for item in connection.execute("PRAGMA table_info(agent_runs)")}
            if "source_snapshot_json" not in run_columns:
                connection.execute("ALTER TABLE agent_runs ADD COLUMN source_snapshot_json TEXT")
            if "request_text" not in run_columns:
                connection.execute("ALTER TABLE agent_runs ADD COLUMN request_text TEXT")
            if "request_json" not in run_columns:
                connection.execute("ALTER TABLE agent_runs ADD COLUMN request_json TEXT")
            if "trial_id" not in run_columns:
                connection.execute("ALTER TABLE agent_runs ADD COLUMN trial_id TEXT")
            if "model_final_reply" not in run_columns:
                connection.execute("ALTER TABLE agent_runs ADD COLUMN model_final_reply TEXT")
            operation_columns = {item[1] for item in connection.execute("PRAGMA table_info(operations)")}
            if "trial_id" not in operation_columns:
                connection.execute("ALTER TABLE operations ADD COLUMN trial_id TEXT")
            review_columns = {item[1] for item in connection.execute("PRAGMA table_info(source_review_requests)")}
            if "source_fixture_version" not in review_columns:
                connection.execute("ALTER TABLE source_review_requests ADD COLUMN "
                                   "source_fixture_version TEXT NOT NULL DEFAULT '3.0'")
            with connection:
                connection.execute(
                    "INSERT OR IGNORE INTO fixture_meta VALUES (1, ?, ?, ?, ?)",
                    (FIXTURE_ID, ORDER_DUE_AT, INITIAL_ORDER_VERSION, source_facts_sha256()),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO orders "
                    "(id, customer_name, item_name, sku, quantity, due_at, status, version) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ORDER_ID, "東都部品販売（合成顧客）", "産業用部品", "PART-40", 40,
                     ORDER_DUE_AT, "exception_detected", INITIAL_ORDER_VERSION),
                )
                for warehouse_id, available in INITIAL_INVENTORY.items():
                    connection.execute(
                        "INSERT OR IGNORE INTO inventory VALUES (?, ?)",
                        (warehouse_id, available),
                    )
                connection.execute(
                    "UPDATE orders SET current_trial_id = ? WHERE id = ? AND current_trial_id IS NULL",
                    (f"trial-{uuid4().hex}", ORDER_ID),
                )
                connection.execute(
                    "UPDATE agent_runs SET trial_id = (SELECT current_trial_id FROM orders "
                    "WHERE orders.id = agent_runs.order_id) WHERE trial_id IS NULL"
                )
                connection.execute(
                    "UPDATE operations SET trial_id = (SELECT current_trial_id FROM orders "
                    "WHERE orders.id = operations.order_id) WHERE trial_id IS NULL"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _get_order_row(connection: sqlite3.Connection, order_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            raise ApiError(404, "not_found", "受注が見つかりません。")
        return row

    @staticmethod
    def _order(row: sqlite3.Row) -> dict[str, Any]:
        next_actor, next_action = {
            "exception_detected": ("受注担当者", "分納と特急費用について顧客の回答を記録してください。"),
            "awaiting_approval": ("出荷責任者", "顧客回答と現在の在庫を確認し、出荷案を承認してください。"),
            "shipment_instructed": ("倉庫担当者", "保存された引当と出荷指示を確認してください。"),
        }[row["status"]]
        return {
            "id": row["id"], "customerName": row["customer_name"],
            "itemName": row["item_name"], "sku": row["sku"],
            "quantity": row["quantity"], "dueAt": row["due_at"],
            "fixtureId": FIXTURE_ID,
            "status": row["status"], "version": row["version"],
            "currentTrialId": row["current_trial_id"],
            "nextActor": next_actor, "nextAction": next_action,
            "dataMode": "synthetic_fixture",
        }

    def list_orders(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM orders ORDER BY id").fetchall()
            return {"orders": [self._order(row) for row in rows], "dataMode": "synthetic_fixture"}

    def get_options(self, order_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            self._get_order_row(connection, order_id)
        return {"orderId": order_id, "options": list(OPTIONS), "dataMode": "synthetic_fixture"}

    def get_sources(self, order_id: str) -> dict[str, Any]:
        """Return source facts only. Fixed completed options are intentionally absent."""
        with closing(self._connect()) as connection:
            row = self._get_order_row(connection, order_id)
            inventory = connection.execute(
                "SELECT warehouse_id, available FROM inventory ORDER BY warehouse_id"
            ).fetchall()
        captured_at = _now()
        return {
            "orderId": order_id,
            "sources": {
                "order": {
                    "sourceId": f"order-{order_id}-v{row['version']}",
                    "fixtureVersion": FIXTURE_VERSION, "fixtureId": FIXTURE_ID,
                    "orderVersion": row["version"],
                    "recordedAt": SCENARIO_TIME, "capturedAt": captured_at,
                    "sku": row["sku"], "quantityBoxes": row["quantity"],
                    "dueAt": row["due_at"], "dataMode": "synthetic_fixture",
                },
                "inventory": [
                    {
                        "sourceId": f"stock-{item['warehouse_id']}",
                        "fixtureVersion": FIXTURE_VERSION, "versionKnown": False,
                        "snapshotOrderVersion": row["version"], "capturedAt": captured_at,
                        "warehouseId": item["warehouse_id"], "availableBoxes": item["available"],
                        "dataMode": "synthetic_fixture",
                    }
                    for item in inventory
                ],
                "carriers": [
                    {**source, "fixtureVersion": source["version"]}
                    for source in CARRIER_SOURCES.values()
                ],
                "contract": {**CONTRACT_SOURCE, "fixtureVersion": CONTRACT_SOURCE["version"]},
            },
            "dataMode": "synthetic_fixture",
        }

    def _run_presentation(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        events = connection.execute(
            "SELECT * FROM agent_run_events WHERE run_id = ? ORDER BY sequence", (row["id"],)
        ).fetchall()
        ref_attempts = connection.execute(
            "SELECT * FROM agent_run_source_ref_attempts WHERE run_id = ? "
            "ORDER BY attempt_no, proposal_index", (row["id"],)
        ).fetchall()
        result: dict[str, Any] = {
            "id": row["id"], "orderId": row["order_id"], "status": row["status"],
            "trialId": row["trial_id"],
            "agentVersion": row["agent_version"], "executionMode": row["execution_mode"],
            "modelRoute": row["route"], "modelId": row["model_id"],
            "dataMode": "synthetic_fixture", "startedAt": row["started_at"],
            "endedAt": row["ended_at"],
            "modelFinalReply": row["model_final_reply"],
            "requestText": row["request_text"],
            "humanWaitSeconds": json.loads(row["request_json"] or "{}").get("humanWaitSeconds", 0),
            "events": [
                {"sequence": event["sequence"], "kind": event["kind"],
                 "actor": event["actor"], "message": event["message"],
                 **({"sourceId": event["source_id"]} if event["source_id"] else {}),
                 "createdAt": event["created_at"]}
                for event in events
            ],
        }
        if row["draft_json"] is not None:
            result["draft"] = json.loads(row["draft_json"])
        if row["source_snapshot_json"] is not None:
            result["sourceSnapshot"] = json.loads(row["source_snapshot_json"])
        tool_responses = self._tool_responses(connection, row["id"])
        if tool_responses:
            result["toolResponses"] = tool_responses
        if row["error_code"] is not None:
            result["errorCode"] = row["error_code"]
        if ref_attempts:
            result["sourceRefAttempts"] = [
                {"attemptNo": item["attempt_no"], "proposalIndex": item["proposal_index"],
                 "submittedRefs": json.loads(item["submitted_refs_json"]),
                 "submittedShape": item["submitted_shape"],
                 "duplicateRefs": json.loads(item["duplicate_refs_json"]),
                 "expectedRefs": json.loads(item["expected_refs_json"]),
                 "outcome": item["outcome"],
                 **({"errorCode": item["error_code"]} if item["error_code"] else {}),
                 **({"fieldPath": item["field_path"]} if item["field_path"] else {}),
                 **({"reason": item["reason"]} if item["reason"] else {}),
                 "createdAt": item["created_at"]}
                for item in ref_attempts
            ]
        return result

    @staticmethod
    def _request_json(request_text: str, expected_version: int,
                      human_wait_seconds: int = 0) -> str:
        request = {"requestText": request_text, "expectedVersion": expected_version}
        if human_wait_seconds:
            request["humanWaitSeconds"] = human_wait_seconds
        return _json(request)

    @staticmethod
    def fixture_validity() -> dict[str, Any]:
        """The dated synthetic carrier facts are not refreshed automatically."""
        detected = datetime.fromisoformat(SCENARIO_TIME)
        cutoffs: list[datetime] = []
        for carrier in CARRIER_SOURCES.values():
            pickup = datetime.fromisoformat(carrier["pickupCutoffAt"])
            if pickup <= detected:
                pickup = datetime.fromisoformat(carrier["nextPickupAt"])
            cutoffs.append(pickup)
            cutoffs.append(datetime.fromisoformat(carrier["earliestDeliveryAt"]))
        expires_at = min(cutoffs)
        return {"newRunAvailable": datetime.now(timezone.utc) < expires_at,
                "scenarioExpiresAt": expires_at.isoformat(),
                "fixtureVersion": FIXTURE_VERSION, "fixtureId": FIXTURE_ID,
                "dataMode": "synthetic_fixture"}

    @staticmethod
    def _validate_request(request_text: object, expected_version: object) -> tuple[str, int]:
        if request_text != SUPPORTED_REQUEST_TEXT:
            raise ApiError(422, "unsupported_request", "この隔離デモは表示された合成受注の依頼文だけを実行できます。")
        return SUPPORTED_REQUEST_TEXT, _expected_version(expected_version)

    def find_run(self, order_id: str, operation_id: object, *,
                 request_text: str, expected_version: int,
                 human_wait_seconds: int = 0) -> dict[str, Any] | None:
        _operation_id(operation_id)
        with closing(self._connect()) as connection:
            order = self._get_order_row(connection, order_id)
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE order_id = ? AND operation_id = ?",
                (order_id, operation_id),
            ).fetchone()
            if row is not None and row["request_json"] != self._request_json(
                    request_text, expected_version, human_wait_seconds):
                raise ApiError(409, "operation_conflict", "同じ操作IDで異なる依頼は送信できません。")
            if row is not None and row["trial_id"] != order["current_trial_id"]:
                raise ApiError(409, "stale_trial", "前回の試行の操作IDは再利用できません。")
            return self._run_presentation(connection, row) if row else None

    def create_run(self, order_id: str, operation_id: object, *, route: str,
                   model_id: str, execution_mode: str, request_text: str,
                   expected_version: int,
                   human_wait_seconds: int = 0,
                   agent_version: str = AGENT_VERSION) -> tuple[dict[str, Any], bool]:
        _operation_id(operation_id)
        if not route or not model_id or not agent_version or execution_mode not in {"live_model", "test_double"}:
            raise ApiError(400, "validation_error", "Agent版とモデル経路を指定してください。")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                self._get_order_row(connection, order_id)
                existing = connection.execute(
                    "SELECT * FROM agent_runs WHERE order_id = ? AND operation_id = ?",
                    (order_id, operation_id),
                ).fetchone()
                if existing is not None:
                    if existing["request_json"] != self._request_json(
                            request_text, expected_version, human_wait_seconds):
                        raise ApiError(409, "operation_conflict", "同じ操作IDで異なる依頼は送信できません。")
                    order = self._get_order_row(connection, order_id)
                    if existing["trial_id"] != order["current_trial_id"]:
                        raise ApiError(409, "stale_trial", "前回の試行の操作IDは再利用できません。")
                    return self._run_presentation(connection, existing), False
                row = self._get_order_row(connection, order_id)
                self._check_version(row, expected_version)
                if row["status"] != "exception_detected":
                    raise ApiError(409, "request_not_available", "顧客回答後または出荷指示後の受注では新しいAI調査を開始できません。")
                active = connection.execute(
                    "SELECT id FROM agent_runs WHERE order_id = ? AND trial_id = ? "
                    "AND status IN ('queued', 'running') LIMIT 1",
                    (order_id, row["current_trial_id"]),
                ).fetchone()
                if active is not None:
                    raise ApiError(409, "run_in_progress", "同じ受注でAgentが実行中です。完了を待ってから再実行してください。")
                if not self.fixture_validity()["newRunAvailable"]:
                    raise ApiError(409, "scenario_expired", "合成便の集荷または納品日時を過ぎています。資料を更新してください。")
                run_id = f"order-run-{uuid4().hex}"
                connection.execute(
                    "INSERT INTO agent_runs (id, order_id, operation_id, status, agent_version, "
                    "execution_mode, route, model_id, started_at, request_text, request_json, trial_id) "
                    "VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, order_id, operation_id, agent_version, execution_mode, route, model_id,
                     _now(), request_text, self._request_json(request_text, expected_version,
                                                              human_wait_seconds),
                     row["current_trial_id"]),
                )
                created = connection.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
                return self._run_presentation(connection, created), True

    def list_runs(self, order_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            self._get_order_row(connection, order_id)
            rows = connection.execute(
                "SELECT * FROM agent_runs WHERE order_id = ? ORDER BY rowid DESC", (order_id,)
            ).fetchall()
            order = self._get_order_row(connection, order_id)
            return {"orderId": order_id, "currentTrialId": order["current_trial_id"],
                    "agentRuns": [self._run_presentation(connection, row) for row in rows]}

    def get_run(self, run_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise ApiError(404, "not_found", "Agent実行が見つかりません。")
            return self._run_presentation(connection, row)

    def get_run_state(self, run_id: str) -> dict[str, Any]:
        """Read only the terminal state needed by the human wait gate."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT status, error_code FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ApiError(404, "not_found", "Agent実行が見つかりません。")
            return {"status": row["status"], "errorCode": row["error_code"]}

    def start_run(self, run_id: str) -> bool:
        with closing(self._connect()) as connection:
            with connection:
                updated = connection.execute(
                    "UPDATE agent_runs SET status = 'running' WHERE id = ? AND status = 'queued'", (run_id,)
                )
                return updated.rowcount == 1

    def append_run_event(self, run_id: str, *, kind: str, actor: str,
                         message: str, source_id: str | None = None) -> None:
        if (kind not in {"status", "manager_selection", "source_read", "source_refused",
                         "draft_rejected", "draft_submitted"}
                or not isinstance(actor, str) or not actor or len(actor) > 80
                or not isinstance(message, str) or not message or len(message) > 500
                or (source_id is not None and (not isinstance(source_id, str) or len(source_id) > 100))):
            raise ApiError(400, "validation_error", "Agentイベントの内容を確認してください。")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT status FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
                if row is None:
                    raise ApiError(404, "not_found", "Agent実行が見つかりません。")
                if row["status"] != "running":
                    return
                sequence = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_run_events WHERE run_id = ?", (run_id,)
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO agent_run_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (run_id, sequence, kind, actor, message, source_id, _now()),
                )

    def capture_source_ref_attempt(self, run_id: str, *, attempt_no: int,
                                   proposal_index: int, submitted_refs: object,
                                   submitted_shape: str, expected_refs: list[str],
                                   outcome: str,
                                   error_code: str | None = None,
                                   field_path: str | None = None,
                                   reason: str | None = None) -> None:
        """Persist only bounded synthetic IDs; never retain arbitrary model text."""
        if (type(attempt_no) is not int or not 1 <= attempt_no <= 3 or
                type(proposal_index) is not int or not -1 <= proposal_index <= 1 or
                not isinstance(outcome, str) or
                outcome not in {"validated", "rejected", "not_checked"} or
                not isinstance(submitted_shape, str) or
                submitted_shape not in {"string_array", "mixed_array", "invalid_shape",
                                        "missing", "unavailable"} or
                not isinstance(expected_refs, list) or len(expected_refs) > 8 or
                any(not isinstance(ref, str) or not _SYNTHETIC_REF.fullmatch(ref)
                    for ref in expected_refs) or
                (outcome == "validated" and any(value is not None for value in
                                                (error_code, field_path, reason))) or
                (outcome == "rejected" and any(value is None for value in
                                               (error_code, field_path, reason))) or
                (error_code is not None and
                 (not isinstance(error_code, str) or
                  not re.fullmatch(r"[a-z0-9_]{1,80}", error_code))) or
                (field_path is not None and
                 (not isinstance(field_path, str) or
                  not re.fullmatch(r"[A-Za-z0-9_\[\].]{1,100}", field_path))) or
                (reason is not None and
                 (not isinstance(reason, str) or
                  not re.fullmatch(r"[a-z0-9_]{1,80}", reason)))):
            raise ApiError(400, "invalid_source_ref_attempt", "資料参照の診断値を確認してください。")
        if isinstance(submitted_refs, list):
            safe_refs = [ref if isinstance(ref, str) and _SYNTHETIC_REF.fullmatch(ref)
                         else "<masked>" for ref in submitted_refs[:12]]
            if len(submitted_refs) > 12:
                safe_refs.append("<omitted>")
        else:
            safe_refs = ["<unavailable>"]
        duplicate_refs = sorted({ref for ref in safe_refs
                                 if _SYNTHETIC_REF.fullmatch(ref) and safe_refs.count(ref) > 1})
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT status FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
                if row is None or row["status"] != "running":
                    raise ApiError(409, "run_not_running", "実行中のAgent案だけ診断できます。")
                connection.execute(
                    "INSERT INTO agent_run_source_ref_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, attempt_no, proposal_index, _json(safe_refs), submitted_shape,
                     _json(duplicate_refs), _json(expected_refs), outcome, error_code,
                     field_path, reason, _now()),
                )

    @staticmethod
    def _tool_responses(connection: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT * FROM agent_run_tool_responses WHERE run_id = ? ORDER BY rowid",
            (run_id,),
        ).fetchall()
        return [{"toolName": row["tool_name"], "responseText": row["response_text"],
                 "responseSha256": row["response_sha256"],
                 "sourceIds": json.loads(row["source_ids_json"]),
                 "fixtureVersion": row["fixture_version"],
                 "capturedAt": row["captured_at"]} for row in rows]

    def get_tool_responses(self, run_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return self._tool_responses(connection, run_id)

    def capture_tool_response(self, run_id: str, *, tool_name: str,
                              response_text: str, response_sha256: str,
                              source_ids: list[str], fixture_version: str) -> None:
        """Save the exact Tool return value before the model can read it."""
        if (tool_name not in {"read_order_inventory", "read_carriers_contract"}
                or not isinstance(response_text, str) or not response_text
                or len(response_text) > 16000
                or not isinstance(source_ids, list) or not source_ids
                or any(not isinstance(item, str) or not item for item in source_ids)
                or fixture_version != FIXTURE_VERSION
                or hashlib.sha256(response_text.encode("utf-8")).hexdigest() != response_sha256):
            raise ApiError(400, "invalid_tool_response", "Tool応答の保存内容を確認してください。")
        try:
            parsed = json.loads(response_text)
            if tool_name == "read_order_inventory":
                actual_ids = [parsed["orderSourceId"], *[
                    item["sourceId"] for item in parsed["warehouses"]]]
            else:
                actual_ids = [*[item["sourceId"] for item in parsed["carriers"]],
                              parsed["contract"]["sourceId"]]
            if parsed["dataMode"] != "synthetic_fixture" or actual_ids != source_ids:
                raise ValueError("source ID mismatch")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ApiError(400, "invalid_tool_response", "Tool応答の資料IDを確認してください。") from None
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                run = connection.execute("SELECT status FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
                if run is None or run["status"] != "running":
                    raise ApiError(409, "run_not_running", "終了したAgent実行にTool応答を保存できません。")
                existing = connection.execute(
                    "SELECT response_text, response_sha256, source_ids_json FROM agent_run_tool_responses "
                    "WHERE run_id = ? AND tool_name = ?", (run_id, tool_name),
                ).fetchone()
                if existing is not None:
                    if (existing["response_text"] != response_text or
                            existing["response_sha256"] != response_sha256 or
                            existing["source_ids_json"] != _json(source_ids)):
                        raise ApiError(409, "tool_response_conflict", "同じToolの応答内容が変わりました。")
                    return
                connection.execute(
                    "INSERT INTO agent_run_tool_responses VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (run_id, tool_name, response_text, response_sha256,
                     _json(source_ids), fixture_version, _now()),
                )

    def finish_run(self, run_id: str, status: str, *, draft: dict[str, Any] | None = None,
                   error_code: str | None = None,
                   source_snapshot: dict[str, Any] | None = None,
                   model_final_reply: str | None = None) -> bool:
        if status not in {"completed", "draft_ready", "failed", "timed_out"}:
            raise ValueError("Agent実行の終了状態を確認してください。")
        if status in {"completed", "draft_ready"} and (not isinstance(draft, dict) or error_code is not None):
            raise ValueError("保存可能なAgent実行には下書きが必要です。")
        if status in {"completed", "draft_ready"} and not isinstance(source_snapshot, dict):
            raise ValueError("保存可能なAgent実行には読取時の受注snapshotが必要です。")
        if status in {"failed", "timed_out"} and (draft is not None or not error_code):
            raise ValueError("未完了のAgent実行には失敗コードが必要です。")
        if model_final_reply is not None and (
                status != "completed" or not _valid_model_final_reply(model_final_reply)):
            raise ValueError("保存するモデル最終返答の形式を確認してください。")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                run = connection.execute(
                    "SELECT order_id, status, execution_mode FROM agent_runs WHERE id = ?", (run_id,)
                ).fetchone()
                if run is None or run["status"] != "running":
                    return False
                if model_final_reply is not None and run["execution_mode"] != "live_model":
                    raise ValueError("モデル最終返答は実モデルrunにだけ保存してください。")
                if status in {"completed", "draft_ready"} and source_snapshot is not None:
                    order = self._get_order_row(connection, run["order_id"])
                    inventory = connection.execute(
                        "SELECT warehouse_id, available FROM inventory ORDER BY warehouse_id"
                    ).fetchall()
                    actual_stock = [{"warehouseId": item["warehouse_id"], "available": item["available"]}
                                    for item in inventory]
                    original_order = source_snapshot["order"]
                    if (order["version"] != original_order["version"] or
                            order["status"] != original_order["status"] or
                            actual_stock != source_snapshot["inventory"]):
                        status, draft, error_code, model_final_reply = "failed", None, "source_stale", None
                    elif not self.fixture_validity()["newRunAvailable"]:
                        status, draft, error_code, model_final_reply = "failed", None, "scenario_expired", None
                updated = connection.execute(
                    "UPDATE agent_runs SET status = ?, ended_at = ?, draft_json = ?, "
                    "model_final_reply = ?, error_code = ?, "
                    "source_snapshot_json = ? "
                    "WHERE id = ? AND status = 'running'",
                    (status, _now(), _json(draft) if draft else None, model_final_reply, error_code,
                     _json(source_snapshot) if status in {"completed", "draft_ready"} else None, run_id),
                )
                return updated.rowcount == 1

    def recover_interrupted_runs(self) -> int:
        """Close unfinished runs when this process takes ownership of the DB.

        A caller invokes this only at server startup, before starting workers.
        Saved events remain available for diagnosis after a process restart.
        """
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                updated = connection.execute(
                    "UPDATE agent_runs SET status = 'failed', ended_at = ?, "
                    "error_code = 'server_restarted' WHERE status IN ('queued', 'running')",
                    (_now(),),
                )
                return updated.rowcount

    def _frozen_contract(self, connection: sqlite3.Connection,
                         review: sqlite3.Row) -> dict[str, Any] | None:
        """Project only an integrity-checked contract Tool response from this run."""
        run = connection.execute(
            "SELECT source_snapshot_json FROM agent_runs WHERE id = ? AND order_id = ? "
            "AND trial_id = ?", (review["run_id"], review["order_id"], review["trial_id"]),
        ).fetchone()
        if run is None or not run["source_snapshot_json"]:
            return None
        try:
            snapshot = json.loads(run["source_snapshot_json"])
            saved = snapshot.get("toolResponses")
            actual = self._tool_responses(connection, review["run_id"])
            if not isinstance(saved, list) or saved != actual:
                return None
            item = next(item for item in actual
                        if item["toolName"] == "read_carriers_contract")
            response_text = item["responseText"]
            if hashlib.sha256(response_text.encode("utf-8")).hexdigest() != item["responseSha256"]:
                return None
            parsed = json.loads(response_text)
            contract = parsed["contract"]
            response_source_ids = [*[carrier["sourceId"] for carrier in parsed["carriers"]],
                                   contract["sourceId"]]
            if (item["fixtureVersion"] != review["source_fixture_version"] or
                    parsed["dataMode"] != "synthetic_fixture" or
                    contract["dataMode"] != "synthetic_fixture" or
                    not isinstance(contract["clause"], str) or not contract["clause"] or
                    not isinstance(contract["scope"], str) or not contract["scope"] or
                    contract["version"] != review["source_fixture_version"] or
                    response_source_ids != item["sourceIds"] or
                    any(carrier["version"] != item["fixtureVersion"]
                        for carrier in parsed["carriers"]) or
                    contract["sourceId"] not in json.loads(review["source_refs_json"])):
                return None
        except (KeyError, TypeError, ValueError, StopIteration, json.JSONDecodeError):
            return None
        return {"sourceId": contract["sourceId"], "version": contract["version"],
                "clause": contract["clause"], "scope": contract["scope"],
                "toolName": item["toolName"], "responseText": response_text,
                "responseSha256": item["responseSha256"], "integrityVerified": True,
                "noPayerClauseReviewed": contract == HISTORICAL_NO_PAYER_CONTRACT_V3}

    def _contract_tool_response_integrity(self, connection: sqlite3.Connection,
                                          review: sqlite3.Row,
                                          frozen_contract: dict[str, Any] | None = None) -> str:
        """Distinguish an old absent response from a present but invalid one."""
        if frozen_contract is not None:
            return "verified"
        row_present = connection.execute(
            "SELECT 1 FROM agent_run_tool_responses WHERE run_id = ? "
            "AND tool_name = 'read_carriers_contract'", (review["run_id"],),
        ).fetchone() is not None
        run = connection.execute(
            "SELECT source_snapshot_json FROM agent_runs WHERE id = ? AND order_id = ? "
            "AND trial_id = ?", (review["run_id"], review["order_id"], review["trial_id"]),
        ).fetchone()
        if run is None:
            return "invalid"
        saved_text = run["source_snapshot_json"]
        if saved_text:
            try:
                snapshot = json.loads(saved_text)
                if not isinstance(snapshot, dict):
                    return "invalid"
                if "toolResponses" in snapshot:
                    items = snapshot["toolResponses"]
                    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                        return "invalid"
                    if any(item.get("toolName") == "read_carriers_contract" for item in items):
                        return "invalid"
            except (TypeError, ValueError, json.JSONDecodeError):
                return "invalid"
        return "invalid" if row_present else "missing"

    def _detail(self, connection: sqlite3.Connection, order_id: str) -> dict[str, Any]:
        row = self._get_order_row(connection, order_id)
        archives = connection.execute(
            "SELECT * FROM trial_archives WHERE order_id = ? ORDER BY rowid DESC",
            (order_id,),
        ).fetchall()
        response = connection.execute("SELECT * FROM customer_responses WHERE order_id = ?", (order_id,)).fetchone()
        approval = connection.execute("SELECT * FROM approvals WHERE order_id = ?", (order_id,)).fetchone()
        review = connection.execute(
            "SELECT * FROM source_review_requests WHERE order_id = ? AND trial_id = ? "
            "ORDER BY requested_at DESC, id DESC LIMIT 1", (order_id, row["current_trial_id"]),
        ).fetchone()
        review_response = (connection.execute(
            "SELECT * FROM source_review_responses WHERE review_id = ?", (review["id"],),
        ).fetchone() if review is not None else None)
        frozen_contract = self._frozen_contract(connection, review) if review is not None else None
        tool_response_integrity = (self._contract_tool_response_integrity(
            connection, review, frozen_contract) if review is not None else None)
        reservations = connection.execute(
            "SELECT warehouse_id, quantity FROM reservations WHERE order_id = ? ORDER BY warehouse_id", (order_id,)
        ).fetchall()
        instructions = connection.execute(
            "SELECT warehouse_id, quantity, delivery_at, created_at FROM shipment_instructions "
            "WHERE order_id = ? ORDER BY warehouse_id", (order_id,),
        ).fetchall()
        inventory = connection.execute("SELECT warehouse_id, available FROM inventory ORDER BY warehouse_id").fetchall()
        shipment = None
        if approval is not None:
            shipment = {
                "reservations": [{"warehouseId": item["warehouse_id"], "quantity": item["quantity"]} for item in reservations],
                "instructions": [
                    {"warehouseId": item["warehouse_id"], "quantity": item["quantity"],
                     "deliveryAt": item["delivery_at"], "createdAt": item["created_at"]}
                    for item in instructions
                ],
                "instructedAt": approval["approved_at"],
                "dataMode": "synthetic_fixture",
            }
        order_view = self._order(row)
        if review is not None and review["status"] == "pending":
            order_view["nextActor"] = "契約確認担当役（合成）"
            order_view["nextAction"] = "追加費用の負担先を原資料で確認し、出荷承認は保留してください。"
        elif review is not None and review["status"] == "original_source_unavailable":
            order_view["nextActor"] = "出荷責任者役・契約確認担当役（合成）"
            order_view["nextAction"] = "旧run時点の便・契約原文を取得して再確認してください。出荷承認は保留します。"
        elif review is not None and review["status"] == "source_clause_no_payer":
            order_view["nextActor"] = "契約確認担当役・出荷責任者役（合成）"
            order_view["nextAction"] = "保存した契約条項には追加費用の負担先がありません。別の原資料を確認し、出荷承認を保留してください。"
        return {
            "order": order_view,
            "exception": {
                "type": "stock_shortage_and_cutoff",
                "message": "倉庫Aは25箱で15箱不足。倉庫Bの通常便は11月7日21時の集荷締切を過ぎています。",
                "detectedAt": SCENARIO_TIME,
                "contractConstraint": "分納には顧客の承諾が必要です。",
                "dataMode": "synthetic_fixture",
            },
            "options": list(OPTIONS),
            "customerResponse": None if response is None else {
                "runId": response["run_id"],
                "selectedOptionId": response["selected_option_id"],
                "acknowledgedTerms": (json.loads(response["acknowledged_terms_json"])
                                      if response["acknowledged_terms_json"] else None),
                "acceptedSplit": bool(response["accepted_split"]), "note": response["note"],
                "acceptedExpressCost": bool(response["accepted_express_cost"]),
                "recordedAt": response["recorded_at"],
            },
            "approval": None if approval is None else {
                "runId": approval["run_id"],
                "optionId": approval["option_id"], "reason": approval["reason"],
                "actorRole": approval["actor_role"], "approvedAt": approval["approved_at"],
                "authorizationMode": "declared_role_fixture",
            },
            "sourceReviewRequest": None if review is None else {
                "id": review["id"], "trialId": review["trial_id"],
                "runId": review["run_id"], "optionId": review["option_id"],
                "requestedByRole": review["requested_by_role"],
                "assignedToRole": review["assigned_to_role"],
                "reason": review["reason"], "sourceRefs": json.loads(review["source_refs_json"]),
                "sourceFixtureVersion": review["source_fixture_version"],
                "status": review["status"], "requestedAt": review["requested_at"],
                "frozenContract": frozen_contract,
                "toolResponseIntegrity": tool_response_integrity,
                "response": None if review_response is None else {
                    "reviewId": review_response["review_id"],
                    "trialId": review_response["trial_id"],
                    "runId": review_response["run_id"],
                    "optionId": review_response["option_id"],
                    "actorRole": review_response["actor_role"],
                    "outcome": review_response["outcome"],
                    "reason": review_response["reason"],
                    "respondedAt": review_response["responded_at"],
                },
                "dataMode": "synthetic_fixture", "authorizationMode": "declared_role_fixture",
            },
            "shipment": shipment,
            "inventory": [{"warehouseId": item["warehouse_id"], "available": item["available"]} for item in inventory],
            "latestTrialArchiveId": archives[0]["id"] if archives else None,
            "previousTrials": [self._archive_presentation(item) for item in archives],
            "dataMode": "synthetic_fixture",
            "authenticationMode": "none_local_trial",
        }

    def get_detail(self, order_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            return self._detail(connection, order_id)

    @staticmethod
    def _archive_presentation(row: sqlite3.Row) -> dict[str, Any]:
        outcome = json.loads(row["outcome_json"])
        approval = outcome.get("approval")
        review = outcome.get("sourceReviewRequest") or {}
        review_response = review.get("response") or {}
        completed_at = approval["approvedAt"] if approval else review_response.get("respondedAt") or row["archived_at"]
        return {
            "archiveId": row["id"], "trialId": row["trial_id"],
            "orderId": row["order_id"],
            "completedAt": completed_at,
            "archivedAt": row["archived_at"],
            "runIds": json.loads(row["run_ids_json"]),
            "outcome": outcome,
            "dataMode": "synthetic_fixture",
        }

    def get_archive(self, order_id: str, archive_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            self._get_order_row(connection, order_id)
            row = connection.execute(
                "SELECT * FROM trial_archives WHERE id = ? AND order_id = ?", (archive_id, order_id)
            ).fetchone()
            if row is None:
                raise ApiError(404, "not_found", "試行履歴が見つかりません。")
            return {"archive": self._archive_presentation(row)}

    def current_trial_id(self, order_id: str = ORDER_ID) -> str:
        with closing(self._connect()) as connection:
            return self._get_order_row(connection, order_id)["current_trial_id"]

    def new_trial(self, order_id: str, raw: object) -> dict[str, Any]:
        body = self._payload(raw, {"operationId", "expectedVersion", "confirmNewTrial"})
        operation_id = _operation_id(body["operationId"])
        expected = _expected_version(body["expectedVersion"])
        if body["confirmNewTrial"] is not True:
            raise ApiError(400, "validation_error", "新しい合成試行の開始を明示的に確認してください。")
        request_json = _json(body)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._get_order_row(connection, order_id)
                replay = self._replay(connection, operation_id, order_id, "new_trial", request_json)
                if replay is not None:
                    return replay
                self._check_version(row, expected)
                active = connection.execute(
                    "SELECT 1 FROM agent_runs WHERE order_id = ? AND trial_id = ? "
                    "AND status IN ('queued', 'running') LIMIT 1",
                    (order_id, row["current_trial_id"]),
                ).fetchone()
                if active is not None:
                    raise ApiError(409, "trial_in_progress", "実行中のAgent調査が終わってから新しい試行を開始してください。")
                review = connection.execute(
                    "SELECT r.status, s.outcome FROM source_review_requests AS r "
                    "LEFT JOIN source_review_responses AS s ON s.review_id = r.id "
                    "AND s.order_id = r.order_id AND s.trial_id = r.trial_id AND s.run_id = r.run_id "
                    "AND s.option_id = r.option_id WHERE r.order_id = ? AND r.trial_id = ? "
                    "ORDER BY r.rowid DESC LIMIT 1",
                    (order_id, row["current_trial_id"]),
                ).fetchone()
                held_after_review = (row["status"] == "awaiting_approval" and review is not None and
                                     review["status"] in {"source_clause_no_payer", "original_source_unavailable"} and
                                     review["outcome"] == review["status"] and
                                     connection.execute("SELECT 1 FROM approvals WHERE order_id = ? LIMIT 1", (order_id,)).fetchone() is None and
                                     connection.execute("SELECT 1 FROM reservations WHERE order_id = ? LIMIT 1", (order_id,)).fetchone() is None and
                                     connection.execute("SELECT 1 FROM shipment_instructions WHERE order_id = ? LIMIT 1", (order_id,)).fetchone() is None)
                if row["status"] != "shipment_instructed" and not held_after_review:
                    raise ApiError(409, "trial_not_completed", "出荷指示済みか、契約確認の回答で出荷を保留した合成試行だけを再開始できます。")
                if not self.fixture_validity()["newRunAvailable"]:
                    raise ApiError(409, "scenario_expired", "合成便の集荷または納品日時を過ぎています。資料を更新してください。")

                # Preserve the complete business result before clearing current rows.
                outcome = self._detail(connection, order_id)
                outcome.pop("previousTrials")
                outcome.pop("latestTrialArchiveId")
                run_ids = [item["id"] for item in connection.execute(
                    "SELECT id FROM agent_runs WHERE order_id = ? AND trial_id = ? ORDER BY started_at, id",
                    (order_id, row["current_trial_id"]),
                ).fetchall()]
                archive_id = f"trial-archive-{uuid4().hex}"
                connection.execute(
                    "INSERT INTO trial_archives VALUES (?, ?, ?, ?, ?, ?)",
                    (archive_id, order_id, row["current_trial_id"], _now(),
                     _json(outcome), _json(run_ids)),
                )
                for table in ("shipment_instructions", "reservations", "approvals", "customer_responses"):
                    connection.execute(f"DELETE FROM {table} WHERE order_id = ?", (order_id,))
                for warehouse_id, available in INITIAL_INVENTORY.items():
                    connection.execute(
                        "UPDATE inventory SET available = ? WHERE warehouse_id = ?",
                        (available, warehouse_id),
                    )
                connection.execute(
                    "UPDATE orders SET status = 'exception_detected', version = version + 1, "
                    "current_trial_id = ? WHERE id = ?",
                    (f"trial-{uuid4().hex}", order_id),
                )
                result = {**self._detail(connection, order_id), "previousArchiveId": archive_id}
                connection.execute(
                    "INSERT INTO operations (operation_id, order_id, operation_type, "
                    "request_json, response_json, trial_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (operation_id, order_id, "new_trial", request_json, _json(result),
                     result["order"]["currentTrialId"]),
                )
                return result

    def record_inventory_count(self, order_id: str, raw: object,
                               verified_actor: dict[str, str]) -> dict[str, Any]:
        """Record a synthetic warehouse count while an investigation may be running.

        The count is business input, so its update invalidates a run's read snapshot.
        The subject is verified at the HTTP boundary before any replay lookup.
        """
        body = self._payload(raw, {"operationId", "runId", "expectedVersion", "warehouseId",
                                   "expectedAvailableBoxes", "availableBoxes", "reason"},
                             {"actorRole"})
        # The browser's old actorRole is only display input, never authority.
        body.pop("actorRole", None)
        body["actorRole"] = "warehouse_clerk"
        body["authenticatedVirtualUserId"] = verified_actor["virtualUserId"]
        operation_id = _operation_id(body["operationId"])
        run_id = body["runId"]
        if not isinstance(run_id, str) or re.fullmatch(r"order-run-[0-9a-f]{32}", run_id) is None:
            raise ApiError(400, "validation_error", "runId は現在のAgent実行IDにしてください。")
        expected_version = _expected_version(body["expectedVersion"])
        warehouse_id = body["warehouseId"]
        if warehouse_id not in INITIAL_INVENTORY:
            raise ApiError(400, "validation_error", "対象倉庫を確認してください。")
        for field in ("expectedAvailableBoxes", "availableBoxes"):
            value = body[field]
            if type(value) is not int or value < 0 or value > 1000:
                raise ApiError(400, "validation_error", f"{field} は0～1000の整数にしてください。")
        if body["expectedAvailableBoxes"] == body["availableBoxes"]:
            raise ApiError(400, "validation_error", "補正後の在庫数を変更してください。")
        if not isinstance(body["reason"], str) or not body["reason"].strip() or len(body["reason"]) > 500:
            raise ApiError(400, "validation_error", "補正理由を1～500文字で入力してください。")
        request_json = _json(body)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._get_order_row(connection, order_id)
                replay = self._replay(connection, operation_id, order_id,
                                      "inventory_count", request_json)
                if replay is not None:
                    return replay
                self._check_version(row, expected_version)
                if row["status"] != "exception_detected":
                    raise ApiError(409, "inventory_count_unavailable",
                                   "回答・出荷処理が始まった受注の在庫はこの操作で補正できません。")
                run = connection.execute(
                    "SELECT order_id, trial_id, status FROM agent_runs WHERE id = ?", (run_id,)
                ).fetchone()
                if (run is None or run["order_id"] != order_id or
                        run["trial_id"] != row["current_trial_id"] or run["status"] != "running"):
                    raise ApiError(409, "run_not_waiting", "このAgent実行は在庫補正を待っていません。")
                tool_rows = connection.execute(
                    "SELECT tool_name, response_text, response_sha256 FROM agent_run_tool_responses "
                    "WHERE run_id = ?", (run_id,),
                ).fetchall()
                if {item["tool_name"] for item in tool_rows} != {
                        "read_order_inventory", "read_carriers_contract"}:
                    raise ApiError(409, "run_tools_incomplete", "Agentの資料読取が終わっていません。")
                if any(hashlib.sha256(item["response_text"].encode()).hexdigest() !=
                       item["response_sha256"] for item in tool_rows):
                    raise ApiError(409, "run_source_invalid", "保存されたAgent資料を照合できません。")
                order_tool = next(item for item in tool_rows
                                  if item["tool_name"] == "read_order_inventory")
                try:
                    tool_body = json.loads(order_tool["response_text"])
                    tool_version = tool_body["orderVersion"]
                    tool_warehouse = next(item["availableBoxes"] for item in
                                          tool_body["warehouses"] if item["warehouseId"] == warehouse_id)
                except (TypeError, ValueError, KeyError, StopIteration):
                    raise ApiError(409, "run_source_invalid", "保存されたAgent資料を照合できません。") from None
                if (type(tool_version) is not int or tool_version != row["version"] or
                        type(tool_warehouse) is not int or
                        tool_warehouse != body["expectedAvailableBoxes"]):
                    raise ApiError(409, "run_source_stale", "Agentが読んだ受注または在庫が変わっています。")
                submitted = connection.execute(
                    "SELECT 1 FROM agent_run_events WHERE run_id = ? AND kind = 'draft_submitted' LIMIT 1",
                    (run_id,),
                ).fetchone()
                if submitted is not None:
                    raise ApiError(409, "run_already_submitted", "Agentが案を提出済みです。")
                stock = connection.execute(
                    "SELECT available FROM inventory WHERE warehouse_id = ?", (warehouse_id,)
                ).fetchone()
                if stock is None or stock["available"] != body["expectedAvailableBoxes"]:
                    raise ApiError(409, "inventory_conflict",
                                   "現在庫が変更されています。再読込してから補正してください。")
                connection.execute(
                    "UPDATE inventory SET available = ? WHERE warehouse_id = ?",
                    (body["availableBoxes"], warehouse_id),
                )
                connection.execute(
                    "UPDATE orders SET version = version + 1 WHERE id = ?", (order_id,)
                )
                updated = self._get_order_row(connection, order_id)
                result = {
                    "orderId": order_id, "runId": run_id,
                    "orderVersion": updated["version"],
                    "warehouseId": warehouse_id,
                    "previousAvailableBoxes": body["expectedAvailableBoxes"],
                    "availableBoxes": body["availableBoxes"],
                    "actorRole": "warehouse_clerk", "reason": body["reason"],
                    "authenticatedVirtualUserId": verified_actor["virtualUserId"],
                    "dataMode": "synthetic_fixture",
                    "authenticationMode": "keycloak_oidc_openfga_local_trial",
                }
                connection.execute(
                    "INSERT INTO operations (operation_id, order_id, operation_type, "
                    "request_json, response_json, trial_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (operation_id, order_id, "inventory_count", request_json,
                     _json(result), row["current_trial_id"]),
                )
                sequence = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_run_events WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO agent_run_events VALUES (?, ?, 'human_inventory_count', ?, ?, NULL, ?)",
                    (run_id, sequence, "verified_warehouse_clerk",
                     f"合成担当{verified_actor['virtualUserId']}が倉庫{warehouse_id}の在庫を"
                     f"{body['expectedAvailableBoxes']}→{body['availableBoxes']}箱へ補正し、"
                     f"受注第{updated['version']}版を保存しました。", _now()),
                )
                # The saved Tool snapshot is now obsolete. Close this run in the
                # same transaction so its worker cannot publish an old proposal.
                connection.execute(
                    "UPDATE agent_runs SET status = 'failed', ended_at = ?, "
                    "error_code = 'source_stale' WHERE id = ? AND status = 'running'",
                    (_now(), run_id),
                )
                return result

    @staticmethod
    def _payload(value: object, required: set[str], optional: set[str] = frozenset()) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) - (required | optional) or not required.issubset(value):
            raise ApiError(400, "validation_error", "操作の入力項目を確認してください。")
        return value

    @staticmethod
    def _replay(connection: sqlite3.Connection, operation_id: str, order_id: str,
                operation_type: str, request_json: str) -> dict[str, Any] | None:
        previous = connection.execute("SELECT * FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
        if previous is None:
            return None
        if (previous["order_id"], previous["operation_type"], previous["request_json"]) != (
            order_id, operation_type, request_json,
        ):
            raise ApiError(409, "operation_conflict", "同じ操作IDで異なる内容は送信できません。")
        current = connection.execute(
            "SELECT current_trial_id FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if current is None or previous["trial_id"] != current["current_trial_id"]:
            raise ApiError(409, "stale_trial", "前回の試行の操作IDは再利用できません。")
        return json.loads(previous["response_json"])

    @staticmethod
    def _check_version(row: sqlite3.Row, expected: int) -> None:
        if row["version"] != expected:
            raise ApiError(409, "version_conflict", "受注が更新されています。再読込してから操作してください。")

    @staticmethod
    def _option_terms(option: dict[str, Any]) -> dict[str, Any]:
        return {"allocations": option["allocations"], "additionalCostJpy": option["additionalCostJpy"]}

    @staticmethod
    def _agent_terms(proposal: dict[str, Any]) -> dict[str, Any]:
        return {
            "allocations": [
                {"warehouseId": item["warehouseId"], "quantity": item["quantityBoxes"],
                 "deliveryAt": item["deliveryAt"]}
                for item in proposal["shipments"]
            ],
            "additionalCostJpy": proposal["additionalCostJpy"],
        }

    @staticmethod
    def _agent_proposal(connection: sqlite3.Connection, order_id: str,
                        run_id: object, option_id: object) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(run_id, str) or not isinstance(option_id, str):
            raise ApiError(400, "validation_error", "runId とAgent案IDを指定してください。")
        row = connection.execute(
            "SELECT status, draft_json, source_snapshot_json, trial_id "
            "FROM agent_runs WHERE id = ? AND order_id = ?",
            (run_id, order_id),
        ).fetchone()
        current = connection.execute(
            "SELECT current_trial_id FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if row is not None and current is not None and row["trial_id"] != current["current_trial_id"]:
            raise ApiError(409, "stale_trial", "前回の試行のAgent案には回答・承認できません。")
        if row is not None:
            latest = connection.execute(
                "SELECT id FROM agent_runs WHERE order_id = ? AND trial_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (order_id, row["trial_id"]),
            ).fetchone()
            if latest is not None and latest["id"] != run_id:
                raise ApiError(409, "stale_run", "新しいAgent実行があります。前の実行の案は操作できません。")
        if row is None or row["status"] not in {"completed", "draft_ready"} or not row["draft_json"] or not row["source_snapshot_json"]:
            raise ApiError(422, "agent_draft_required", "資料照合後に保存した同じ受注のAgent案が必要です。")
        draft = json.loads(row["draft_json"])
        proposal = next(
            (item for item in draft["plan"]["proposals"]
             if item.get("id") == option_id and item.get("runId") == run_id), None,
        )
        if proposal is None:
            raise ApiError(422, "agent_option_mismatch", "指定案はこのAgent実行にありません。")
        return proposal, json.loads(row["source_snapshot_json"])

    @staticmethod
    def _check_agent_snapshot(connection: sqlite3.Connection, row: sqlite3.Row,
                              snapshot: dict[str, Any], *, run_id: str,
                              response_recorded: bool) -> None:
        run = connection.execute("SELECT trial_id FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if (run is None or run["trial_id"] != row["current_trial_id"] or
                snapshot["order"].get("currentTrialId", row["current_trial_id"]) != row["current_trial_id"]):
            raise ApiError(409, "stale_trial", "前回の試行のAgent案には回答・承認できません。")
        expected_version = snapshot["order"]["version"] + int(response_recorded)
        expected_status = "awaiting_approval" if response_recorded else snapshot["order"]["status"]
        inventory = connection.execute(
            "SELECT warehouse_id, available FROM inventory ORDER BY warehouse_id"
        ).fetchall()
        actual_stock = [{"warehouseId": item["warehouse_id"], "available": item["available"]}
                        for item in inventory]
        if (row["version"] != expected_version or row["status"] != expected_status or
                actual_stock != snapshot["inventory"]):
            raise ApiError(409, "source_stale", "Agent案の読取後に受注または在庫が変わりました。")

    @staticmethod
    def _check_agent_schedule(proposal: dict[str, Any], snapshot: dict[str, Any]) -> None:
        detected = datetime.fromisoformat(snapshot["exception"]["detectedAt"])
        now = datetime.now(timezone.utc)
        for shipment in proposal["shipments"]:
            carrier = CARRIER_SOURCES[shipment["carrierSourceId"]]
            pickup = datetime.fromisoformat(carrier["pickupCutoffAt"])
            if pickup <= detected:
                pickup = datetime.fromisoformat(carrier["nextPickupAt"])
            if pickup <= now or datetime.fromisoformat(shipment["deliveryAt"]) <= now:
                raise ApiError(409, "source_stale", "集荷または納品日時を過ぎたため、Agent案を更新してください。")

    def record_agent_customer_response(self, order_id: str, raw: object) -> dict[str, Any]:
        body = self._payload(raw, {"runId", "selectedOptionId", "acknowledgedTerms", "acceptedSplit",
                                   "acceptedExpressCost", "expectedVersion", "operationId", "actorRole"}, {"note"})
        operation_id = _operation_id(body["operationId"])
        expected = _expected_version(body["expectedVersion"])
        if body["actorRole"] != "order_clerk":
            raise ApiError(403, "forbidden", "合成受注担当者の操作として登録してください。")
        if type(body["acceptedSplit"]) is not bool or type(body["acceptedExpressCost"]) is not bool:
            raise ApiError(400, "validation_error", "同意欄は真偽値にしてください。")
        note = body.get("note", "")
        if not isinstance(note, str) or len(note) > 1000:
            raise ApiError(400, "validation_error", "顧客回答のメモは1000文字以下にしてください。")
        request_json = _json(body)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._get_order_row(connection, order_id)
                replay = self._replay(connection, operation_id, order_id, "agent_customer_response", request_json)
                if replay is not None:
                    return replay
                self._check_version(row, expected)
                proposal, snapshot = self._agent_proposal(connection, order_id,
                                                          body["runId"], body["selectedOptionId"])
                self._check_agent_snapshot(connection, row, snapshot,
                                           run_id=body["runId"], response_recorded=False)
                self._check_agent_schedule(proposal, snapshot)
                terms = self._agent_terms(proposal)
                if _json(body["acknowledgedTerms"]) != _json(terms):
                    raise ApiError(422, "option_terms_mismatch", "数量・納品日時・費用の確認値がAgent案と異なります。")
                if proposal["additionalCostJpy"] == 0 and body["acceptedExpressCost"]:
                    raise ApiError(422, "unrelated_cost_consent", "通常便案に特急費用への同意は記録できません。")
                if connection.execute("SELECT 1 FROM customer_responses WHERE order_id = ?", (order_id,)).fetchone():
                    raise ApiError(409, "response_already_recorded", "顧客回答は登録済みです。")
                connection.execute(
                    "INSERT INTO customer_responses "
                    "(order_id, run_id, selected_option_id, acknowledged_terms_json, accepted_split, "
                    "accepted_express_cost, note, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (order_id, body["runId"], body["selectedOptionId"], _json(terms),
                     int(body["acceptedSplit"]), int(body["acceptedExpressCost"]), note, _now()),
                )
                connection.execute(
                    "UPDATE orders SET status = 'awaiting_approval', version = version + 1 WHERE id = ?", (order_id,)
                )
                result = self._detail(connection, order_id)
                connection.execute(
                    "INSERT INTO operations (operation_id, order_id, operation_type, request_json, "
                    "response_json, trial_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (operation_id, order_id, "agent_customer_response", request_json,
                     _json(result), row["current_trial_id"]),
                )
                return result

    def decide_agent_proposal(self, order_id: str, raw: object) -> dict[str, Any]:
        body = self._payload(raw, {"runId", "optionId", "reason", "expectedVersion", "operationId", "actorRole"})
        operation_id = _operation_id(body["operationId"])
        expected = _expected_version(body["expectedVersion"])
        if body["actorRole"] != "shipping_manager":
            raise ApiError(403, "forbidden", "合成出荷責任者の操作として承認してください。")
        if not isinstance(body["reason"], str) or not body["reason"].strip() or len(body["reason"]) > 1000:
            raise ApiError(400, "validation_error", "承認理由を1〜1000文字で入力してください。")
        request_json = _json(body)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._get_order_row(connection, order_id)
                replay = self._replay(connection, operation_id, order_id, "agent_decision", request_json)
                if replay is not None:
                    return replay
                self._check_version(row, expected)
                proposal, snapshot = self._agent_proposal(connection, order_id,
                                                          body["runId"], body["optionId"])
                if row["status"] == "shipment_instructed":
                    raise ApiError(409, "already_instructed", "出荷指示は確定済みです。")
                response = connection.execute(
                    "SELECT * FROM customer_responses WHERE order_id = ?", (order_id,)
                ).fetchone()
                if response is None:
                    raise ApiError(422, "customer_response_required", "Agent案の承認前に顧客回答が必要です。")
                self._check_agent_snapshot(connection, row, snapshot,
                                           run_id=body["runId"], response_recorded=True)
                self._check_agent_schedule(proposal, snapshot)
                if response["run_id"] != body["runId"] or response["selected_option_id"] != body["optionId"]:
                    raise ApiError(422, "selected_option_mismatch", "承認案が顧客の選んだAgent案と異なります。")
                if response["acknowledged_terms_json"] != _json(self._agent_terms(proposal)):
                    raise ApiError(409, "option_terms_changed", "Agent案の数量・納品日時・費用が変わりました。")
                if not response["accepted_split"]:
                    raise ApiError(422, "consent_required", "別送による分納の承諾が必要です。")
                if proposal["additionalCostJpy"]:
                    if not response["accepted_express_cost"]:
                        raise ApiError(422, "express_cost_consent_required", "追加費用への承諾が必要です。")
                    raise ApiError(422, "cost_bearer_source_missing", "追加費用の負担先を示す資料がありません。")
                terms = self._agent_terms(proposal)
                if sum(item["quantity"] for item in terms["allocations"]) != row["quantity"]:
                    raise ApiError(422, "invalid_allocation", "Agent案の数量が受注数量と一致しません。")
                for allocation in terms["allocations"]:
                    stock = connection.execute("SELECT available FROM inventory WHERE warehouse_id = ?",
                                               (allocation["warehouseId"],)).fetchone()
                    if stock is None or stock["available"] < allocation["quantity"]:
                        raise ApiError(409, "insufficient_inventory", "現在の在庫では出荷できません。")
                timestamp = _now()
                for allocation in terms["allocations"]:
                    connection.execute("UPDATE inventory SET available = available - ? WHERE warehouse_id = ?",
                                       (allocation["quantity"], allocation["warehouseId"]))
                    connection.execute("INSERT INTO reservations VALUES (?, ?, ?)",
                                       (order_id, allocation["warehouseId"], allocation["quantity"]))
                    connection.execute("INSERT INTO shipment_instructions VALUES (?, ?, ?, ?, ?)",
                                       (order_id, allocation["warehouseId"], allocation["quantity"],
                                        allocation["deliveryAt"], timestamp))
                connection.execute(
                    "INSERT INTO approvals (order_id, run_id, option_id, reason, actor_role, approved_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (order_id, body["runId"], body["optionId"], body["reason"], body["actorRole"], timestamp),
                )
                connection.execute(
                    "UPDATE orders SET status = 'shipment_instructed', version = version + 1 WHERE id = ?", (order_id,)
                )
                result = self._detail(connection, order_id)
                connection.execute(
                    "INSERT INTO operations (operation_id, order_id, operation_type, request_json, "
                    "response_json, trial_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (operation_id, order_id, "agent_decision", request_json,
                     _json(result), row["current_trial_id"]),
                )
                return result

    def request_cost_source_review(self, order_id: str, raw: object) -> dict[str, Any]:
        """Save a pending specialist handoff; never infer a cost bearer or approve shipping."""
        body = self._payload(raw, {"runId", "optionId", "reason", "expectedVersion",
                                   "operationId", "actorRole"})
        operation_id = _operation_id(body["operationId"])
        expected = _expected_version(body["expectedVersion"])
        if body["actorRole"] != "shipping_manager":
            raise ApiError(403, "forbidden", "合成出荷責任者役の依頼として登録してください。")
        if not isinstance(body["reason"], str) or not body["reason"].strip() or len(body["reason"]) > 1000:
            raise ApiError(400, "validation_error", "確認依頼の理由を1〜1000文字で入力してください。")
        request_json = _json(body)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._get_order_row(connection, order_id)
                replay = self._replay(connection, operation_id, order_id, "cost_source_review", request_json)
                if replay is not None:
                    return replay
                self._check_version(row, expected)
                proposal, snapshot = self._agent_proposal(connection, order_id,
                                                          body["runId"], body["optionId"])
                if row["status"] != "awaiting_approval":
                    raise ApiError(409, "review_not_available", "合成顧客回答後の承認待ち案だけ確認依頼できます。")
                self._check_agent_snapshot(connection, row, snapshot,
                                           run_id=body["runId"], response_recorded=True)
                response = connection.execute(
                    "SELECT * FROM customer_responses WHERE order_id = ?", (order_id,),
                ).fetchone()
                if (response is None or response["run_id"] != body["runId"] or
                        response["selected_option_id"] != body["optionId"]):
                    raise ApiError(422, "selected_option_mismatch", "顧客回答と同じAgent案を指定してください。")
                if response["acknowledged_terms_json"] != _json(self._agent_terms(proposal)):
                    raise ApiError(409, "option_terms_changed", "Agent案の数量・納品日時・費用が変わりました。")
                if not response["accepted_express_cost"] or proposal["additionalCostJpy"] <= 0:
                    raise ApiError(422, "cost_review_not_required", "追加費用に同意した特急案だけ確認依頼できます。")
                if proposal.get("approvalAvailability") != "blocked_missing_cost_bearer_source":
                    raise ApiError(422, "cost_source_not_missing", "費用負担先資料の欠落がある案だけ確認依頼できます。")
                source_refs = [item for item in proposal.get("sourceRefs", []) if isinstance(item, str)]
                if CONTRACT_SOURCE["sourceId"] not in source_refs:
                    raise ApiError(422, "contract_source_not_read", "契約資料IDを案で確認できません。")
                existing = connection.execute(
                    "SELECT id FROM source_review_requests WHERE order_id = ? AND trial_id = ? "
                    "AND run_id = ? AND option_id = ?",
                    (order_id, row["current_trial_id"], body["runId"], body["optionId"]),
                ).fetchone()
                if existing is not None:
                    raise ApiError(409, "review_already_requested", "この試行の同じ案は確認依頼済みです。")
                connection.execute(
                    "INSERT INTO source_review_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"source-review-{uuid4().hex}", order_id, row["current_trial_id"],
                     body["runId"], body["optionId"], body["actorRole"], "contract_reviewer",
                     body["reason"].strip(), _json(source_refs), FIXTURE_VERSION, "pending", _now()),
                )
                result = self._detail(connection, order_id)
                connection.execute(
                    "INSERT INTO operations (operation_id, order_id, operation_type, request_json, "
                    "response_json, trial_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (operation_id, order_id, "cost_source_review", request_json,
                     _json(result), row["current_trial_id"]),
                )
                return result

    def respond_cost_source_review(self, order_id: str, raw: object) -> dict[str, Any]:
        """Record what the synthetic reviewer could verify from the saved run.

        This changes only the handoff. It cannot establish a cost bearer or
        authorize an approval, reservation, or shipment.
        """
        body = self._payload(raw, {"reviewId", "trialId", "runId", "optionId",
                                   "outcome", "reason", "expectedVersion",
                                   "operationId", "actorRole"})
        operation_id = _operation_id(body["operationId"])
        expected = _expected_version(body["expectedVersion"])
        if body["actorRole"] != "contract_reviewer":
            raise ApiError(403, "forbidden", "契約確認担当役の回答として登録してください。")
        if (not isinstance(body["outcome"], str) or
                body["outcome"] not in {"original_source_unavailable", "source_clause_no_payer"}):
            raise ApiError(422, "unsupported_review_outcome",
                           "原文欠落または保存契約条項に負担先がない回答を選んでください。")
        if not isinstance(body["reason"], str) or not body["reason"].strip() or len(body["reason"]) > 1000:
            raise ApiError(400, "validation_error", "確認できない理由を1〜1000文字で入力してください。")
        for key in ("reviewId", "trialId", "runId", "optionId"):
            if not isinstance(body[key], str) or not body[key]:
                raise ApiError(400, "validation_error", f"{key} を指定してください。")
        request_json = _json(body)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._get_order_row(connection, order_id)
                replay = self._replay(connection, operation_id, order_id,
                                      "cost_source_review_response", request_json)
                if replay is not None:
                    return replay
                if body["trialId"] != row["current_trial_id"]:
                    raise ApiError(409, "stale_trial", "前回の試行の確認依頼には回答できません。")
                self._check_version(row, expected)
                review = connection.execute(
                    "SELECT * FROM source_review_requests WHERE id = ? AND order_id = ?",
                    (body["reviewId"], order_id),
                ).fetchone()
                if review is None:
                    raise ApiError(404, "review_not_found", "確認依頼が見つかりません。")
                if (review["trial_id"] != body["trialId"] or
                        review["run_id"] != body["runId"] or
                        review["option_id"] != body["optionId"]):
                    raise ApiError(409, "review_identity_mismatch",
                                   "確認依頼の試行・run・案IDを読み直してください。")
                if review["assigned_to_role"] != body["actorRole"]:
                    raise ApiError(403, "forbidden", "この確認依頼の担当役として登録してください。")
                if review["status"] != "pending":
                    raise ApiError(409, "review_already_answered", "確認依頼は回答済みです。")
                if row["status"] != "awaiting_approval":
                    raise ApiError(409, "review_not_available", "承認待ちの確認依頼だけ回答できます。")
                _, snapshot = self._agent_proposal(connection, order_id,
                                                    body["runId"], body["optionId"])
                self._check_agent_snapshot(connection, row, snapshot,
                                           run_id=body["runId"], response_recorded=True)
                frozen_contract = self._frozen_contract(connection, review)
                integrity = self._contract_tool_response_integrity(connection, review, frozen_contract)
                if integrity == "invalid":
                    raise ApiError(409, "tool_response_integrity_invalid",
                                   "保存された契約Tool応答の整合性を確認できません。")
                if body["outcome"] == "source_clause_no_payer" and frozen_contract is None:
                    raise ApiError(422, "frozen_contract_unavailable",
                                   "保存runの契約Tool原文・hash・版を照合できません。")
                old_source_texts = snapshot.get("sourceTexts")
                old_source_texts_present = isinstance(old_source_texts, dict) and all(
                    isinstance(old_source_texts.get(ref), str) and old_source_texts[ref].strip()
                    for ref in json.loads(review["source_refs_json"]))
                if (body["outcome"] == "original_source_unavailable" and
                        (frozen_contract is not None or old_source_texts_present)):
                    raise ApiError(409, "original_source_present",
                                   "保存runに照合済みの契約原文があります。原文不足として回答できません。")
                # The historical v3 clause says only that split delivery
                # needs customer consent. An unknown future clause is held.
                if (body["outcome"] == "source_clause_no_payer" and
                        not frozen_contract["noPayerClauseReviewed"]):
                    raise ApiError(422, "payer_clause_unreviewed",
                                   "この契約条項から費用負担先を判断できません。")
                timestamp = _now()
                connection.execute(
                    "INSERT INTO source_review_responses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (review["id"], order_id, row["current_trial_id"], body["runId"],
                     body["optionId"], body["actorRole"], body["outcome"],
                     body["reason"].strip(), timestamp),
                )
                connection.execute(
                    "UPDATE source_review_requests SET status = ? "
                    "WHERE id = ? AND status = 'pending'", (body["outcome"], review["id"]),
                )
                result = self._detail(connection, order_id)
                connection.execute(
                    "INSERT INTO operations (operation_id, order_id, operation_type, request_json, "
                    "response_json, trial_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (operation_id, order_id, "cost_source_review_response", request_json,
                     _json(result), row["current_trial_id"]),
                )
                return result

    def record_customer_response(self, order_id: str, raw: object) -> dict[str, Any]:
        body = self._payload(raw, {"selectedOptionId", "acknowledgedTerms", "acceptedSplit",
                                   "expectedVersion", "operationId", "actorRole"},
                             {"note", "acceptedExpressCost"})
        operation_id = _operation_id(body["operationId"])
        expected = _expected_version(body["expectedVersion"])
        if body["actorRole"] != "order_clerk":
            raise ApiError(403, "forbidden", "受注担当者の操作として登録してください。")
        if not isinstance(body["acceptedSplit"], bool):
            raise ApiError(400, "validation_error", "acceptedSplit は真偽値にしてください。")
        if (not isinstance(body["selectedOptionId"], str) or
                body["selectedOptionId"] not in {item["id"] for item in OPTIONS}):
            raise ApiError(400, "validation_error", "顧客が選んだ対処案を確認してください。")
        selected_option = next(item for item in OPTIONS if item["id"] == body["selectedOptionId"])
        terms = self._option_terms(selected_option)
        if _json(body["acknowledgedTerms"]) != _json(terms):
            raise ApiError(422, "option_terms_mismatch", "顧客回答に記録する数量・納品日時・費用が選択案と異なります。")
        accepted_express_cost = body.get("acceptedExpressCost", False)
        if not isinstance(accepted_express_cost, bool):
            raise ApiError(400, "validation_error", "acceptedExpressCost は真偽値にしてください。")
        note = body.get("note", "")
        if not isinstance(note, str) or len(note) > 1000:
            raise ApiError(400, "validation_error", "顧客回答のメモは1000文字以下にしてください。")
        request_json = _json(body)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                self._get_order_row(connection, order_id)
                replay = self._replay(connection, operation_id, order_id, "customer_response", request_json)
                if replay is not None:
                    return replay
                row = self._get_order_row(connection, order_id)
                self._check_version(row, expected)
                active_agent_run = connection.execute(
                    "SELECT 1 FROM agent_runs WHERE order_id = ? AND trial_id = ? "
                    "AND status IN ('queued', 'running') LIMIT 1",
                    (order_id, row["current_trial_id"]),
                ).fetchone()
                if active_agent_run is not None:
                    raise ApiError(409, "trial_in_progress",
                                   "Agent調査中は固定比較案への顧客回答を登録できません。調査終了後に保存結果を確認してください。")
                if row["status"] == "shipment_instructed":
                    raise ApiError(409, "already_instructed", "出荷指示の確定後は顧客回答を変更できません。")
                existing_response = connection.execute(
                    "SELECT run_id FROM customer_responses WHERE order_id = ?", (order_id,)
                ).fetchone()
                if existing_response is not None and existing_response["run_id"] is not None:
                    raise ApiError(409, "agent_response_locked", "Agent案の顧客回答は固定比較案へ切り替えできません。")
                completed_agent_run = connection.execute(
                    "SELECT 1 FROM agent_runs WHERE order_id = ? AND trial_id = ? "
                    "AND status IN ('completed', 'draft_ready') LIMIT 1",
                    (order_id, row["current_trial_id"]),
                ).fetchone()
                if completed_agent_run is not None:
                    raise ApiError(409, "agent_draft_requires_agent_response",
                                   "保存済みAgent案の回答にはAgent案IDを指定してください。")
                connection.execute(
                    "INSERT INTO customer_responses "
                    "(order_id, selected_option_id, acknowledged_terms_json, accepted_split, "
                    "accepted_express_cost, note, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(order_id) DO UPDATE SET run_id=NULL, selected_option_id=excluded.selected_option_id, "
                    "acknowledged_terms_json=excluded.acknowledged_terms_json, "
                    "accepted_split=excluded.accepted_split, "
                    "accepted_express_cost=excluded.accepted_express_cost, "
                    "note=excluded.note, recorded_at=excluded.recorded_at",
                    (order_id, body["selectedOptionId"], _json(terms), int(body["acceptedSplit"]),
                     int(accepted_express_cost), note, _now()),
                )
                connection.execute(
                    "UPDATE orders SET status = 'awaiting_approval', version = version + 1 WHERE id = ?", (order_id,)
                )
                result = self._detail(connection, order_id)
                connection.execute(
                    "INSERT INTO operations (operation_id, order_id, operation_type, request_json, "
                    "response_json, trial_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (operation_id, order_id, "customer_response", request_json,
                     _json(result), row["current_trial_id"]),
                )
                return result

    def decide(self, order_id: str, raw: object) -> dict[str, Any]:
        body = self._payload(raw, {"optionId", "reason", "expectedVersion", "operationId", "actorRole"})
        operation_id = _operation_id(body["operationId"])
        expected = _expected_version(body["expectedVersion"])
        if body["actorRole"] != "shipping_manager":
            raise ApiError(403, "forbidden", "出荷責任者の操作として承認してください。")
        if not isinstance(body["optionId"], str):
            raise ApiError(400, "validation_error", "対処案を選んでください。")
        option = next((item for item in OPTIONS if item["id"] == body["optionId"]), None)
        if option is None:
            raise ApiError(400, "validation_error", "対処案を選んでください。")
        if not isinstance(body["reason"], str) or not body["reason"].strip() or len(body["reason"]) > 1000:
            raise ApiError(400, "validation_error", "承認理由を1〜1000文字で入力してください。")
        request_json = _json(body)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._get_order_row(connection, order_id)
                replay = self._replay(connection, operation_id, order_id, "decision", request_json)
                if replay is not None:
                    return replay
                self._check_version(row, expected)
                if row["status"] == "shipment_instructed":
                    raise ApiError(409, "already_instructed", "出荷指示は確定済みです。")
                response = connection.execute(
                    "SELECT selected_option_id, acknowledged_terms_json, accepted_split, accepted_express_cost "
                    "FROM customer_responses WHERE order_id = ?",
                    (order_id,),
                ).fetchone()
                if response is None:
                    raise ApiError(422, "customer_response_required", "出荷案の承認前に顧客回答が必要です。")
                agent_response = connection.execute(
                    "SELECT run_id FROM customer_responses WHERE order_id = ?", (order_id,)
                ).fetchone()
                if agent_response is not None and agent_response["run_id"] is not None:
                    raise ApiError(422, "agent_option_mismatch", "Agent案の顧客回答はAgent案の承認経路で確認してください。")
                if response["selected_option_id"] != option["id"]:
                    raise ApiError(422, "selected_option_mismatch", "承認する案が顧客回答で選んだ案と異なります。")
                if response["acknowledged_terms_json"] != _json(self._option_terms(option)):
                    raise ApiError(409, "option_terms_changed", "顧客回答後に選択案の数量・納品日時・費用が変わりました。")
                if option["id"] == "split-delivery" and not response["accepted_split"]:
                    raise ApiError(422, "consent_required", "分納の承認には顧客の承諾が必要です。")
                if option["id"] == "express-consolidated":
                    if not response["accepted_express_cost"]:
                        raise ApiError(422, "express_cost_consent_required", "特急費用への顧客同意が必要です。")
                    raise ApiError(422, "consolidation_source_missing",
                                   "二倉庫の荷物を一括輸送できる便の資料がありません。")
                if sum(item["quantity"] for item in option["allocations"]) != row["quantity"]:
                    raise ApiError(422, "invalid_allocation", "対処案の数量が受注数量と一致しません。")
                # Fixed comparison options also expire at the actual decision
                # time. The source list is synthetic; no external TMS is used.
                detected = datetime.fromisoformat(SCENARIO_TIME)
                now = datetime.now(timezone.utc)
                for source_id in option["sourceRefs"]:
                    carrier = CARRIER_SOURCES.get(source_id)
                    if carrier is None:
                        continue
                    pickup = datetime.fromisoformat(carrier["pickupCutoffAt"])
                    if pickup <= detected:
                        pickup = datetime.fromisoformat(carrier["nextPickupAt"])
                    if pickup <= now or datetime.fromisoformat(carrier["earliestDeliveryAt"]) <= now:
                        raise ApiError(422, "carrier_cutoff", "配送便の集荷または納品日時を過ぎています。")
                for allocation in option["allocations"]:
                    stock = connection.execute(
                        "SELECT available FROM inventory WHERE warehouse_id = ?",
                        (allocation["warehouseId"],),
                    ).fetchone()
                    if stock is None or stock["available"] < allocation["quantity"]:
                        raise ApiError(409, "insufficient_inventory", "現在の在庫では出荷できません。")
                timestamp = _now()
                for allocation in option["allocations"]:
                    connection.execute(
                        "UPDATE inventory SET available = available - ? WHERE warehouse_id = ?",
                        (allocation["quantity"], allocation["warehouseId"]),
                    )
                    connection.execute(
                        "INSERT INTO reservations VALUES (?, ?, ?)",
                        (order_id, allocation["warehouseId"], allocation["quantity"]),
                    )
                    connection.execute(
                        "INSERT INTO shipment_instructions VALUES (?, ?, ?, ?, ?)",
                        (order_id, allocation["warehouseId"], allocation["quantity"],
                         allocation["deliveryAt"], timestamp),
                    )
                connection.execute(
                    "INSERT INTO approvals (order_id, option_id, reason, actor_role, approved_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (order_id, option["id"], body["reason"], body["actorRole"], timestamp),
                )
                connection.execute(
                    "UPDATE orders SET status = 'shipment_instructed', version = version + 1 WHERE id = ?", (order_id,)
                )
                result = self._detail(connection, order_id)
                connection.execute(
                    "INSERT INTO operations (operation_id, order_id, operation_type, request_json, "
                    "response_json, trial_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (operation_id, order_id, "decision", request_json,
                     _json(result), row["current_trial_id"]),
                )
                return result


class RunController:
    """Local worker boundary for an explicitly configured Agent runner.

    The command-line server wires the real adapter; test servers may inject a
    scripted runner, whose output is labeled test_double in saved records.
    """

    def __init__(self, store: OrderStore, *, config_loader: Callable[[], Any],
                 runner: Callable[..., Any], execution_mode: str,
                 timeout_seconds: float = 180,
                 agent_version: str = AGENT_VERSION):
        if not 1 <= timeout_seconds <= 900:
            raise ValueError("Agent実行全体の期限は1〜900秒にしてください。")
        self.store = store
        self.config_loader = config_loader
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        self.agent_version = agent_version
        if execution_mode not in {"live_model", "test_double"}:
            raise ValueError("Agent実行モードを確認してください。")
        self.execution_mode = execution_mode

    def submit(self, order_id: str, payload: object) -> dict[str, Any]:
        body = self.store._payload(payload, {"operationId", "requestText", "expectedVersion"},
                                   {"humanWaitSeconds"})
        operation_id = _operation_id(body["operationId"])
        human_wait_seconds = body.get("humanWaitSeconds", 0)
        if type(human_wait_seconds) is not int or not 0 <= human_wait_seconds <= 600:
            raise ApiError(400, "validation_error", "人の操作待機は0〜600秒で指定してください。")
        request_text, expected_version = self.store._validate_request(
            body["requestText"], body["expectedVersion"])
        existing = self.store.find_run(order_id, operation_id,
                                       request_text=request_text, expected_version=expected_version,
                                       human_wait_seconds=human_wait_seconds)
        if existing is not None:
            return existing
        try:
            config = self.config_loader()
        except Exception as error:
            if getattr(error, "code", None) == "model_not_configured":
                raise ApiError(503, "model_not_configured", "AI調査のモデルが設定されていません。") from None
            raise ApiError(503, "agent_unavailable", "AI調査を開始できません。") from None
        route = getattr(config, "route", None)
        model_id = getattr(config, "model_id", None)
        if route not in {"direct", "gateway"} or not isinstance(model_id, str) or not model_id:
            raise ApiError(503, "model_not_configured", "AI調査のモデルが設定されていません。")
        if human_wait_seconds and (
                getattr(config, "run_timeout_seconds", 0) < human_wait_seconds + 240 or
                self.timeout_seconds < human_wait_seconds + 240):
            raise ApiError(503, "agent_timeout_configuration",
                           "Agent実行期限は人の待機時間より240秒以上長く設定してください。")
        run, created = self.store.create_run(order_id, operation_id, route=route, model_id=model_id,
                                             execution_mode=self.execution_mode,
                                             request_text=request_text, expected_version=expected_version,
                                             human_wait_seconds=human_wait_seconds,
                                             agent_version=self.agent_version)
        if created:
            try:
                Thread(target=self._execute, args=(run["id"], order_id, config), daemon=True,
                       name=f"order-run-{run['id'][-8:]}").start()
            except RuntimeError:
                self.store.start_run(run["id"])
                self.store.finish_run(run["id"], "failed", error_code="worker_unavailable")
                return self.store.get_run(run["id"])
        return run

    def _execute(self, run_id: str, order_id: str, config: Any) -> None:
        if not self.store.start_run(run_id):
            return
        self.store.append_run_event(run_id, kind="status", actor="system",
                                    message="AI調査を開始しました。")
        timer = Timer(self.timeout_seconds, lambda: self.store.finish_run(
            run_id, "timed_out", error_code="execution_timeout"))
        timer.daemon = True
        timer.start()

        def on_event(**event: object) -> None:
            if event.get("kind") == "tool_response_captured":
                self.store.capture_tool_response(
                    run_id, tool_name=event["tool_name"],
                    response_text=event["response_text"],
                    response_sha256=event["response_sha256"],
                    source_ids=event["source_ids"],
                    fixture_version=event["fixture_version"],
                )
            elif event.get("kind") == "source_ref_attempt_captured":
                self.store.capture_source_ref_attempt(
                    run_id, attempt_no=event["attempt_no"],
                    proposal_index=event["proposal_index"],
                    submitted_refs=event["submitted_refs"],
                    submitted_shape=event["submitted_shape"],
                    expected_refs=event["expected_refs"], outcome=event["outcome"],
                    error_code=event.get("error_code"),
                    field_path=event.get("field_path"), reason=event.get("reason"),
                )
            else:
                self.store.append_run_event(run_id, **event)

        try:
            snapshot = self.store.get_detail(order_id)
            saved_run = self.store.get_run(run_id)
            snapshot["requestText"] = saved_run["requestText"]
            snapshot["humanWaitSeconds"] = saved_run["humanWaitSeconds"]
            result = asyncio.run(asyncio.wait_for(
                self.runner(snapshot, config, run_id=run_id, on_event=on_event),
                timeout=self.timeout_seconds,
            ))
            expected_generation = "live_model" if self.execution_mode == "live_model" else "test_double"
            if (not isinstance(result, dict) or result.get("runId") != run_id or
                    result.get("orderId") != order_id or result.get("generationMode") != expected_generation or
                    result.get("shipmentChanged") is not False or
                    result.get("requiresHumanApproval") is not True or
                    not isinstance(result.get("draftBody"), str) or not result["draftBody"].strip() or
                    not isinstance(result.get("draftPlan"), dict)):
                self.store.finish_run(run_id, "failed", error_code="invalid_agent_result")
                return
            final_reply_mode = result.get("finalReplyMode", "model_final_reply_received"
                                          if expected_generation == "live_model" else "test_double_reply")
            if (final_reply_mode not in {"model_final_reply_received", "tool_submitted_model_final_timeout",
                                         "test_double_reply"} or
                    (final_reply_mode == "model_final_reply_received" and
                     result.get("modelFinalReplyReceived") is False) or
                    (final_reply_mode == "tool_submitted_model_final_timeout" and
                     (expected_generation != "live_model" or result.get("modelFinalReplyReceived") is not False)) or
                    (final_reply_mode == "test_double_reply" and expected_generation != "test_double")):
                self.store.finish_run(run_id, "failed", error_code="invalid_agent_result")
                return
            model_final_reply = (result["draftBody"] if expected_generation == "live_model" and
                                 final_reply_mode == "model_final_reply_received" else None)
            provider_key = getattr(config, "api_key", None)
            if model_final_reply is not None and (
                    not _valid_model_final_reply(model_final_reply) or
                    (isinstance(provider_key, str) and bool(provider_key) and
                     provider_key in model_final_reply)):
                self.store.finish_run(run_id, "failed", error_code="invalid_agent_result")
                return
            plan = result["draftPlan"]
            if any(not isinstance(plan.get(field), str) or not plan[field].strip()
                   for field in ("recommendation", "alternatives", "customerQuestion")):
                self.store.finish_run(run_id, "failed", error_code="invalid_agent_result")
                return
            saved_run = self.store.get_run(run_id)
            tool_responses = self.store.get_tool_responses(run_id)
            if (expected_generation == "live_model" and
                    {item["toolName"] for item in tool_responses} !=
                    {"read_order_inventory", "read_carriers_contract"}):
                self.store.finish_run(run_id, "failed", error_code="tool_response_missing")
                return
            snapshot["toolResponses"] = tool_responses
            if final_reply_mode == "tool_submitted_model_final_timeout" and sum(
                    event["kind"] == "draft_submitted" and event["actor"] == "OrderInvestigator"
                    for event in saved_run["events"]) != 1:
                self.store.finish_run(run_id, "failed", error_code="draft_submission_event_missing")
                return
            try:
                projected = validate_structured_draft(snapshot, plan, saved_run["events"])
            except DraftGroundingError as error:
                self.store.append_run_event(
                    run_id, kind="draft_rejected", actor="system",
                    message=(f"業務APIで案を拒否しました: {error.code} "
                             f"({error.field_path}: {error.reason})"),
                )
                self.store.finish_run(run_id, "failed", error_code=error.code)
                return
            # Keep the model's bounded final reply separately. The draft body
            # remains API-owned text projected from checked structured facts.
            no_cost = next(item for item in projected["proposals"] if item["additionalCostJpy"] == 0)
            express = next(item for item in projected["proposals"] if item["additionalCostJpy"] > 0)
            for proposal, suffix in ((no_cost, "normal"), (express, "express")):
                proposal["id"] = f"{run_id}:{suffix}"
                proposal["runId"] = run_id
                proposal["acknowledgedTerms"] = self.store._agent_terms(proposal)
                proposal["approvalAvailability"] = (
                    "synthetic_manager_review_required" if suffix == "normal"
                    else "blocked_missing_cost_bearer_source"
                )
            def describe(item: dict[str, Any]) -> str:
                parts = [f"{shipment['warehouseId']} {shipment['quantityBoxes']}箱 "
                         f"{shipment['deliveryAt']} ({shipment['carrierSourceId']})"
                         for shipment in item["shipments"]]
                return "、".join(parts) + f"。追加費用 {item['additionalCostJpy']}円。"
            projected["recommendation"] = "通常便を使う別送比較案。" + describe(no_cost)
            projected["alternatives"] = "特急便を使う別送案。" + describe(express)
            projected["customerQuestion"] = " ".join(projected["customerQuestions"])
            projected["recommendationSelectionMode"] = "api_fixed_order_for_legacy_ui"
            draft_ready = final_reply_mode == "tool_submitted_model_final_timeout"
            self.store.finish_run(run_id, "draft_ready" if draft_ready else "completed",
                                  source_snapshot=snapshot,
                                  model_final_reply=model_final_reply, draft={
                "body": ("AgentのTool提出済み2案を業務APIが資料照合しました。"
                         "モデルの最終返答は時間切れで未取得です。顧客未回答・出荷未承認です。"
                         if draft_ready else projected["recommendation"] + " " +
                         projected["alternatives"] + " " + " ".join(projected["limitations"])),
                "plan": projected,
                "contractReviewStatus": "not_evaluated",
                "businessReviewStatus": "unverified",
                "generationMode": expected_generation, "requiresHumanApproval": True,
                "shipmentChanged": False,
                "modelProseStored": model_final_reply is not None,
                "modelFinalReplyReceived": final_reply_mode == "model_final_reply_received",
                "finalReplyMode": final_reply_mode,
                "projectionMode": "api_from_model_structured_data",
                "sourceReads": [{"sourceId": event["sourceId"]} for event in saved_run["events"]
                                if event["kind"] == "source_read"],
                "managerEvents": result.get("managerEvents", []),
                "planBusinessValidation": projected["validation"],
            })
        except (TimeoutError, asyncio.TimeoutError):
            self.store.finish_run(run_id, "timed_out", error_code="execution_timeout")
        except Exception as error:
            code = getattr(error, "code", None)
            safe_code = code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", code) else "agent_run_failed"
            self.store.finish_run(run_id, "failed", error_code=safe_code)
        finally:
            timer.cancel()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: object, store: OrderStore, runs: RunController | None = None,
                 dist_dir: Path | None = None, inventory_auth: InventoryAuthorizer | None = None,
                 **kwargs: object):
        self.store = store
        self.runs = runs
        self.inventory_auth = inventory_auth
        self.dist_dir = dist_dir if dist_dir is not None else DIST
        super().__init__(*args, directory=str(self.dist_dir), **kwargs)

    def log_message(self, *_args: object) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        content = _json(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def _error(self, error: ApiError) -> None:
        self._send_json(error.status, {"error": error.code, "message": str(error)})

    def _read_json(self) -> object:
        if self.headers.get_content_type() != "application/json":
            raise ApiError(400, "validation_error", "Content-Type は application/json にしてください。")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ApiError(400, "validation_error", "Content-Length を指定してください。") from None
        if length < 1 or length > 65536:
            raise ApiError(400, "validation_error", "65,536バイト以下のJSONを送信してください。")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise ApiError(400, "validation_error", "JSONの形式を確認してください。") from None

    def _path(self) -> list[str]:
        return [part for part in unquote(urlsplit(self.path).path).split("/") if part]

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parts = self._path()
        try:
            if parts == ["api", "health"]:
                model_configured = False
                if self.runs is not None:
                    try:
                        self.runs.config_loader()
                        model_configured = True
                    except Exception:
                        pass
                self._send_json(200, {"status": "ok", "dataMode": "synthetic_fixture",
                                      "authenticationMode": "none_local_trial",
                                      "modelConfigured": model_configured,
                                      "inventoryCountAuthenticationMode": (
                                          "keycloak_oidc_openfga_local_trial" if self.inventory_auth
                                          else "unavailable"),
                                      "agentExecutionMode": (
                                          "single_agent_live_model" if self.runs and
                                          self.runs.execution_mode == "live_model" and
                                          self.runs.agent_version.endswith("-single")
                                          else self.runs.execution_mode if self.runs else "not_connected"
                                      ),
                                      "supportedRequestText": SUPPORTED_REQUEST_TEXT,
                                      "currentTrialId": self.store.current_trial_id(),
                                      **self.store.fixture_validity()})
            elif parts == ["api", "orders"]:
                self._send_json(200, self.store.list_orders())
            elif len(parts) == 3 and parts[:2] == ["api", "orders"]:
                self._send_json(200, self.store.get_detail(parts[2]))
            elif len(parts) == 4 and parts[:2] == ["api", "orders"] and parts[3] == "options":
                self._send_json(200, self.store.get_options(parts[2]))
            elif len(parts) == 4 and parts[:2] == ["api", "orders"] and parts[3] == "sources":
                self._send_json(200, self.store.get_sources(parts[2]))
            elif len(parts) == 4 and parts[:2] == ["api", "orders"] and parts[3] == "agent-runs":
                self._send_json(200, self.store.list_runs(parts[2]))
            elif len(parts) == 5 and parts[:2] == ["api", "orders"] and parts[3] == "trial-archives":
                self._send_json(200, self.store.get_archive(parts[2], parts[4]))
            elif len(parts) == 3 and parts[:2] == ["api", "agent-runs"]:
                self._send_json(200, {"run": self.store.get_run(parts[2])})
            elif parts and parts[0] == "api":
                raise ApiError(404, "not_found", "APIが見つかりません。")
            elif parts == ["demo"] and self.dist_dir.is_dir():
                self.path = "/index.html"
                super().do_GET()
            elif self.dist_dir.is_dir():
                super().do_GET()
            else:
                raise ApiError(503, "frontend_not_built", "画面のビルドがまだありません。")
        except ApiError as error:
            self._error(error)

    def do_POST(self) -> None:
        parts = self._path()
        try:
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host', '')}":
                raise ApiError(403, "origin_forbidden", "異なるOriginからの更新は受け付けません。")
            if len(parts) != 4 or parts[:2] != ["api", "orders"]:
                raise ApiError(404, "not_found", "APIが見つかりません。")
            body = self._read_json()
            if parts[3] == "customer-response":
                result = self.store.record_customer_response(parts[2], body)
            elif parts[3] == "agent-customer-response":
                result = self.store.record_agent_customer_response(parts[2], body)
            elif parts[3] == "decision":
                result = self.store.decide(parts[2], body)
            elif parts[3] == "agent-decision":
                result = self.store.decide_agent_proposal(parts[2], body)
            elif parts[3] == "cost-source-review":
                result = self.store.request_cost_source_review(parts[2], body)
            elif parts[3] == "cost-source-review-response":
                result = self.store.respond_cost_source_review(parts[2], body)
            elif parts[3] == "new-trial":
                result = self.store.new_trial(parts[2], body)
            elif parts[3] == "inventory-count":
                if self.inventory_auth is None:
                    raise ApiError(503, "inventory_auth_unavailable", "在庫補正の認可設定がありません。")
                if not isinstance(body, dict):
                    raise ApiError(400, "validation_error", "操作の入力項目を確認してください。")
                try:
                    actor = self.inventory_auth.authorize(self.headers, body.get("warehouseId"))
                except InventoryAccessError as error:
                    raise ApiError(error.status, error.code, error.message) from None
                result = self.store.record_inventory_count(parts[2], body, actor)
            elif parts[3] == "agent-runs":
                if self.runs is None:
                    raise ApiError(503, "agent_unavailable", "AI調査はまだ接続されていません。")
                self._send_json(200, {"run": self.runs.submit(parts[2], body)})
                return
            else:
                raise ApiError(404, "not_found", "APIが見つかりません。")
            self._send_json(200, result)
        except ApiError as error:
            self._error(error)


def make_server(port: int = 5340, db_path: str | Path = DEFAULT_DB,
                runs_factory: Callable[[OrderStore], RunController] | None = None,
                *, recover_interrupted: bool = False,
                dist_dir: str | Path | None = None,
                inventory_auth: InventoryAuthorizer | None = None) -> ThreadingHTTPServer:
    store = OrderStore(db_path)
    if recover_interrupted:
        store.recover_interrupted_runs()
    runs = runs_factory(store) if runs_factory is not None else None
    return ThreadingHTTPServer(("127.0.0.1", port), partial(
        Handler, store=store, runs=runs, inventory_auth=inventory_auth,
        dist_dir=Path(dist_dir).resolve() if dist_dir is not None else None))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5340)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--dist", type=Path, default=DIST)
    parser.add_argument("--inventory-auth-config", type=Path,
                        help="隔離試行用のKeycloak/OpenFGA設定JSON")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--single-agent", action="store_true",
                       help="前回採用した単一MAF Agentを局所実モデルで動かします。")
    modes.add_argument("--magentic-agent", action="store_true",
                       help="時間切れで不採用のMagentic経路を隔離再試行します。")
    args = parser.parse_args()
    inventory_auth = (InventoryAuthorizer.from_config(args.inventory_auth_config)
                      if args.inventory_auth_config else None)
    if args.single_agent:
        from order_agent import OrderAgentConfig
        from single_agent import run_single_order
        def runs_factory(store: OrderStore) -> RunController:
            async def waiting_runner(snapshot: dict, config: OrderAgentConfig, *,
                                     run_id: str, on_event: Callable[..., object]) -> dict:
                return await run_single_order(
                    snapshot, config, run_id=run_id, on_event=on_event,
                    run_state=lambda: store.get_run_state(run_id),
                    human_wait_seconds=snapshot["humanWaitSeconds"],
                )
            return RunController(
                store, config_loader=OrderAgentConfig.from_env, runner=waiting_runner,
                execution_mode="live_model",
                timeout_seconds=int(os.environ.get("ORDER_AGENT_RUN_TIMEOUT_SECONDS", "180")),
                agent_version=AGENT_VERSION + "-single",
            )
    else:
        from order_agent import OrderAgentConfig, run_initial_order
        runs_factory = lambda store: RunController(
            store, config_loader=OrderAgentConfig.from_env, runner=run_initial_order,
            execution_mode="live_model", timeout_seconds=180,
        )
    server = make_server(args.port, args.db, runs_factory=runs_factory,
                         recover_interrupted=True, dist_dir=args.dist,
                         inventory_auth=inventory_auth)
    try:
        print(f"Order exception trial: http://127.0.0.1:{server.server_port}/", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
