"""Live MAF Magentic trial for one synthetic shipment exception.

The adapter only reads a domain snapshot and synthetic source fixtures. It cannot
reserve stock, approve a plan, or create a shipment instruction. Its output is
an unverified draft for the order clerk and shipping manager to examine.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4

from source_fixtures import CARRIER_SOURCES, CONTRACT_SOURCE


ORDER_ID = "SO-24017"
AGENT_VERSION = "shipment-exception-initial-v2"
SCENARIO_TIME = "2026-10-19T17:00:00+09:00"
POST_SUBMIT_RESELECTION_LIMIT = 3


class OrderAgentError(RuntimeError):
    """An error with a code and safe diagnostics; provider text is omitted."""

    def __init__(self, code: str, message: str, *, run_id: str | None = None,
                 diagnostics: dict | None = None):
        self.code = code
        self.run_id = run_id
        self.diagnostics = diagnostics or {}
        super().__init__(message)


@dataclass(frozen=True)
class OrderAgentConfig:
    model_id: str
    api_key: str = field(repr=False)
    base_url: str
    route: str
    protocol: str = "responses"
    request_timeout_seconds: int = 60
    run_timeout_seconds: int = 180

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.model_id, self.api_key, self.base_url, self.route)):
            raise OrderAgentError("model_not_configured", "受注Agentのモデル接続設定が不足しています。")
        if self.route not in ("direct", "gateway"):
            raise OrderAgentError("invalid_route", "モデル経路は direct または gateway にしてください。")
        if self.protocol not in ("responses", "chat-completions"):
            raise OrderAgentError("invalid_protocol", "モデルAPI方式を確認してください。")
        if not 5 <= self.request_timeout_seconds <= 120 or not 30 <= self.run_timeout_seconds <= 300:
            raise OrderAgentError("invalid_timeout", "モデル要求とrun全体の待ち時間を確認してください。")
        try:
            endpoint = urlsplit(self.base_url)
            port = endpoint.port
            host = endpoint.hostname
        except ValueError:
            raise OrderAgentError("invalid_endpoint", "モデル接続先のURLを確認してください。") from None
        loopback = host in ("localhost", "127.0.0.1", "::1")
        if (endpoint.scheme not in ("http", "https") or not host or
                (endpoint.scheme == "http" and (not loopback or port is None)) or
                endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
            raise OrderAgentError("invalid_endpoint", "モデル接続先のURLを確認してください。")

    @classmethod
    def from_env(cls) -> OrderAgentConfig:
        try:
            request_timeout = int(os.environ.get("ORDER_AGENT_REQUEST_TIMEOUT_SECONDS", "60"))
            run_timeout = int(os.environ.get("ORDER_AGENT_RUN_TIMEOUT_SECONDS", "180"))
        except ValueError:
            raise OrderAgentError("invalid_timeout", "待ち時間を秒数で指定してください。") from None
        return cls(
            model_id=os.environ.get("ORDER_AGENT_MODEL_ID", "").strip(),
            api_key=os.environ.get("ORDER_AGENT_API_KEY", "").strip(),
            base_url=os.environ.get("ORDER_AGENT_BASE_URL", "").strip(),
            route=os.environ.get("ORDER_AGENT_ROUTE", "").strip(),
            protocol=os.environ.get("ORDER_AGENT_PROTOCOL", "responses").strip(),
            request_timeout_seconds=request_timeout,
            run_timeout_seconds=run_timeout,
        )


def _validate_snapshot(snapshot: object) -> dict[str, Any]:
    if not isinstance(snapshot, dict) or snapshot.get("dataMode") != "synthetic_fixture":
        raise OrderAgentError("invalid_snapshot", "合成受注の詳細snapshotが必要です。")
    order = snapshot.get("order")
    exception = snapshot.get("exception")
    inventory = snapshot.get("inventory")
    if not isinstance(order, dict) or order.get("id") != ORDER_ID or order.get("status") not in (
            "exception_detected", "awaiting_approval"):
        raise OrderAgentError("invalid_snapshot", "対象の出荷例外が見つかりません。")
    if (type(order.get("version")) is not int or order["version"] < 1 or
            type(order.get("quantity")) is not int or order["quantity"] < 1 or
            not isinstance(order.get("dueAt"), str)):
        raise OrderAgentError("invalid_snapshot", "受注の版、数量、納期を確認してください。")
    if (not isinstance(exception, dict) or exception.get("detectedAt") != SCENARIO_TIME or
            exception.get("contractConstraint") != CONTRACT_SOURCE["clause"]):
        raise OrderAgentError("fixture_mismatch", "合成便・契約資料と受注例外の条件が一致しません。")
    if not isinstance(inventory, list):
        raise OrderAgentError("invalid_snapshot", "倉庫在庫が見つかりません。")
    stock: dict[str, int] = {}
    for item in inventory:
        if (not isinstance(item, dict) or item.get("warehouseId") not in
                ("warehouse-a", "warehouse-b") or type(item.get("available")) is not int or
                item["available"] < 0 or item["warehouseId"] in stock):
            raise OrderAgentError("invalid_snapshot", "倉庫在庫の値を確認してください。")
        stock[item["warehouseId"]] = item["available"]
    if set(stock) != {"warehouse-a", "warehouse-b"}:
        raise OrderAgentError("invalid_snapshot", "両倉庫の在庫が必要です。")
    # M0's `options` are completed fixed proposals. They are intentionally not
    # used by any Agent tool or prompt in this module.
    return snapshot


def _require_complete_run(*, body: str, manager_events: list[dict],
                          source_reads: list[dict], plan: dict | None, run_id: str) -> None:
    diagnostics = {"managerEvents": manager_events, "sourceReads": source_reads,
                   "planSubmitted": plan is not None, "draftPlan": plan}
    if body.startswith("Workflow terminated due to"):
        diagnostics["sdkTermination"] = body
    if (body.startswith("Workflow terminated due to") or
            not any(item.get("requestSatisfied") is True for item in manager_events)):
        raise OrderAgentError("agent_incomplete", "管理Agentが調整案を完了できませんでした。",
                              run_id=run_id, diagnostics=diagnostics)
    selected = {item.get("nextSpeaker") for item in manager_events}
    if not {"StockInvestigator", "DispatchPlanner"}.issubset(selected):
        raise OrderAgentError("specialist_not_selected", "必要な専門Agentの選択を確認できません。",
                              run_id=run_id, diagnostics=diagnostics)
    source_ids = {item["sourceId"] for item in source_reads}
    required = {f"order-{ORDER_ID}-v", "stock-warehouse-a", "stock-warehouse-b",
                "carrier-a-normal-v1", "carrier-b-normal-v1", "carrier-b-express-v1",
                "contract-split-v1"}
    if not all(any(actual.startswith(needed) for actual in source_ids) for needed in required):
        raise OrderAgentError("insufficient_sources", "受注・在庫・便・契約のTool読取りが不足しています。",
                              run_id=run_id, diagnostics=diagnostics)
    if plan is None:
        raise OrderAgentError("plan_not_submitted", "調整案と顧客への質問が提出されませんでした。",
                              run_id=run_id, diagnostics=diagnostics)
    if not body.strip():
        raise OrderAgentError("empty_model_output", "管理Agentの最終説明がありません。",
                              run_id=run_id, diagnostics=diagnostics)


def _reject_post_submit_loop(*, manager_events: list[dict], submitted_after_event_count: int | None,
                             source_reads: list[dict], plan: dict | None, run_id: str) -> None:
    """Fail a repeated specialist selection without treating a submitted draft as success."""
    if submitted_after_event_count is None:
        return
    repeats = sum(
        item.get("nextSpeaker") == "DispatchPlanner" and item.get("requestSatisfied") is False
        for item in manager_events[submitted_after_event_count:]
    )
    if repeats >= POST_SUBMIT_RESELECTION_LIMIT:
        raise OrderAgentError(
            "manager_loop_after_draft", "管理Agentが案の提出後も同じ担当を選び続けました。",
            run_id=run_id,
            diagnostics={"managerEvents": manager_events, "sourceReads": source_reads,
                         "planSubmitted": plan is not None, "draftPlan": plan,
                         "postSubmitReselections": repeats},
        )


def _error_category(error: Exception) -> tuple[str, dict]:
    error_type = type(error).__name__
    cause = getattr(error, "__cause__", None)
    diagnostics = {"errorType": error_type}
    if cause is not None:
        diagnostics["causeType"] = type(cause).__name__
    known = {
        "APITimeoutError": "provider_timeout", "APIConnectionError": "provider_unavailable",
        "RateLimitError": "provider_rate_limit", "AuthenticationError": "provider_authentication",
        "PermissionDeniedError": "provider_authentication", "BadRequestError": "provider_invalid_request",
        "ChatClientInvalidRequestException": "provider_invalid_request",
        "ChatClientException": "provider_unavailable", "TimeoutError": "execution_timeout",
    }
    category = known.get(error_type, "agent_run_failed")
    if cause is not None:
        category = known.get(type(cause).__name__, category)
    return category, diagnostics


async def run_initial_order(snapshot: dict, config: OrderAgentConfig | None = None, *,
                            run_id: str | None = None,
                            on_event: Callable[..., object] | None = None) -> dict:
    """Run the real SDK and return an unsaved draft; never mutate order state."""
    snapshot = _validate_snapshot(snapshot)
    config = config or OrderAgentConfig.from_env()
    try:
        from agent_framework import Agent
        from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient
        from agent_framework.orchestrations import MagenticBuilder
        from openai import AsyncOpenAI
    except ImportError:
        raise OrderAgentError("sdk_unavailable", "固定版のMicrosoft Agent Frameworkが必要です。") from None

    run_id = run_id or f"order-agent-{uuid4().hex}"
    reads: list[dict] = []
    manager_events: list[dict] = []
    submitted: dict | None = None
    submitted_after_event_count: int | None = None
    order = snapshot["order"]
    inventory = {item["warehouseId"]: item["available"] for item in snapshot["inventory"]}

    def record_read(actor: str, source_id: str, version: str | None) -> None:
        reads.append({"sourceId": source_id, "version": version, "dataMode": "synthetic_fixture"})
        if on_event is not None:
            on_event(kind="source_read", actor=actor, source_id=source_id,
                     message="合成資料を読み取りました。")

    async def read_order() -> str:
        """Read the current synthetic order and exception, without fixed plans."""
        source_id = f"order-{ORDER_ID}-v{order['version']}"
        record_read("StockInvestigator", source_id, str(order["version"]))
        return json.dumps({
            "sourceId": source_id, "dataMode": "synthetic_fixture",
            "orderId": order["id"], "orderVersion": order["version"],
            "sku": order["sku"], "quantityBoxes": order["quantity"],
            "dueAt": order["dueAt"], "exception": snapshot["exception"]["message"],
            "exceptionDetectedAt": snapshot["exception"]["detectedAt"],
        }, ensure_ascii=False)

    async def read_inventory(warehouse_id: str) -> str:
        """Read current synthetic available boxes at warehouse-a or warehouse-b."""
        if warehouse_id not in inventory:
            raise OrderAgentError("source_not_available", "対象倉庫の在庫はありません。")
        source_id = f"stock-{warehouse_id}-snapshot-order-v{order['version']}"
        record_read("StockInvestigator", source_id, None)
        return json.dumps({
            "sourceId": source_id, "versionKnown": False,
            "snapshotOrderVersion": order["version"], "dataMode": "synthetic_fixture",
            "warehouseId": warehouse_id, "availableBoxes": inventory[warehouse_id],
        }, ensure_ascii=False)

    async def read_delivery_schedule(schedule_id: str) -> str:
        """Read a synthetic carrier's cutoff, delivery time, capacity, and cost."""
        source = CARRIER_SOURCES.get(schedule_id)
        if source is None:
            raise OrderAgentError("source_not_available", "対象の配送便資料はありません。")
        record_read("DispatchPlanner", source["sourceId"], source["version"])
        return json.dumps(source, ensure_ascii=False)

    async def read_contract_term(term_id: str) -> str:
        """Read the synthetic split-delivery clause; this does not verify a plan."""
        if term_id != CONTRACT_SOURCE["sourceId"]:
            raise OrderAgentError("source_not_available", "対象の契約条項はありません。")
        record_read("DispatchPlanner", CONTRACT_SOURCE["sourceId"], CONTRACT_SOURCE["version"])
        return json.dumps(CONTRACT_SOURCE, ensure_ascii=False)

    async def submit_adjustment_plan(recommendation: str, alternatives: str,
                                     customer_question: str) -> str:
        """Capture an unsaved draft and customer question; never issue shipment."""
        nonlocal submitted, submitted_after_event_count
        fields = (recommendation, alternatives, customer_question)
        if any(not isinstance(value, str) or not value.strip() for value in fields):
            raise OrderAgentError("invalid_plan", "調整案、代替案、顧客への質問が必要です。")
        if len(recommendation) > 4000 or len(alternatives) > 4000 or len(customer_question) > 500:
            raise OrderAgentError("invalid_plan", "調整案の文字数が上限を超えています。")
        if submitted is not None:
            raise OrderAgentError("plan_already_submitted", "調整案は提出済みです。")
        submitted = {"recommendation": recommendation, "alternatives": alternatives,
                     "customerQuestion": customer_question}
        submitted_after_event_count = len(manager_events)
        if on_event is not None:
            on_event(kind="draft_submitted", actor="DispatchPlanner",
                     message="出荷変更を伴わない調整案を提出しました。")
        return json.dumps({"acceptedAsDraft": True, "shipmentChanged": False}, ensure_ascii=False)

    provider = AsyncOpenAI(api_key=config.api_key, base_url=config.base_url,
                           max_retries=0, timeout=config.request_timeout_seconds)
    try:
        client_type = OpenAIChatClient if config.protocol == "responses" else OpenAIChatCompletionClient
        client = client_type(model=config.model_id, async_client=provider)
        stock_agent = Agent(
            client=client, name="StockInvestigator", id="StockInvestigator",
            description="受注と両倉庫の現在庫をToolで調べ、不足数量を説明する専門Agent",
            instructions=(
                "read_order()とread_inventory(warehouse_id='warehouse-a')、"
                "read_inventory(warehouse_id='warehouse-b')を呼んで、受注版と数量を報告してください。"
                "固定の対処案は入力されません。出荷変更はしません。"
            ), tools=[read_order, read_inventory],
        )
        dispatch_agent = Agent(
            client=client, name="DispatchPlanner", id="DispatchPlanner",
            description="配送便の時刻と費用、分納条項を読み、顧客へ確認する調整案を作る専門Agent",
            instructions=(
                "read_delivery_scheduleでcarrier-a-normal-v1、carrier-b-normal-v1、"
                "carrier-b-express-v1をすべて読み、read_contract_termでcontract-split-v1を読んでください。"
                "在庫担当の結果と合わせ、納期・費用・分納承諾の違いを比較してください。"
                "submit_adjustment_planでrecommendation、alternatives、customer_questionを提出してください。"
                "acceptedAsDraftがtrueなら作業完了を短く報告し、同じ案を再提出しないでください。"
                "契約条項は読めますが、契約への適合を確定する専門審査はこの版にありません。"
                "顧客の同意や出荷責任者の承認を得たと書かないでください。"
            ), tools=[read_delivery_schedule, read_contract_term, submit_adjustment_plan],
        )
        manager = Agent(
            client=client, name="ShipmentManager", id="ShipmentManager",
            description="在庫と配送の専門Agentを選んで出荷例外の調整案をまとめる管理Agent",
            instructions=(
                "今回の完了条件は、StockInvestigatorとDispatchPlannerが原資料をToolで読み、"
                "DispatchPlannerが未照合の調整案と顧客への質問を提出することです。"
                "DispatchPlannerのsubmit_adjustment_planがacceptedAsDraft=trueを返したら、"
                "今回の依頼は満たされています。他の担当を再選択せず、最終説明を返してください。"
                "契約照合の専門Agentはまだいません。分納の適合確認、顧客承諾、最終出荷判断は"
                "このAgent作業の完了条件に含めず、人に残してください。"
                "最終出力は日本語の説明にし、出荷が確定したと書かないでください。"
            ),
        )
        workflow = MagenticBuilder(
            participants=[stock_agent, dispatch_agent], manager_agent=manager,
            intermediate_output_from=[stock_agent, dispatch_agent],
            max_round_count=8, max_stall_count=2, max_reset_count=1,
        ).build()
        task = (
            f"合成受注{ORDER_ID}の出荷例外を調査。40箱の希望納期は{order['dueAt']}。"
            "受注・倉庫A/B在庫・3つの配送便・分納契約条項をToolで読み、"
            "期限と追加費用を比べて、顧客への質問がある未照合の調整案を"
            "submit_adjustment_planへ一度提出する。acceptedAsDraft=trueで依頼は完了。"
            "契約適合審査・顧客承諾・出荷判断はこの依頼の対象外。出荷状態は変更しない。"
        )

        async def consume() -> str:
            stream = workflow.run(message=task, stream=True)
            async for event in stream:
                if event.type != "magentic_orchestrator":
                    continue
                content = event.data.content
                speaker = getattr(getattr(content, "next_speaker", None), "answer", None)
                satisfied = getattr(getattr(content, "is_request_satisfied", None), "answer", None)
                chosen = speaker if speaker in {"StockInvestigator", "DispatchPlanner"} else None
                observed = {"eventType": event.data.event_type.value,
                            "nextSpeaker": chosen,
                            "requestSatisfied": satisfied if isinstance(satisfied, bool) else None}
                manager_events.append(observed)
                _reject_post_submit_loop(
                    manager_events=manager_events,
                    submitted_after_event_count=submitted_after_event_count,
                    source_reads=reads, plan=submitted, run_id=run_id,
                )
                if chosen and on_event is not None:
                    on_event(kind="manager_selection", actor="ShipmentManager",
                             message=f"{chosen}を選択しました。")
            response = await stream.get_final_response()
            outputs = response.get_outputs()
            return getattr(outputs[-1], "text", "") if outputs else ""

        body = await asyncio.wait_for(consume(), timeout=config.run_timeout_seconds)
        _require_complete_run(body=body, manager_events=manager_events,
                              source_reads=reads, plan=submitted, run_id=run_id)
        return {
            "runId": run_id, "orderId": ORDER_ID, "orderVersion": order["version"],
            "agentVersion": AGENT_VERSION, "generationMode": "live_model",
            "dataMode": "synthetic_fixture", "authorizationMode": "not_connected_synthetic_only",
            "configuredModelRoute": config.route, "configuredModelProtocol": config.protocol,
            "modelId": config.model_id, "draftBody": body, "draftPlan": submitted,
            "contractReviewStatus": "not_evaluated", "requiresCustomerAnswer": True,
            "requiresHumanApproval": True, "shipmentChanged": False,
            "draftFormatValidated": True, "planBusinessValidation": "not_run",
            "sourceReads": reads, "managerEvents": manager_events,
            "gatewayRequestIds": [], "persistence": "not_connected",
        }
    except OrderAgentError:
        raise
    except Exception as error:
        category, error_types = _error_category(error)
        raise OrderAgentError(category, "受注Agentの実行が完了しませんでした。", run_id=run_id,
                              diagnostics={**error_types, "managerEvents": manager_events,
                                           "sourceReads": reads, "planSubmitted": submitted is not None,
                                           "draftPlan": submitted}) from None
    finally:
        await provider.close()
