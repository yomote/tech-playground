"""Loopback-only synthetic order exception API.

The fixed options are fixtures, not AI output. The role field is a local trial
stand-in and must not be treated as authentication or an OpenFGA decision.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
from threading import Thread, Timer
from typing import Any, Callable
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

from order_agent import AGENT_VERSION
from source_fixtures import CARRIER_SOURCES, CONTRACT_SOURCE


ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
DEFAULT_DB = ROOT / ".runtime" / "orders.sqlite3"
ORDER_ID = "SO-24017"
SCENARIO_TIME = "2026-10-19T17:00:00+09:00"
OPTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "split-delivery",
        "title": "25箱を先行し、残り15箱を翌々日に納品",
        "summary": "倉庫Aから25箱を期限内に納品し、倉庫Bから15箱を翌々日に納品します。分納には顧客の承諾が必要です。",
        "allocations": [
            {"warehouseId": "warehouse-a", "quantity": 25, "deliveryAt": "2026-10-20T10:00:00+09:00"},
            {"warehouseId": "warehouse-b", "quantity": 15, "deliveryAt": "2026-10-21T10:00:00+09:00"},
        ],
        "additionalCostJpy": 0,
        "requiresCustomerConsent": True,
        "customerConsentField": "acceptedSplit",
        "sourceRefs": ["stock-warehouse-a", "stock-warehouse-b",
                       "carrier-a-normal-v1", "carrier-b-normal-v1", "contract-split-v1"],
        "generationMode": "fixed_fixture",
        "dataMode": "synthetic_fixture",
    },
    {
        "id": "express-consolidated",
        "title": "追加の専用便で40箱を期限内に一括納品",
        "summary": "倉庫Aの25箱と倉庫Bの15箱を専用便でまとめ、翌日10時までに納品します。",
        "allocations": [
            {"warehouseId": "warehouse-a", "quantity": 25, "deliveryAt": "2026-10-20T10:00:00+09:00"},
            {"warehouseId": "warehouse-b", "quantity": 15, "deliveryAt": "2026-10-20T10:00:00+09:00"},
        ],
        "additionalCostJpy": 78000,
        "requiresCustomerConsent": False,
        "customerConsentField": None,
        "sourceRefs": ["stock-warehouse-a", "stock-warehouse-b",
                       "carrier-a-normal-v1", "carrier-b-express-v1", "contract-split-v1"],
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
            connection.executescript("""
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
                    accepted_split INTEGER NOT NULL, note TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    order_id TEXT PRIMARY KEY REFERENCES orders(id),
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
                    error_code TEXT,
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
            """)
            with connection:
                connection.execute(
                    "INSERT OR IGNORE INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ORDER_ID, "東都部品販売（合成顧客）", "産業用部品", "PART-40", 40,
                     "2026-10-20T10:00:00+09:00", "exception_detected", 1),
                )
                connection.execute("INSERT OR IGNORE INTO inventory VALUES ('warehouse-a', 25)")
                connection.execute("INSERT OR IGNORE INTO inventory VALUES ('warehouse-b', 20)")

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
            "exception_detected": ("受注担当者", "顧客に分納可否を確認するか、追加費用を伴う一括納品案を選んでください。"),
            "awaiting_approval": ("出荷責任者", "顧客回答と現在の在庫を確認し、出荷案を承認してください。"),
            "shipment_instructed": ("倉庫担当者", "保存された引当と出荷指示を確認してください。"),
        }[row["status"]]
        return {
            "id": row["id"], "customerName": row["customer_name"],
            "itemName": row["item_name"], "sku": row["sku"],
            "quantity": row["quantity"], "dueAt": row["due_at"],
            "status": row["status"], "version": row["version"],
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
                    "fixtureVersion": "1.0", "orderVersion": row["version"],
                    "recordedAt": SCENARIO_TIME, "capturedAt": captured_at,
                    "sku": row["sku"], "quantityBoxes": row["quantity"],
                    "dueAt": row["due_at"], "dataMode": "synthetic_fixture",
                },
                "inventory": [
                    {
                        "sourceId": f"stock-{item['warehouse_id']}",
                        "fixtureVersion": "1.0", "versionKnown": False,
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
        result: dict[str, Any] = {
            "id": row["id"], "orderId": row["order_id"], "status": row["status"],
            "agentVersion": row["agent_version"], "executionMode": row["execution_mode"],
            "modelRoute": row["route"], "modelId": row["model_id"],
            "dataMode": "synthetic_fixture", "startedAt": row["started_at"],
            "endedAt": row["ended_at"],
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
        if row["error_code"] is not None:
            result["errorCode"] = row["error_code"]
        return result

    def find_run(self, order_id: str, operation_id: object) -> dict[str, Any] | None:
        _operation_id(operation_id)
        with closing(self._connect()) as connection:
            self._get_order_row(connection, order_id)
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE order_id = ? AND operation_id = ?",
                (order_id, operation_id),
            ).fetchone()
            return self._run_presentation(connection, row) if row else None

    def create_run(self, order_id: str, operation_id: object, *, route: str,
                   model_id: str, execution_mode: str,
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
                    return self._run_presentation(connection, existing), False
                row = self._get_order_row(connection, order_id)
                if row["status"] == "shipment_instructed":
                    raise ApiError(409, "already_instructed", "出荷指示済みの受注ではAI調査を開始できません。")
                run_id = f"order-run-{uuid4().hex}"
                connection.execute(
                    "INSERT INTO agent_runs VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, NULL, NULL, NULL)",
                    (run_id, order_id, operation_id, agent_version, execution_mode, route, model_id, _now()),
                )
                created = connection.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
                return self._run_presentation(connection, created), True

    def list_runs(self, order_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            self._get_order_row(connection, order_id)
            rows = connection.execute(
                "SELECT * FROM agent_runs WHERE order_id = ? ORDER BY started_at DESC, id DESC", (order_id,)
            ).fetchall()
            return {"orderId": order_id, "agentRuns": [self._run_presentation(connection, row) for row in rows]}

    def get_run(self, run_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise ApiError(404, "not_found", "Agent実行が見つかりません。")
            return self._run_presentation(connection, row)

    def start_run(self, run_id: str) -> bool:
        with closing(self._connect()) as connection:
            with connection:
                updated = connection.execute(
                    "UPDATE agent_runs SET status = 'running' WHERE id = ? AND status = 'queued'", (run_id,)
                )
                return updated.rowcount == 1

    def append_run_event(self, run_id: str, *, kind: str, actor: str,
                         message: str, source_id: str | None = None) -> None:
        if (kind not in {"status", "manager_selection", "source_read", "source_refused", "draft_submitted"}
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

    def finish_run(self, run_id: str, status: str, *, draft: dict[str, Any] | None = None,
                   error_code: str | None = None) -> bool:
        if status not in {"completed", "failed", "timed_out"}:
            raise ValueError("Agent実行の終了状態を確認してください。")
        if status == "completed" and (not isinstance(draft, dict) or error_code is not None):
            raise ValueError("完了したAgent実行には下書きが必要です。")
        if status != "completed" and (draft is not None or not error_code):
            raise ValueError("未完了のAgent実行には失敗コードが必要です。")
        with closing(self._connect()) as connection:
            with connection:
                updated = connection.execute(
                    "UPDATE agent_runs SET status = ?, ended_at = ?, draft_json = ?, error_code = ? "
                    "WHERE id = ? AND status = 'running'",
                    (status, _now(), _json(draft) if draft else None, error_code, run_id),
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

    def _detail(self, connection: sqlite3.Connection, order_id: str) -> dict[str, Any]:
        row = self._get_order_row(connection, order_id)
        response = connection.execute("SELECT * FROM customer_responses WHERE order_id = ?", (order_id,)).fetchone()
        approval = connection.execute("SELECT * FROM approvals WHERE order_id = ?", (order_id,)).fetchone()
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
        return {
            "order": self._order(row),
            "exception": {
                "type": "stock_shortage_and_cutoff",
                "message": "倉庫Aは25箱で15箱不足。倉庫Bの通常便は当日16時の集荷締切を過ぎています。",
                "detectedAt": SCENARIO_TIME,
                "contractConstraint": "分納には顧客の承諾が必要です。",
                "dataMode": "synthetic_fixture",
            },
            "options": list(OPTIONS),
            "customerResponse": None if response is None else {
                "acceptedSplit": bool(response["accepted_split"]), "note": response["note"],
                "recordedAt": response["recorded_at"],
            },
            "approval": None if approval is None else {
                "optionId": approval["option_id"], "reason": approval["reason"],
                "actorRole": approval["actor_role"], "approvedAt": approval["approved_at"],
                "authorizationMode": "declared_role_fixture",
            },
            "shipment": shipment,
            "inventory": [{"warehouseId": item["warehouse_id"], "available": item["available"]} for item in inventory],
            "dataMode": "synthetic_fixture",
            "authenticationMode": "none_local_trial",
        }

    def get_detail(self, order_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            return self._detail(connection, order_id)

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
        return json.loads(previous["response_json"])

    @staticmethod
    def _check_version(row: sqlite3.Row, expected: int) -> None:
        if row["version"] != expected:
            raise ApiError(409, "version_conflict", "受注が更新されています。再読込してから操作してください。")

    def record_customer_response(self, order_id: str, raw: object) -> dict[str, Any]:
        body = self._payload(raw, {"acceptedSplit", "expectedVersion", "operationId", "actorRole"}, {"note"})
        operation_id = _operation_id(body["operationId"])
        expected = _expected_version(body["expectedVersion"])
        if body["actorRole"] != "order_clerk":
            raise ApiError(403, "forbidden", "受注担当者の操作として登録してください。")
        if not isinstance(body["acceptedSplit"], bool):
            raise ApiError(400, "validation_error", "acceptedSplit は真偽値にしてください。")
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
                if row["status"] == "shipment_instructed":
                    raise ApiError(409, "already_instructed", "出荷指示の確定後は顧客回答を変更できません。")
                connection.execute(
                    "INSERT INTO customer_responses VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(order_id) DO UPDATE SET accepted_split=excluded.accepted_split, "
                    "note=excluded.note, recorded_at=excluded.recorded_at",
                    (order_id, int(body["acceptedSplit"]), note, _now()),
                )
                connection.execute(
                    "UPDATE orders SET status = 'awaiting_approval', version = version + 1 WHERE id = ?", (order_id,)
                )
                result = self._detail(connection, order_id)
                connection.execute("INSERT INTO operations VALUES (?, ?, ?, ?, ?)",
                                   (operation_id, order_id, "customer_response", request_json, _json(result)))
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
                if option["requiresCustomerConsent"]:
                    response = connection.execute(
                        "SELECT accepted_split FROM customer_responses WHERE order_id = ?", (order_id,)
                    ).fetchone()
                    if response is None or not response["accepted_split"]:
                        raise ApiError(422, "consent_required", "分納の承認には顧客の承諾が必要です。")
                if sum(item["quantity"] for item in option["allocations"]) != row["quantity"]:
                    raise ApiError(422, "invalid_allocation", "対処案の数量が受注数量と一致しません。")
                # Fixed-fixture carrier slots are rechecked at the same scenario time
                # as the recorded stock exception. No external TMS is connected.
                cutoff = ("2026-10-20T16:00:00+09:00" if option["id"] == "split-delivery"
                          else "2026-10-19T19:00:00+09:00")
                if datetime.fromisoformat(cutoff) <= datetime.fromisoformat(SCENARIO_TIME):
                    raise ApiError(422, "carrier_cutoff", "配送便の集荷締切を過ぎています。")
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
                    "INSERT INTO approvals VALUES (?, ?, ?, ?, ?)",
                    (order_id, option["id"], body["reason"], body["actorRole"], timestamp),
                )
                connection.execute(
                    "UPDATE orders SET status = 'shipment_instructed', version = version + 1 WHERE id = ?", (order_id,)
                )
                result = self._detail(connection, order_id)
                connection.execute("INSERT INTO operations VALUES (?, ?, ?, ?, ?)",
                                   (operation_id, order_id, "decision", request_json, _json(result)))
                return result


class RunController:
    """Local worker boundary for an explicitly configured Agent runner.

    The command-line server wires the real adapter; test servers may inject a
    scripted runner, whose output is labeled test_double in saved records.
    """

    def __init__(self, store: OrderStore, *, config_loader: Callable[[], Any],
                 runner: Callable[..., Any], execution_mode: str,
                 timeout_seconds: float = 180):
        if not 1 <= timeout_seconds <= 600:
            raise ValueError("Agent実行全体の期限は1〜600秒にしてください。")
        self.store = store
        self.config_loader = config_loader
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        if execution_mode not in {"live_model", "test_double"}:
            raise ValueError("Agent実行モードを確認してください。")
        self.execution_mode = execution_mode

    def submit(self, order_id: str, payload: object) -> dict[str, Any]:
        body = self.store._payload(payload, {"operationId"})
        operation_id = _operation_id(body["operationId"])
        existing = self.store.find_run(order_id, operation_id)
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
        run, created = self.store.create_run(order_id, operation_id, route=route, model_id=model_id,
                                             execution_mode=self.execution_mode)
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
            self.store.append_run_event(run_id, **event)

        try:
            snapshot = self.store.get_detail(order_id)
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
            plan = result["draftPlan"]
            if any(not isinstance(plan.get(field), str) or not plan[field].strip()
                   for field in ("recommendation", "alternatives", "customerQuestion")):
                self.store.finish_run(run_id, "failed", error_code="invalid_agent_result")
                return
            self.store.finish_run(run_id, "completed", draft={
                "body": result["draftBody"], "plan": plan,
                "contractReviewStatus": "not_evaluated",
                "businessReviewStatus": "unverified",
                "generationMode": expected_generation, "requiresHumanApproval": True,
                "shipmentChanged": False, "sourceReads": result.get("sourceReads", []),
                "managerEvents": result.get("managerEvents", []),
                "planBusinessValidation": "not_run",
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
                 **kwargs: object):
        self.store = store
        self.runs = runs
        super().__init__(*args, directory=str(DIST), **kwargs)

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
                self._send_json(200, {"status": "ok", "dataMode": "synthetic_fixture", "authenticationMode": "none_local_trial"})
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
            elif len(parts) == 3 and parts[:2] == ["api", "agent-runs"]:
                self._send_json(200, {"run": self.store.get_run(parts[2])})
            elif parts and parts[0] == "api":
                raise ApiError(404, "not_found", "APIが見つかりません。")
            elif DIST.is_dir():
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
            elif parts[3] == "decision":
                result = self.store.decide(parts[2], body)
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
                *, recover_interrupted: bool = False) -> ThreadingHTTPServer:
    store = OrderStore(db_path)
    if recover_interrupted:
        store.recover_interrupted_runs()
    runs = runs_factory(store) if runs_factory is not None else None
    return ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, store=store, runs=runs))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5340)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    from order_agent import OrderAgentConfig, run_initial_order

    server = make_server(args.port, args.db, runs_factory=lambda store: RunController(
        store, config_loader=OrderAgentConfig.from_env, runner=run_initial_order,
        execution_mode="live_model", timeout_seconds=180,
    ), recover_interrupted=True)
    try:
        print(f"Order exception trial: http://127.0.0.1:{server.server_port}/", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
