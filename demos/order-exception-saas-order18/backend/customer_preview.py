"""Read-only customer wording derived from one saved, source-checked run.

This is a narrow domain projection. It neither calls a model nor sends a message.
The model's free-form reply and draft body are deliberately never read here.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from source_fixtures import CARRIER_SOURCES, CONTRACT_SOURCE, FIXTURE_ID, FIXTURE_VERSION


LIMITATIONS = [
    "確認用・未送信です。担当者による確認が必要です。",
    "契約原文の担当者確認、契約への適合、実際の集荷・出荷日時は未確認です。",
    "この表示では顧客回答・出荷承認を実行していません。",
]


def _unavailable(reason: str, historical: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "not_available", "basis": "saved_structured_draft",
        "limitations": LIMITATIONS.copy(), "historical": historical,
        "sendable": False, "reason": reason,
    }
    if historical:
        result["historicalReason"] = "過去の保存案です。閲覧専用で、現在の案内には使えません。"
    return result


def _datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing datetime")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed


def _display_datetime(value: str) -> str:
    parsed = _datetime(value)
    offset = parsed.strftime("%z")
    return f"{parsed:%Y-%m-%d %H:%M}（UTC{offset[:3]}:{offset[3:]}）"


def _tool_facts(saved: dict[str, Any], snapshot: dict[str, Any]) -> tuple[dict, dict]:
    responses = saved["toolResponses"]
    if not isinstance(responses, list) or responses != snapshot["toolResponses"] or len(responses) != 2:
        raise ValueError("tool response mismatch")
    parsed: dict[str, dict] = {}
    for response in responses:
        name = response["toolName"]
        if name in parsed or name not in {"read_order_inventory", "read_carriers_contract"}:
            raise ValueError("tool name mismatch")
        text = response["responseText"]
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != response["responseSha256"]:
            raise ValueError("tool hash mismatch")
        payload = json.loads(text)
        if payload["dataMode"] != "synthetic_fixture":
            raise ValueError("source mode mismatch")
        actual_ids = ([payload["orderSourceId"], *[item["sourceId"] for item in payload["warehouses"]]]
                      if name == "read_order_inventory" else
                      [*[item["sourceId"] for item in payload["carriers"]], payload["contract"]["sourceId"]])
        if actual_ids != response["sourceIds"]:
            raise ValueError("source ID mismatch")
        parsed[name] = payload
    if set(parsed) != {"read_order_inventory", "read_carriers_contract"}:
        raise ValueError("required tool missing")
    return parsed["read_order_inventory"], parsed["read_carriers_contract"]


def customer_preview(saved: dict[str, Any], *, current_order: dict[str, Any],
                     current_inventory: list[dict[str, Any]], latest_run_id: str | None,
                     now: datetime | None = None) -> dict[str, Any]:
    """Project only a current, still usable saved proposal; fail closed otherwise."""
    snapshot = saved.get("sourceSnapshot")
    source_order = snapshot.get("order") if isinstance(snapshot, dict) else None
    historical = (saved.get("trialId") != current_order.get("currentTrialId") or
                  saved.get("id") != latest_run_id or
                  (isinstance(source_order, dict) and
                   source_order.get("version") != current_order.get("version")))
    if saved.get("status") not in {"completed", "draft_ready"}:
        return _unavailable("完了して保存された案がありません。", historical)
    if saved.get("executionMode") != "live_model":
        return _unavailable("実モデルが提出した保存案ではありません。", historical)
    if historical:
        return _unavailable("新しい実行または受注更新があるため、過去案の文案は表示しません。", True)
    if (current_order.get("status") != "exception_detected" or
            source_order is None or source_order.get("status") != current_order.get("status") or
            snapshot.get("inventory") != current_inventory):
        return _unavailable("保存後に受注または在庫が変わりました。資料を読み直してください。", False)
    try:
        draft = saved["draft"]
        plan = draft["plan"]
        if (draft["planBusinessValidation"] != "structured_source_consistency_checked" or
                plan["validation"] != "structured_source_consistency_checked" or
                draft["projectionMode"] != "api_from_model_structured_data"):
            raise ValueError("unchecked plan")
        order_tool, carrier_tool = _tool_facts(saved, snapshot)
        order_id = saved["orderId"]
        version = source_order["version"]
        if (source_order["id"] != order_id or source_order["currentTrialId"] != saved["trialId"] or
                type(version) is not int or
                order_tool["orderSourceId"] != f"order-{order_id}-v{version}" or
                order_tool["orderVersion"] != version or
                order_tool["quantityBoxes"] != source_order["quantity"] or
                order_tool["dueAt"] != source_order["dueAt"] or
                order_tool["detectedAt"] != snapshot["exception"]["detectedAt"]):
            raise ValueError("order source mismatch")
        stock = {item["warehouseId"]: item for item in order_tool["warehouses"]}
        saved_stock = {item["warehouseId"]: item["available"] for item in snapshot["inventory"]}
        if (set(stock) != {"warehouse-a", "warehouse-b"} or
                any(stock[warehouse]["sourceId"] != f"stock-{warehouse}-snapshot-order-v{version}" or
                    stock[warehouse]["availableBoxes"] != saved_stock[warehouse]
                    for warehouse in stock)):
            raise ValueError("stock source mismatch")
        carriers = {item["sourceId"]: item for item in carrier_tool["carriers"]}
        contract = carrier_tool["contract"]
        if (len(carriers) != len(carrier_tool["carriers"]) or
                set(carriers) != set(CARRIER_SOURCES) or
                contract != CONTRACT_SOURCE or
                contract["clause"] != CONTRACT_SOURCE["clause"] or
                source_order["fixtureId"] != current_order["fixtureId"] or
                any(item["fixtureVersion"] != FIXTURE_VERSION for item in saved["toolResponses"])):
            raise ValueError("saved sources mismatch")
        proposals = plan["proposals"]
        if not isinstance(proposals, list) or len(proposals) != 2:
            raise ValueError("missing proposals")
        timestamp = now or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise ValueError("now timezone required")
        delivery_change = FIXTURE_ID == "oe02-order18-due16-20261111"
        due_at = _datetime(source_order["dueAt"])
        lines = [f"【確認用・未送信】合成受注{order_id}の配送候補です。"
                 + (f"希望納期は{_display_datetime(source_order['dueAt'])}です。" if delivery_change else "")]
        costs: set[int] = set()
        normal_late = False
        express_on_time = False
        for proposal in sorted(proposals, key=lambda item: item["additionalCostJpy"]):
            cost = proposal["additionalCostJpy"]
            if (type(cost) is not int or cost < 0 or cost in costs or
                    proposal["runId"] != saved["id"] or
                    proposal["id"] != f"{saved['id']}:{'normal' if cost == 0 else 'express'}" or
                    proposal["deliveryPattern"] != "separate_warehouse_shipments"):
                raise ValueError("proposal identity mismatch")
            costs.add(cost)
            shipments = proposal["shipments"]
            if not isinstance(shipments, list) or len(shipments) != 2:
                raise ValueError("shipments missing")
            parts: list[str] = []
            warehouse_ids: set[str] = set()
            expected_cost = 0
            carrier_ids: set[str] = set()
            total = 0
            for shipment in shipments:
                warehouse = shipment["warehouseId"]
                carrier_id = shipment["carrierSourceId"]
                carrier = carriers[carrier_id]
                frozen = CARRIER_SOURCES[carrier_id]
                quantity = shipment["quantityBoxes"]
                delivery = _datetime(shipment["deliveryAt"])
                pickup = _datetime(carrier["pickupCutoffAt"])
                detected = _datetime(snapshot["exception"]["detectedAt"])
                if pickup <= detected:
                    pickup = _datetime(carrier["nextPickupAt"])
                if (warehouse in warehouse_ids or warehouse not in stock or
                        carrier["warehouseId"] != warehouse or
                        any(carrier.get(key) != frozen.get(key) for key in
                            ("warehouseId", "earliestDeliveryAt", "pickupCutoffAt",
                             "nextPickupAt", "capacityBoxes", "additionalCostJpy", "version")) or
                        type(quantity) is not int or quantity <= 0 or
                        quantity > stock[warehouse]["availableBoxes"] or
                        quantity > carrier["capacityBoxes"] or
                        delivery != _datetime(carrier["earliestDeliveryAt"])):
                    raise ValueError("shipment source mismatch")
                if pickup <= timestamp or delivery <= timestamp:
                    return _unavailable("便の集荷期限または到着予定を過ぎました。新しい資料で再調査してください。", False)
                warehouse_ids.add(warehouse)
                carrier_ids.add(carrier_id)
                expected_cost += carrier["additionalCostJpy"]
                total += quantity
                label = "倉庫A" if warehouse == "warehouse-a" else "倉庫B"
                parts.append(f"{label}から{quantity}箱（到着予定 {_display_datetime(shipment['deliveryAt'])}）")
            expected_refs = {order_tool["orderSourceId"],
                             *[item["sourceId"] for item in stock.values()],
                             *carrier_ids, contract["sourceId"]}
            if (warehouse_ids != set(stock) or total != source_order["quantity"] or
                    cost != expected_cost or
                    len(proposal["sourceRefs"]) != len(expected_refs) or
                    set(proposal["sourceRefs"]) != expected_refs):
                raise ValueError("proposal source refs or total mismatch")
            payer_source = proposal.get("payerSource")
            contract_tool = next(item for item in saved["toolResponses"]
                                 if item["toolName"] == "read_carriers_contract")
            if cost == 0:
                if (proposal.get("additionalCostPayer") != "not_applicable" or
                        payer_source is not None):
                    raise ValueError("normal payer mismatch")
            elif (proposal.get("additionalCostPayer") != "seller" or
                  proposal.get("payerReviewStatus") != "human_review_required" or
                  payer_source != {"sourceId": CONTRACT_SOURCE["sourceId"],
                                   "version": CONTRACT_SOURCE["version"],
                                   "toolName": "read_carriers_contract",
                                   "responseSha256": contract_tool["responseSha256"]}):
                raise ValueError("express payer source mismatch")
            label = "通常便案" if cost == 0 else "特急便案"
            latest_delivery = max(_datetime(item["deliveryAt"]) for item in shipments)
            if cost == 0:
                normal_late = latest_delivery > due_at
            else:
                express_on_time = latest_delivery <= due_at
            timing = ("希望納期を超える見込みです。" if latest_delivery > due_at else
                      "予定上は希望納期内です。") if delivery_change else ""
            lines.append(f"{label}：{'、'.join(parts)}。{timing}追加費用は{cost:,}円です。")
        if costs != {0, 78000}:
            raise ValueError("expected alternative costs missing")
        if delivery_change and not (normal_late and express_on_time):
            raise ValueError("delivery change comparison mismatch")
        lines.append("通常便案は希望納期を超える見込みです。分納と納期変更をご承諾いただけるか、特急便案と比較してご返信ください。特急費用は合成契約5.0に販売者負担と記載され、確認担当者の原文確認待ちです。" if delivery_change else
                     "分納をご承諾いただけるか、ご確認をお願いします。特急費用は合成契約5.0に販売者負担と記載され、確認担当者の原文確認待ちです。")
        lines.append("契約への適合と実際の集荷・出荷日時は未確認です。この文案から顧客回答や出荷承認は実行していません。")
        return {"status": "available", "text": "\n".join(lines),
                "basis": "saved_structured_draft", "limitations": LIMITATIONS.copy(),
                "historical": False, "sendable": False}
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return _unavailable("保存案とTool読取資料の対応を確認できません。再調査してください。", False)
