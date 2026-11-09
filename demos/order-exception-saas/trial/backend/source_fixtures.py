"""Versioned synthetic source facts for the order exception trial.

These facts are inputs to the business workflow and Agent tools. They contain
no completed resolution option and are not live ERP, WMS, TMS, or contract data.
"""

from __future__ import annotations

from typing import Any

FIXTURE_VERSION = "3.0"
SCENARIO_TIME = "2026-11-08T00:00:00+09:00"
ORDER_DUE_AT = "2026-11-16T10:00:00+09:00"


CARRIER_SOURCES: dict[str, dict[str, Any]] = {
    "carrier-a-normal-v3": {
        "sourceId": "carrier-a-normal-v3", "version": FIXTURE_VERSION, "dataMode": "synthetic_fixture",
        "warehouseId": "warehouse-a", "pickupCutoffAt": "2026-11-15T07:00:00+09:00",
        "earliestDeliveryAt": ORDER_DUE_AT, "capacityBoxes": 25,
        "additionalCostJpy": 0,
    },
    "carrier-b-normal-v3": {
        "sourceId": "carrier-b-normal-v3", "version": FIXTURE_VERSION, "dataMode": "synthetic_fixture",
        "warehouseId": "warehouse-b", "pickupCutoffAt": "2026-11-07T21:00:00+09:00",
        "nextPickupAt": "2026-11-15T16:00:00+09:00",
        "earliestDeliveryAt": "2026-11-17T10:00:00+09:00", "capacityBoxes": 20,
        "additionalCostJpy": 0,
    },
    "carrier-b-express-v3": {
        "sourceId": "carrier-b-express-v3", "version": FIXTURE_VERSION, "dataMode": "synthetic_fixture",
        "warehouseId": "warehouse-b", "pickupCutoffAt": "2026-11-15T07:30:00+09:00",
        "earliestDeliveryAt": ORDER_DUE_AT, "capacityBoxes": 20,
        "additionalCostJpy": 78000,
    },
}

CONTRACT_SOURCE: dict[str, Any] = {
    "sourceId": "contract-split-v3", "version": FIXTURE_VERSION, "dataMode": "synthetic_fixture",
    "clause": "分納には顧客の承諾が必要です。",
    "scope": "合成受注SO-24017の分納条件",
}
