"""Versioned synthetic source facts for the order exception trial.

These facts are inputs to the business workflow and Agent tools. They contain
no completed resolution option and are not live ERP, WMS, TMS, or contract data.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

FIXTURE_VERSION = "5.0"
SCENARIO_TIME = "2026-11-11T11:00:00+09:00"
SCENARIO_PATH = Path(os.environ.get("OE02_SCENARIO_PATH", Path(__file__).with_name("scenario-order18.json")))


def _load_scenario() -> dict[str, Any]:
    """Read one explicit synthetic order seed before any database is opened."""
    scenario = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    if not isinstance(scenario, dict) or set(scenario) != {
            "fixtureId", "orderDueAt", "initialOrderVersion"}:
        raise ValueError("invalid synthetic scenario fields")
    fixture_id = scenario["fixtureId"]
    due_at = scenario["orderDueAt"]
    version = scenario["initialOrderVersion"]
    if not isinstance(fixture_id, str) or re.fullmatch(r"[a-z0-9-]{1,64}", fixture_id) is None:
        raise ValueError("invalid synthetic fixtureId")
    if not isinstance(due_at, str):
        raise ValueError("invalid synthetic orderDueAt")
    try:
        due = datetime.fromisoformat(due_at)
    except ValueError:
        raise ValueError("invalid synthetic orderDueAt") from None
    if due.tzinfo is None or due <= datetime.fromisoformat(SCENARIO_TIME):
        raise ValueError("synthetic orderDueAt must be after detection time with timezone")
    if type(version) is not int or version < 1:
        raise ValueError("invalid synthetic initialOrderVersion")
    return scenario


SCENARIO = _load_scenario()
FIXTURE_ID = SCENARIO["fixtureId"]
ORDER_DUE_AT = SCENARIO["orderDueAt"]
INITIAL_ORDER_VERSION = SCENARIO["initialOrderVersion"]

# Carrier facts are dated independently from the customer's requested due time.
# Changing the order scenario must not silently rewrite carrier evidence.
CARRIER_A_DELIVERY_AT = "2026-11-16T10:00:00+09:00"
CARRIER_B_EXPRESS_DELIVERY_AT = "2026-11-16T10:00:00+09:00"


CARRIER_SOURCES: dict[str, dict[str, Any]] = {
    "carrier-a-normal-v5": {
        "sourceId": "carrier-a-normal-v5", "version": FIXTURE_VERSION, "dataMode": "synthetic_fixture",
        "warehouseId": "warehouse-a", "pickupCutoffAt": "2026-11-15T07:00:00+09:00",
        "earliestDeliveryAt": CARRIER_A_DELIVERY_AT, "capacityBoxes": 25,
        "additionalCostJpy": 0,
    },
    "carrier-b-normal-v5": {
        "sourceId": "carrier-b-normal-v5", "version": FIXTURE_VERSION, "dataMode": "synthetic_fixture",
        "warehouseId": "warehouse-b", "pickupCutoffAt": "2026-11-07T21:00:00+09:00",
        "nextPickupAt": "2026-11-15T16:00:00+09:00",
        "earliestDeliveryAt": "2026-11-17T10:00:00+09:00", "capacityBoxes": 20,
        "additionalCostJpy": 0,
    },
    "carrier-b-express-v5": {
        "sourceId": "carrier-b-express-v5", "version": FIXTURE_VERSION, "dataMode": "synthetic_fixture",
        "warehouseId": "warehouse-b", "pickupCutoffAt": "2026-11-15T07:30:00+09:00",
        "earliestDeliveryAt": CARRIER_B_EXPRESS_DELIVERY_AT, "capacityBoxes": 20,
        "additionalCostJpy": 78000,
    },
}

CONTRACT_SOURCE: dict[str, Any] = {
    "sourceId": "contract-split-v5", "version": FIXTURE_VERSION, "dataMode": "synthetic_fixture",
    "clause": "分納には顧客の承諾が必要です。合成受注SO-24018の倉庫B特急便追加費用78,000円は販売者が負担します。",
    "scope": "合成受注SO-24018の分納と特急費用負担条件",
}


def source_facts_sha256() -> str:
    """Fingerprint the synthetic carrier and contract facts read by Agent tools."""
    facts = {
        "fixtureVersion": FIXTURE_VERSION,
        "detectedAt": SCENARIO_TIME,
        "carriers": CARRIER_SOURCES,
        "contract": CONTRACT_SOURCE,
    }
    canonical = json.dumps(facts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
