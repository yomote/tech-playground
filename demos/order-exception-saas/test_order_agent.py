"""Checks for the synthetic-only, read-only live Agent adapter contract."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from order_agent import (CARRIER_SOURCES, CONTRACT_SOURCE, OrderAgentConfig,
                         OrderAgentError, _reject_post_submit_loop, _require_complete_run,
                         _validate_snapshot)
from server import OrderStore
import source_fixtures


class OrderAgentContractTests(TestCase):
    def test_current_domain_snapshot_is_accepted(self) -> None:
        with TemporaryDirectory() as folder:
            snapshot = OrderStore(Path(folder) / "orders.sqlite3").get_detail("SO-24017")
        self.assertEqual(_validate_snapshot(snapshot)["order"]["quantity"], 40)
        self.assertEqual(snapshot["inventory"][0]["available"], 25)

    def test_changed_contract_fixture_is_rejected(self) -> None:
        with TemporaryDirectory() as folder:
            snapshot = OrderStore(Path(folder) / "orders.sqlite3").get_detail("SO-24017")
        snapshot["exception"]["contractConstraint"] = "同意は不要"
        with self.assertRaises(OrderAgentError) as raised:
            _validate_snapshot(snapshot)
        self.assertEqual(raised.exception.code, "fixture_mismatch")

    def test_carrier_sources_are_raw_facts_with_versions(self) -> None:
        self.assertIs(CARRIER_SOURCES, source_fixtures.CARRIER_SOURCES)
        self.assertIs(CONTRACT_SOURCE, source_fixtures.CONTRACT_SOURCE)
        self.assertEqual(len(CARRIER_SOURCES), 3)
        for source in CARRIER_SOURCES.values():
            self.assertEqual(source["dataMode"], "synthetic_fixture")
            self.assertTrue(source["version"])
            self.assertNotIn("allocations", source)
            self.assertNotIn("generationMode", source)
        self.assertEqual(CONTRACT_SOURCE["sourceId"], "contract-split-v1")

    def test_model_config_is_required_and_secret_is_not_repr(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(OrderAgentError) as raised:
                OrderAgentConfig.from_env()
        self.assertEqual(raised.exception.code, "model_not_configured")
        config = OrderAgentConfig("trial-model", "secret-for-test", "http://127.0.0.1:11434/v1", "direct")
        self.assertNotIn("secret-for-test", repr(config))

    def test_sdk_stop_is_never_a_successful_draft(self) -> None:
        with self.assertRaises(OrderAgentError) as raised:
            _require_complete_run(
                body="Workflow terminated due to reaching maximum round count.",
                manager_events=[{"nextSpeaker": "StockInvestigator", "requestSatisfied": False}],
                source_reads=[], plan=None, run_id="trial-stop",
            )
        self.assertEqual(raised.exception.code, "agent_incomplete")
        self.assertEqual(raised.exception.run_id, "trial-stop")

    def test_manager_and_source_reads_are_required(self) -> None:
        full_reads = [
            {"sourceId": source_id} for source_id in (
                "order-SO-24017-v1", "stock-warehouse-a-snapshot-order-v1",
                "stock-warehouse-b-snapshot-order-v1", "carrier-a-normal-v1",
                "carrier-b-normal-v1", "carrier-b-express-v1", "contract-split-v1",
            )
        ]
        manager_events = [
            {"nextSpeaker": "StockInvestigator", "requestSatisfied": False},
            {"nextSpeaker": "DispatchPlanner", "requestSatisfied": False},
            {"nextSpeaker": None, "requestSatisfied": True},
        ]
        plan = {"recommendation": "要確認", "alternatives": "要確認", "customerQuestion": "分納できますか。"}
        _require_complete_run(body="確認待ちです。", manager_events=manager_events,
                              source_reads=full_reads, plan=plan, run_id="trial-good")
        with self.assertRaises(OrderAgentError) as raised:
            _require_complete_run(body="確認待ちです。", manager_events=manager_events,
                                  source_reads=full_reads[:-1], plan=plan, run_id="trial-missing")
        self.assertEqual(raised.exception.code, "insufficient_sources")
        with self.assertRaises(OrderAgentError) as raised:
            _require_complete_run(body="確認待ちです。", manager_events=manager_events,
                                  source_reads=full_reads, plan=None, run_id="trial-no-plan")
        self.assertEqual(raised.exception.code, "plan_not_submitted")

    def test_repeated_manager_selection_after_draft_fails_without_success(self) -> None:
        plan = {"recommendation": "未照合案", "alternatives": "納期遅延案",
                "customerQuestion": "分納を承諾しますか。"}
        events = [{"nextSpeaker": "DispatchPlanner", "requestSatisfied": False}]
        for _ in range(2):
            _reject_post_submit_loop(manager_events=events, submitted_after_event_count=0,
                                     source_reads=[], plan=plan, run_id="trial-loop")
            events.append({"nextSpeaker": "DispatchPlanner", "requestSatisfied": False})
        with self.assertRaises(OrderAgentError) as raised:
            _reject_post_submit_loop(manager_events=events, submitted_after_event_count=0,
                                     source_reads=[], plan=plan, run_id="trial-loop")
        self.assertEqual(raised.exception.code, "manager_loop_after_draft")
        self.assertEqual(raised.exception.diagnostics["postSubmitReselections"], 3)
        with self.assertRaises(OrderAgentError) as incomplete:
            _require_complete_run(body="提出済み", manager_events=events, source_reads=[],
                                  plan=plan, run_id="trial-loop")
        self.assertEqual(incomplete.exception.code, "agent_incomplete")
