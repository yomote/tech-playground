"""Short MAF Agent trial using the same sources and business API gate.

The model must invoke two read tools and one submit tool. No Tool changes
business state; the domain API independently checks the structured proposal.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Any, Callable
from uuid import uuid4

from draft_gate import (DraftGroundingError, expected_source_refs_for_proposal,
                        validate_structured_draft)
from order_agent import (AGENT_VERSION, ORDER_ID, OrderAgentConfig,
                         OrderAgentError, _error_category, _validate_snapshot)
from source_fixtures import CARRIER_SOURCES, CONTRACT_SOURCE, FIXTURE_VERSION


async def wait_for_inventory_correction(reads_complete: asyncio.Event,
                                        run_state: Callable[[], dict[str, Any]],
                                        wait_seconds: int,
                                        *, poll_seconds: float = 0.2) -> str:
    """End the human window on a saved correction, another terminal state, or expiry.

    The business API makes the correction and source_stale transition in one
    SQLite transaction. Poll only that authoritative run state; the model's
    prose and the browser's pending display cannot release this gate.
    """
    await reads_complete.wait()
    deadline = time.monotonic() + wait_seconds
    while True:
        state = run_state()
        if state["status"] == "failed" and state["errorCode"] == "source_stale":
            return "source_stale"
        if state["status"] != "running":
            return "run_not_active"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "human_wait_expired"
        await asyncio.sleep(min(poll_seconds, remaining))


def _decision_field_shape_issue(proposals: list[Any]) -> tuple[int, str, str, list[str]] | None:
    """Return only fixed field names and reason codes, never candidate values."""
    for index, item in enumerate(proposals):
        expected = ["acceptedSplit"] + (["acceptedExpressCost"]
                                        if isinstance(item, dict) and item.get("additionalCostJpy") else [])
        if not isinstance(item, dict):
            return index, f"proposals[{index}]", "expected_object", expected
        fields = item.get("customerDecisionFields")
        if fields != expected:
            reason = ("expected_string_array" if not isinstance(fields, list) or
                      any(not isinstance(field, str) for field in fields)
                      else "decision_field_names_mismatch")
            return index, f"proposals[{index}].customerDecisionFields", reason, expected
    return None


async def run_single_order(snapshot: dict, config: OrderAgentConfig | None = None, *,
                           run_id: str | None = None,
                           on_event: Callable[..., object] | None = None,
                           run_state: Callable[[], dict[str, Any]] | None = None,
                           human_wait_seconds: int = 0) -> dict:
    snapshot = _validate_snapshot(snapshot)
    config = config or OrderAgentConfig.from_env()
    if (type(human_wait_seconds) is not int or not 0 <= human_wait_seconds <= 600 or
            (human_wait_seconds and run_state is None)):
        raise OrderAgentError("invalid_human_wait", "人の操作待機は0～600秒で指定してください。")
    if human_wait_seconds and config.run_timeout_seconds < human_wait_seconds + 240:
        raise OrderAgentError("invalid_human_wait",
                              "run全体の期限は人の操作待機より240秒以上長くしてください。")
    try:
        from agent_framework import Agent
        from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient
        from openai import AsyncOpenAI
    except ImportError:
        raise OrderAgentError("sdk_unavailable", "固定版のMicrosoft Agent Frameworkが必要です。") from None

    run_id = run_id or f"order-agent-{uuid4().hex}"
    order = snapshot["order"]
    inventory = {item["warehouseId"]: item["available"] for item in snapshot["inventory"]}
    reads: list[dict] = []
    submitted: dict | None = None
    submission_attempts = 0
    reads_complete = asyncio.Event()
    human_gate = asyncio.Event()
    captured_tools: set[str] = set()

    def record_ref_attempt(proposals: object, *, outcome: str,
                           error_code: str | None = None,
                           field_path: str | None = None,
                           reason: str | None = None,
                           source_check_reached: bool = False) -> None:
        """Pass only sourceRefs to the store's bounded diagnostic, not model prose."""
        if on_event is None:
            return
        if isinstance(proposals, list) and len(proposals) == 2:
            items = enumerate(proposals)
        else:
            items = [(-1, None)]
        match = re.match(r"proposals\[([01])\](?:\.|$)", field_path or "")
        failed_index = int(match.group(1)) if match else None
        for index, proposal in items:
            if isinstance(proposal, dict):
                if "sourceRefs" not in proposal:
                    refs, shape = None, "missing"
                else:
                    refs = proposal["sourceRefs"]
                    shape = ("string_array" if isinstance(refs, list) and
                             all(isinstance(ref, str) for ref in refs) else
                             "mixed_array" if isinstance(refs, list) else "invalid_shape")
            else:
                refs, shape = None, "unavailable"
            if outcome == "validated":
                ref_outcome = "validated"
            elif not source_check_reached:
                ref_outcome = "not_checked"
            elif failed_index is None:
                ref_outcome = ("validated" if field_path == "proposals" and
                               reason == "normal_and_express_required" else "not_checked")
            elif index < failed_index:
                ref_outcome = "validated"
            elif index > failed_index:
                ref_outcome = "not_checked"
            elif field_path == f"proposals[{index}].sourceRefs":
                ref_outcome = "rejected"
            elif reason == "duplicate_carrier_option":
                ref_outcome = "validated"
            else:
                ref_outcome = "not_checked"
            # A proposal may have valid sourceRefs even when its business option
            # is rejected. Keep the business reason in draft_rejected, not in a
            # validated source-reference diagnostic row.
            own_error = (index == failed_index or index == -1) and ref_outcome != "validated"
            on_event(kind="source_ref_attempt_captured", attempt_no=submission_attempts,
                     proposal_index=index, submitted_refs=refs, submitted_shape=shape,
                     expected_refs=expected_source_refs_for_proposal(snapshot, proposal),
                     outcome=ref_outcome,
                     error_code=error_code if own_error else None,
                     field_path=field_path if own_error else None,
                     reason=reason if own_error else None)

    def record(source_id: str, version: str | None) -> None:
        reads.append({"sourceId": source_id, "version": version,
                      "dataMode": "synthetic_fixture"})
        if on_event is not None:
            on_event(kind="source_read", actor="OrderInvestigator", source_id=source_id,
                     message="合成資料をToolで読み取りました。")

    def capture_tool_response(tool_name: str, response_text: str,
                              source_ids: list[str]) -> None:
        """Persist the exact string before it is returned to the model."""
        if on_event is not None:
            on_event(kind="tool_response_captured", actor="OrderInvestigator",
                     tool_name=tool_name, response_text=response_text,
                     response_sha256=hashlib.sha256(response_text.encode("utf-8")).hexdigest(),
                     source_ids=source_ids, fixture_version=FIXTURE_VERSION)
    def complete_read(tool_name: str) -> None:
        captured_tools.add(tool_name)
        if human_wait_seconds and captured_tools == {"read_order_inventory", "read_carriers_contract"}:
            if not reads_complete.is_set():
                reads_complete.set()
                if on_event is not None:
                    on_event(kind="status", actor="system",
                             message=(f"Tool読取後、合成倉庫担当者の在庫補正を最大{human_wait_seconds}秒待ちます。"
                                      "補正後、旧案は保存されません。現行版の調査は再依頼で開始してください。"))

    async def read_order_inventory() -> str:
        """Read the synthetic order, stock exception, and both warehouse quantities."""
        order_id = f"order-{ORDER_ID}-v{order['version']}"
        response_text = json.dumps({
            "dataMode": "synthetic_fixture", "orderSourceId": order_id,
            "orderVersion": order["version"], "quantityBoxes": order["quantity"],
            "dueAt": order["dueAt"], "detectedAt": snapshot["exception"]["detectedAt"],
            "warehouses": [
                {"sourceId": f"stock-{warehouse}-snapshot-order-v{order['version']}",
                 "warehouseId": warehouse, "availableBoxes": inventory[warehouse]}
                for warehouse in ("warehouse-a", "warehouse-b")
            ],
        }, ensure_ascii=False)
        capture_tool_response("read_order_inventory", response_text,
                              [order_id, *[
                                  f"stock-{warehouse}-snapshot-order-v{order['version']}"
                                  for warehouse in ("warehouse-a", "warehouse-b")]])
        record(order_id, str(order["version"]))
        for warehouse in ("warehouse-a", "warehouse-b"):
            record(f"stock-{warehouse}-snapshot-order-v{order['version']}", None)
        complete_read("read_order_inventory")
        return response_text

    async def read_carriers_contract() -> str:
        """Read three synthetic carrier schedules and the split-delivery consent clause."""
        response_text = json.dumps({"dataMode": "synthetic_fixture",
                                    "carriers": list(CARRIER_SOURCES.values()),
                                    "contract": CONTRACT_SOURCE}, ensure_ascii=False)
        capture_tool_response("read_carriers_contract", response_text,
                              [*CARRIER_SOURCES, CONTRACT_SOURCE["sourceId"]])
        for source in CARRIER_SOURCES.values():
            record(source["sourceId"], source["version"])
        record(CONTRACT_SOURCE["sourceId"], CONTRACT_SOURCE["version"])
        complete_read("read_carriers_contract")
        return response_text

    async def submit_adjustment_plan(proposals_json: str) -> str:
        """Receive exactly two unsaved A/B shipment proposals as a JSON array string.

        Each proposal's customerDecisionFields is a string array of field
        names, not a map of consent values: ["acceptedSplit"] or
        ["acceptedSplit", "acceptedExpressCost"]. Receipt is not final API
        validation, customer consent, or shipment approval.
        """
        nonlocal submitted, submission_attempts
        if human_wait_seconds:
            # Only the authoritative watcher releases this Tool. In both exit
            # cases it cancels agent.run, so no old-source proposal is accepted.
            await human_gate.wait()
            raise OrderAgentError("human_wait_closed", "人の操作待機が終了しました。")
        if submitted is not None:
            return json.dumps({"receivedForValidation": True,
                               "businessDraftSaved": False,
                               "message": "案は受領済みです。最終返答に進んでください。"}, ensure_ascii=False)
        submission_attempts += 1
        if submission_attempts > 3:
            raise OrderAgentError("invalid_plan", "案の提出試行が上限を超えました。")
        if not isinstance(proposals_json, str) or len(proposals_json) > 8000:
            record_ref_attempt(None, outcome="rejected", error_code="invalid_json",
                               field_path="proposals_json", reason="invalid_or_oversized_json")
            if on_event is not None:
                on_event(kind="draft_rejected", actor="OrderInvestigator",
                         message="案を拒否しました: invalid_json (proposals_json: invalid_or_oversized_json)")
            return json.dumps({"receivedForValidation": False,
                               "errorCode": "invalid_json", "fieldPath": "proposals_json",
                               "reason": "invalid_or_oversized_json",
                               "message": "案JSON文字列を確認してください。"}, ensure_ascii=False)
        try:
            proposals = json.loads(proposals_json)
        except json.JSONDecodeError:
            record_ref_attempt(None, outcome="rejected", error_code="invalid_json",
                               field_path="proposals_json", reason="invalid_json")
            if on_event is not None:
                on_event(kind="draft_rejected", actor="OrderInvestigator",
                         message="案を拒否しました: invalid_json (proposals_json: invalid_json)")
            return json.dumps({"receivedForValidation": False,
                               "errorCode": "invalid_json", "fieldPath": "proposals_json",
                               "reason": "invalid_json",
                               "message": "案JSON文字列を確認してください。"}, ensure_ascii=False)
        if not isinstance(proposals, list) or len(proposals) != 2:
            record_ref_attempt(proposals, outcome="rejected", error_code="proposal_count",
                               field_path="proposals", reason="expected_two_proposals")
            if on_event is not None:
                on_event(kind="draft_rejected", actor="OrderInvestigator",
                         message="案を拒否しました: proposal_count (proposals: expected_two_proposals)")
            return json.dumps({"receivedForValidation": False,
                               "errorCode": "proposal_count", "fieldPath": "proposals",
                               "reason": "expected_two_proposals",
                               "message": "案は2件の配列にしてください。"}, ensure_ascii=False)
        issue = _decision_field_shape_issue(proposals)
        if issue is not None:
            index, field_path, reason, expected = issue
            record_ref_attempt(proposals, outcome="rejected",
                               error_code="customer_decision_fields_shape",
                               field_path=field_path, reason=reason)
            if on_event is not None:
                on_event(kind="draft_rejected", actor="OrderInvestigator",
                         message=f"案を拒否しました: customer_decision_fields_shape ({field_path}: {reason})")
            return json.dumps({"receivedForValidation": False,
                               "errorCode": "customer_decision_fields_shape",
                               "proposalIndex": index,
                               "fieldPath": field_path, "reason": reason,
                               "expectedFieldNames": expected,
                               "message": "customerDecisionFieldsは同意値ではなく文字列の配列です。"}, ensure_ascii=False)
        candidate = {
            "recommendation": "通常便のA/B別送案を照合してください。",
            "alternatives": "倉庫Bの特急便を使うA/B別送案を照合してください。",
            "customerQuestion": "別送への承諾と特急費用の負担先を確認してください。",
            "proposals": proposals,
        }
        try:
            validate_structured_draft(snapshot, candidate, [
                {"kind": "source_read", "sourceId": item["sourceId"]} for item in reads
            ])
        except DraftGroundingError as error:
            record_ref_attempt(proposals, outcome="rejected", error_code=error.code,
                               field_path=error.field_path, reason=error.reason,
                               source_check_reached=True)
            if on_event is not None:
                on_event(kind="draft_rejected", actor="OrderInvestigator",
                         message=f"資料照合で案を拒否しました: {error.code} ({error.field_path}: {error.reason})")
            return json.dumps({"receivedForValidation": False,
                               "errorCode": error.code,
                               "fieldPath": error.field_path, "reason": error.reason,
                               "message": "Tool読取資料と数量・日時・費用・参照を照合して再提出してください。"}, ensure_ascii=False)
        record_ref_attempt(proposals, outcome="validated")
        submitted = candidate
        if on_event is not None:
            on_event(kind="draft_submitted", actor="OrderInvestigator",
                     message="最終API保存前の構造化案を受領しました。")
        return json.dumps({"receivedForValidation": True,
                           "businessDraftSaved": False, "shipmentChanged": False}, ensure_ascii=False)

    provider = AsyncOpenAI(api_key=config.api_key, base_url=config.base_url,
                           max_retries=0, timeout=config.request_timeout_seconds)
    try:
        client_type = OpenAIChatClient if config.protocol == "responses" else OpenAIChatCompletionClient
        agent = Agent(
            client=client_type(model=config.model_id, async_client=provider,
                               function_invocation_configuration={"allow_concurrent_invocation": False}),
            name="OrderInvestigator", id="OrderInvestigator",
            instructions=(
                "合成受注の出荷例外を調べるAgentです。必ず最初にread_order_inventoryをToolで呼び、"
                "次にread_carriers_contractをToolで呼んでください。読んだ資料から数量、納期、費用を計算し、"
                "最後にsubmit_adjustment_planを1回だけToolで呼んでください。"
                "proposals_jsonは通常便案と倉庫B特急便案の2件のJSON配列文字列。各案は"
                "deliveryPattern='separate_warehouse_shipments'、shipments配列に倉庫A/Bの"
                "warehouseId、quantityBoxes、carrierSourceId、deliveryAtを入れてください。"
                "各案にadditionalCostJpy、customerDecisionFields、sourceRefsを入れてください。"
                "sourceRefsは実際にToolで読んだ受注版・両倉庫・使う2便・契約のsourceIdです。"
                "customerDecisionFieldsは同意値ではなく文字列の配列です。"
                "通常案は['acceptedSplit']、追加費用案は['acceptedSplit','acceptedExpressCost']。"
                "資料に無い一括輸送と費用負担者を断定せず、分納承諾・契約適合・出荷承認は得ていません。"
                "ToolがreceivedForValidation=falseを返したら型と資料値を直して再提出してください。"
                "receivedForValidation=trueでも業務APIの最終照合・保存前です。"
                "最後は『顧客未回答・出荷未承認』と明記し、確認事項を短く返してください。"
            ),
            tools=[read_order_inventory, read_carriers_contract, submit_adjustment_plan],
        )
        request_text = snapshot.get("requestText")
        if not isinstance(request_text, str) or not request_text.strip():
            raise OrderAgentError("invalid_request", "保存した依頼文が見つかりません。", run_id=run_id)
        required = {f"order-{ORDER_ID}-v{order['version']}",
                    f"stock-warehouse-a-snapshot-order-v{order['version']}",
                    f"stock-warehouse-b-snapshot-order-v{order['version']}",
                    *CARRIER_SOURCES, CONTRACT_SOURCE["sourceId"]}
        final_reply_mode = "model_final_reply_received"
        # asyncio.wait reports whether OUR deadline elapsed; an internal
        # TimeoutError raised by the model or a Tool remains an ordinary failure.
        task = asyncio.create_task(agent.run(request_text))
        watcher = (asyncio.create_task(wait_for_inventory_correction(
            reads_complete, run_state, human_wait_seconds)) if human_wait_seconds else None)
        outer_limit = (min(config.run_timeout_seconds - 10, human_wait_seconds + 230)
                       if human_wait_seconds else min(config.run_timeout_seconds - 10, 160))
        done, _ = await asyncio.wait({task, watcher} if watcher else {task}, timeout=outer_limit,
                                     return_when=asyncio.FIRST_COMPLETED)
        if watcher and watcher in done:
            outcome = watcher.result()
            human_gate.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise OrderAgentError(outcome, "在庫補正待機が終了しました。", run_id=run_id,
                                  diagnostics={"sourceReads": reads})
        if watcher:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        if task in done:
            response = task.result()
            body = getattr(response, "text", "") or ""
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # The submit Tool already checked the structured source values.
            # Only this outer deadline may be recovered.
            if submitted is None or not required.issubset({item["sourceId"] for item in reads}):
                raise TimeoutError("Agent run deadline elapsed without an accepted Tool submission")
            final_reply_mode = "tool_submitted_model_final_timeout"
            body = ("AgentのToolが構造化案を提出しました。モデルの最終返答は時間切れで未取得です。"
                    "顧客未回答・出荷未承認です。業務APIが案を資料と再照合します。")
        if not required.issubset({item["sourceId"] for item in reads}):
            raise OrderAgentError("insufficient_sources", "必要な資料のTool読取が不足しています。",
                                  run_id=run_id, diagnostics={"sourceReads": reads})
        if submitted is None or not body.strip():
            raise OrderAgentError("agent_incomplete", "案の提出または最終返答がありません。",
                                  run_id=run_id,
                                  diagnostics={"sourceReads": reads,
                                               "planSubmitted": submitted is not None})
        return {
            "runId": run_id, "orderId": ORDER_ID, "orderVersion": order["version"],
            "agentVersion": AGENT_VERSION + "-single", "generationMode": "live_model",
            "dataMode": "synthetic_fixture", "authorizationMode": "not_connected_synthetic_only",
            "configuredModelRoute": config.route, "configuredModelProtocol": config.protocol,
            "modelId": config.model_id, "draftBody": body, "draftPlan": submitted,
            "finalReplyMode": final_reply_mode,
            "modelFinalReplyReceived": final_reply_mode == "model_final_reply_received",
            "contractReviewStatus": "not_evaluated", "requiresCustomerAnswer": True,
            "requiresHumanApproval": True, "shipmentChanged": False,
            "sourceReads": reads, "managerEvents": [],
            "gatewayRequestIds": [], "persistence": "not_connected",
        }
    except OrderAgentError:
        raise
    except Exception as error:
        category, types = _error_category(error)
        raise OrderAgentError(category, "受注Agentの実行が完了しませんでした。",
                              run_id=run_id,
                              diagnostics={**types, "sourceReads": reads,
                                           "planSubmitted": submitted is not None}) from None
    finally:
        await provider.close()
