"""Versioned synthetic source facts for the order exception trial.

These facts are inputs to the business workflow and Agent tools. They contain
no completed resolution option and are not live ERP, WMS, TMS, or contract data.
"""

from __future__ import annotations

from typing import Any


CARRIER_SOURCES: dict[str, dict[str, Any]] = {
    "carrier-a-normal-v1": {
        "sourceId": "carrier-a-normal-v1", "version": "1.0", "dataMode": "synthetic_fixture",
        "warehouseId": "warehouse-a", "pickupCutoffAt": "2026-10-19T18:00:00+09:00",
        "earliestDeliveryAt": "2026-10-20T10:00:00+09:00", "capacityBoxes": 25,
        "additionalCostJpy": 0,
    },
    "carrier-b-normal-v1": {
        "sourceId": "carrier-b-normal-v1", "version": "1.0", "dataMode": "synthetic_fixture",
        "warehouseId": "warehouse-b", "pickupCutoffAt": "2026-10-19T16:00:00+09:00",
        "nextPickupAt": "2026-10-20T16:00:00+09:00",
        "earliestDeliveryAt": "2026-10-21T10:00:00+09:00", "capacityBoxes": 20,
        "additionalCostJpy": 0,
    },
    "carrier-b-express-v1": {
        "sourceId": "carrier-b-express-v1", "version": "1.0", "dataMode": "synthetic_fixture",
        "warehouseId": "warehouse-b", "pickupCutoffAt": "2026-10-19T19:00:00+09:00",
        "earliestDeliveryAt": "2026-10-20T10:00:00+09:00", "capacityBoxes": 20,
        "additionalCostJpy": 78000,
    },
}

CONTRACT_SOURCE: dict[str, Any] = {
    "sourceId": "contract-split-v1", "version": "1.0", "dataMode": "synthetic_fixture",
    "clause": "分納には顧客の承諾が必要です。",
    "scope": "合成受注SO-24017の分納条件",
}
