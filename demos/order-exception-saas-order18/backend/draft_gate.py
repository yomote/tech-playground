"""Narrow source consistency gate for synthetic order drafts.

The gate checks structured facts against the order snapshot, actual Tool-read
events, and versioned synthetic carrier fixtures. It does not judge the model's
free prose or establish that a shipment plan is commercially acceptable.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from source_fixtures import CARRIER_SOURCES, CONTRACT_SOURCE, FIXTURE_VERSION


# The positive payer rule is restricted to this reviewed synthetic source.
# A later fixture version, a different clause, carrier, or amount needs a new
# domain rule and human review before any saved payer assertion is allowed.
SELLER_PAYER_CONTRACT_V5 = {
    "sourceId": "contract-split-v5", "version": "5.0",
    "dataMode": "synthetic_fixture",
    "clause": "分納には顧客の承諾が必要です。合成受注SO-24018の倉庫B特急便追加費用78,000円は販売者が負担します。",
    "scope": "合成受注SO-24018の分納と特急費用負担条件",
}
SELLER_PAYER_EXPRESS_CARRIER_ID = "carrier-b-express-v5"
SELLER_PAYER_EXPRESS_COST_JPY = 78000


class DraftGroundingError(ValueError):
    """A fixed public code and a model-value-free location/reason pair."""

    def __init__(self, code: str, field_path: str, reason: str):
        self.code = code
        self.field_path = field_path
        self.reason = reason
        super().__init__(code)


def _verified_contract_tool(snapshot: dict[str, Any]) -> dict[str, str]:
    """Accept only the exact synthetic v5 clause read by this run's Tool.

    This is a domain rule for one synthetic contract, not a general contract
    parser. The model's payer prose is never treated as authority.
    """
    responses = snapshot.get("toolResponses")
    if not isinstance(responses, list):
        raise DraftGroundingError("payer_source_unavailable", "toolResponses",
                                  "contract_tool_missing")
    matching = [item for item in responses if isinstance(item, dict) and
                item.get("toolName") == "read_carriers_contract"]
    if len(matching) != 1:
        raise DraftGroundingError("payer_source_unavailable", "toolResponses",
                                  "contract_tool_missing")
    item = matching[0]
    response_text = item.get("responseText")
    response_sha = item.get("responseSha256")
    if (not isinstance(response_text, str) or not isinstance(response_sha, str) or
            hashlib.sha256(response_text.encode("utf-8")).hexdigest() != response_sha):
        raise DraftGroundingError("payer_source_mismatch", "toolResponses",
                                  "contract_tool_hash_mismatch")
    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        raise DraftGroundingError("payer_source_mismatch", "toolResponses",
                                  "contract_tool_invalid_json") from None
    expected_ids = [*CARRIER_SOURCES, CONTRACT_SOURCE["sourceId"]]
    exception = snapshot.get("exception")
    if (not isinstance(parsed, dict) or
            parsed.get("dataMode") != "synthetic_fixture" or
            CONTRACT_SOURCE != SELLER_PAYER_CONTRACT_V5 or
            FIXTURE_VERSION != "5.0" or
            parsed.get("contract") != CONTRACT_SOURCE or
            parsed.get("carriers") != list(CARRIER_SOURCES.values()) or
            item.get("sourceIds") != expected_ids or
            item.get("fixtureVersion") != FIXTURE_VERSION or
            not isinstance(exception, dict) or
            exception.get("contractConstraint") != CONTRACT_SOURCE["clause"] or
            CARRIER_SOURCES.get(SELLER_PAYER_EXPRESS_CARRIER_ID, {}).get("additionalCostJpy") !=
            SELLER_PAYER_EXPRESS_COST_JPY):
        raise DraftGroundingError("payer_source_mismatch", "toolResponses",
                                  "contract_terms_changed_or_missing")
    return {"sourceId": CONTRACT_SOURCE["sourceId"],
            "version": CONTRACT_SOURCE["version"],
            "toolName": "read_carriers_contract", "responseSha256": response_sha}


def _when(value: object, field_path: str) -> datetime:
    if not isinstance(value, str):
        raise DraftGroundingError("draft_grounding_failed", field_path, "invalid_datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise DraftGroundingError("draft_grounding_failed", field_path, "invalid_datetime") from None
    if parsed.tzinfo is None:
        raise DraftGroundingError("draft_grounding_failed", field_path, "timezone_required")
    return parsed


def expected_source_refs_for_proposal(snapshot: dict[str, Any],
                                      proposal: object) -> list[str]:
    """Return the fixture IDs a well-shaped proposal must cite.

    This is also used for a bounded failure diagnostic. An invalid shipment
    shape has no reliable expected carrier set, so it returns an empty list.
    """
    if not isinstance(proposal, dict):
        return []
    shipments = proposal.get("shipments")
    if not isinstance(shipments, list) or len(shipments) != 2:
        return []
    carrier_ids = []
    for shipment in shipments:
        if not isinstance(shipment, dict):
            return []
        carrier_id = shipment.get("carrierSourceId")
        if not isinstance(carrier_id, str) or carrier_id not in CARRIER_SOURCES:
            return []
        carrier_ids.append(carrier_id)
    order = snapshot["order"]
    version = order["version"]
    return sorted({
        f"order-{order['id']}-v{version}",
        f"stock-warehouse-a-snapshot-order-v{version}",
        f"stock-warehouse-b-snapshot-order-v{version}",
        *carrier_ids,
        CONTRACT_SOURCE["sourceId"],
    })


def validate_structured_draft(snapshot: dict[str, Any], plan: dict[str, Any],
                              read_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Return an API-owned projection separate from any saved model final reply."""
    order = snapshot["order"]
    version = order["version"]
    read_ids = {event.get("sourceId") for event in read_events
                if event.get("kind") == "source_read"}
    required_reads = {
        f"order-{order['id']}-v{version}",
        f"stock-warehouse-a-snapshot-order-v{version}",
        f"stock-warehouse-b-snapshot-order-v{version}",
        *CARRIER_SOURCES,
        CONTRACT_SOURCE["sourceId"],
    }
    if not required_reads.issubset(read_ids):
        raise DraftGroundingError("source_not_read", "sourceReads", "required_source_missing")
    payer_source = _verified_contract_tool(snapshot)
    proposals = plan.get("proposals")
    if not isinstance(proposals, list) or len(proposals) != 2:
        raise DraftGroundingError("draft_grounding_failed", "proposals", "expected_two_proposals")
    stock = {item["warehouseId"]: item["available"] for item in snapshot["inventory"]}
    verified: list[dict[str, Any]] = []
    used_carrier_sets: set[tuple[str, ...]] = set()
    for proposal_index, proposal in enumerate(proposals):
        proposal_path = f"proposals[{proposal_index}]"
        if not isinstance(proposal, dict):
            raise DraftGroundingError("consolidation_source_missing", proposal_path, "expected_object")
        if proposal.get("deliveryPattern") != "separate_warehouse_shipments":
            # Neither carrier source establishes a consolidated A+B shipment.
            raise DraftGroundingError("consolidation_source_missing",
                                      f"{proposal_path}.deliveryPattern", "unsupported_delivery_pattern")
        shipments = proposal.get("shipments")
        if not isinstance(shipments, list) or len(shipments) != 2:
            raise DraftGroundingError("draft_grounding_failed", f"{proposal_path}.shipments",
                                      "expected_two_shipments")
        normalized = []
        seen_warehouses: set[str] = set()
        cost = 0
        for shipment_index, item in enumerate(shipments):
            shipment_path = f"{proposal_path}.shipments[{shipment_index}]"
            if not isinstance(item, dict):
                raise DraftGroundingError("draft_grounding_failed", shipment_path, "expected_object")
            warehouse = item.get("warehouseId")
            quantity = item.get("quantityBoxes")
            carrier_id = item.get("carrierSourceId")
            carrier = CARRIER_SOURCES.get(carrier_id) if isinstance(carrier_id, str) else None
            if not isinstance(warehouse, str) or warehouse not in stock:
                raise DraftGroundingError("draft_grounding_failed", f"{shipment_path}.warehouseId",
                                          "unknown_warehouse")
            if warehouse in seen_warehouses:
                raise DraftGroundingError("draft_grounding_failed", f"{shipment_path}.warehouseId",
                                          "duplicate_warehouse")
            if type(quantity) is not int or quantity <= 0:
                raise DraftGroundingError("draft_grounding_failed", f"{shipment_path}.quantityBoxes",
                                          "expected_positive_integer")
            if quantity > stock[warehouse]:
                raise DraftGroundingError("draft_grounding_failed", f"{shipment_path}.quantityBoxes",
                                          "exceeds_stock")
            if carrier is None:
                raise DraftGroundingError("draft_grounding_failed", f"{shipment_path}.carrierSourceId",
                                          "unknown_carrier_source")
            if carrier["warehouseId"] != warehouse:
                raise DraftGroundingError("draft_grounding_failed", f"{shipment_path}.carrierSourceId",
                                          "wrong_warehouse")
            if carrier_id not in read_ids:
                raise DraftGroundingError("draft_grounding_failed", f"{shipment_path}.carrierSourceId",
                                          "carrier_source_not_read")
            if quantity > carrier["capacityBoxes"]:
                raise DraftGroundingError("draft_grounding_failed", f"{shipment_path}.quantityBoxes",
                                          "exceeds_carrier_capacity")
            if (_when(item.get("deliveryAt"), f"{shipment_path}.deliveryAt") !=
                    _when(carrier["earliestDeliveryAt"], "carrierSources.earliestDeliveryAt")):
                raise DraftGroundingError("draft_grounding_failed", f"{shipment_path}.deliveryAt",
                                          "delivery_date_mismatch")
            # If the original cutoff elapsed, only an explicitly listed next
            # pickup can support the displayed earliest delivery date.
            scenario_time = _when(snapshot["exception"]["detectedAt"], "exception.detectedAt")
            effective_pickup = _when(carrier["pickupCutoffAt"], "carrierSources.pickupCutoffAt")
            if effective_pickup <= scenario_time:
                next_pickup = carrier.get("nextPickupAt")
                if next_pickup is None or _when(next_pickup, "carrierSources.nextPickupAt") <= scenario_time:
                    raise DraftGroundingError("draft_grounding_failed", f"{shipment_path}.carrierSourceId",
                                              "pickup_unavailable")
                effective_pickup = _when(next_pickup, "carrierSources.nextPickupAt")
            # A proposal may have been valid at the synthetic detection time
            # but stale by the time the real model completes. Never save it.
            now = datetime.now(timezone.utc)
            if (effective_pickup <= now or
                    _when(carrier["earliestDeliveryAt"], "carrierSources.earliestDeliveryAt") <= now):
                raise DraftGroundingError("source_stale", f"{shipment_path}.carrierSourceId",
                                          "schedule_expired")
            seen_warehouses.add(warehouse)
            cost += carrier["additionalCostJpy"]
            normalized.append({"warehouseId": warehouse, "quantityBoxes": quantity,
                               "carrierSourceId": carrier_id,
                               "deliveryAt": carrier["earliestDeliveryAt"]})
        if seen_warehouses != set(stock):
            raise DraftGroundingError("draft_grounding_failed", f"{proposal_path}.shipments",
                                      "warehouse_missing")
        if sum(item["quantityBoxes"] for item in normalized) != order["quantity"]:
            raise DraftGroundingError("draft_grounding_failed", f"{proposal_path}.shipments",
                                      "quantity_total_mismatch")
        if type(proposal.get("additionalCostJpy")) is not int or proposal["additionalCostJpy"] != cost:
            raise DraftGroundingError("draft_grounding_failed", f"{proposal_path}.additionalCostJpy",
                                      "cost_mismatch")
        required_decisions = ["acceptedSplit"] + (["acceptedExpressCost"] if cost else ["acceptedLateDelivery"])
        if proposal.get("customerDecisionFields") != required_decisions:
            raise DraftGroundingError("draft_grounding_failed", f"{proposal_path}.customerDecisionFields",
                                      "decision_field_names_mismatch")
        carriers = tuple(sorted(item["carrierSourceId"] for item in normalized))
        if cost and (cost != SELLER_PAYER_EXPRESS_COST_JPY or
                     SELLER_PAYER_EXPRESS_CARRIER_ID not in carriers):
            raise DraftGroundingError("payer_clause_not_applicable",
                                      f"{proposal_path}.additionalCostJpy",
                                      "unsupported_positive_cost")
        expected_refs = set(expected_source_refs_for_proposal(snapshot, proposal))
        refs = proposal.get("sourceRefs")
        if (not isinstance(refs, list) or len(refs) != len(expected_refs) or
                not all(isinstance(ref, str) for ref in refs) or set(refs) != expected_refs):
            raise DraftGroundingError("draft_grounding_failed", f"{proposal_path}.sourceRefs",
                                      "source_refs_mismatch")
        if carriers in used_carrier_sets:
            raise DraftGroundingError("draft_grounding_failed", f"{proposal_path}.shipments",
                                      "duplicate_carrier_option")
        used_carrier_sets.add(carriers)
        verified.append({
            "deliveryPattern": "separate_warehouse_shipments",
            "shipments": sorted(normalized, key=lambda item: item["warehouseId"]),
            "additionalCostJpy": cost,
            "customerDecisionFields": required_decisions,
            "sourceRefs": sorted(expected_refs),
            "additionalCostPayer": "seller" if cost else "not_applicable",
            "payerReviewStatus": "human_review_required" if cost else "not_applicable",
            "payerSource": payer_source if cost else None,
        })
    if not {"carrier-b-normal-v5", "carrier-b-express-v5"}.issubset(
            {item["carrierSourceId"] for proposal in verified for item in proposal["shipments"]}):
        raise DraftGroundingError("draft_grounding_failed", "proposals",
                                  "normal_and_express_required")
    normal_delivery_at = CARRIER_SOURCES["carrier-b-normal-v5"]["earliestDeliveryAt"]
    normal_delivery = _when(normal_delivery_at, "carrierSources.earliestDeliveryAt")
    due = _when(order["dueAt"], "order.dueAt")
    normal_due_relation = ("希望日時より後" if normal_delivery > due else
                           "希望日時と同時刻" if normal_delivery == due else "希望日時より前")
    return {
        "proposals": verified,
        "customerQuestions": ["A/B別送による分納を承諾しますか。",
                              "通常便の倉庫B到着は希望納期後です。合成顧客役は希望11月16日10時から到着11月17日10時への変更を分納とは別に承諾しますか。",
                              "倉庫Bの特急便利用を希望しますか。追加費用78,000円は合成契約v5に販売者負担と記載されています。契約確認担当役による原文確認は別途必要です。"],
        "limitations": [f"通常便案の倉庫B分は{normal_delivery_at.replace('T', ' ')}到着予定です。"
                        f"受注希望{order['dueAt'].replace('T', ' ')}との比較では予定上{normal_due_relation}です。",
                        "両倉庫を一つの便に集約できる資料はありません。",
                        "特急便追加費用78,000円の販売者負担は合成契約v5の原文確認待ちです。",
                        "原資料との数値一致だけを照合しました。業務案と契約適合は未判定です。"],
        "validation": "structured_source_consistency_checked",
    }
