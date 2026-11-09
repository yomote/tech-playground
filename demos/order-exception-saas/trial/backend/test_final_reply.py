"""Saved model reply boundary; all runners in this file are local test doubles."""

from __future__ import annotations

from contextlib import closing
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from uuid import uuid4

from order_agent import AGENT_VERSION, OrderAgentConfig
from server import (MAX_MODEL_FINAL_REPLY_BYTES, ORDER_ID, OrderStore,
                    RunController, SUPPORTED_REQUEST_TEXT, make_server)


class FinalReplyStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "orders.sqlite3"
        self.validity = patch.object(OrderStore, "fixture_validity", return_value={
            "newRunAvailable": True,
        })
        self.validity.start()
        self.store = OrderStore(self.db_path)

    def tearDown(self) -> None:
        self.validity.stop()
        self.temp.cleanup()

    def create_running(self, execution_mode: str = "live_model") -> tuple[str, dict]:
        snapshot = self.store.get_detail(ORDER_ID)
        run, created = self.store.create_run(
            ORDER_ID, str(uuid4()), route="direct", model_id="local-test-model",
            execution_mode=execution_mode, request_text=SUPPORTED_REQUEST_TEXT,
            expected_version=snapshot["order"]["version"],
        )
        self.assertTrue(created)
        self.assertTrue(self.store.start_run(run["id"]))
        return run["id"], snapshot

    @staticmethod
    def api_draft(model_prose_stored: bool = False) -> dict:
        return {"body": "資料照合済みのAPI投影文", "modelProseStored": model_prose_stored}

    def test_successful_model_reply_is_separate_from_projected_draft_and_get(self) -> None:
        run_id, snapshot = self.create_running()
        reply = "顧客未回答・出荷未承認です。\n通常便と特急便の条件を確認してください。"
        self.assertTrue(self.store.finish_run(
            run_id, "completed", draft=self.api_draft(True),
            source_snapshot=snapshot, model_final_reply=reply,
        ))
        server = make_server(0, self.db_path)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with closing(HTTPConnection("127.0.0.1", server.server_port, timeout=3)) as client:
                client.request("GET", f"/api/agent-runs/{run_id}")
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                saved = json.loads(response.read())["run"]
            self.assertEqual(saved["modelFinalReply"], reply)
            self.assertEqual(saved["draft"]["body"], "資料照合済みのAPI投影文")
            self.assertNotEqual(saved["modelFinalReply"], saved["draft"]["body"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_health_marks_missing_model_configuration_without_exposing_a_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            server = make_server(
                0, self.db_path,
                runs_factory=lambda store: RunController(
                    store, config_loader=OrderAgentConfig.from_env,
                    runner=lambda *_args, **_kwargs: None,
                    execution_mode="live_model", agent_version=AGENT_VERSION + "-single",
                ),
            )
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with closing(HTTPConnection("127.0.0.1", server.server_port, timeout=3)) as client:
                    client.request("GET", "/api/health")
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    health = json.loads(response.read())
                self.assertEqual(health["agentExecutionMode"], "single_agent_live_model")
                self.assertIs(health["modelConfigured"], False)
                self.assertEqual(health["authenticationMode"], "none_local_trial")
                self.assertNotIn("apiKey", health)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_old_schema_migrates_without_inventing_a_reply(self) -> None:
        run_id, snapshot = self.create_running()
        self.assertTrue(self.store.finish_run(
            run_id, "completed", draft=self.api_draft(), source_snapshot=snapshot,
        ))
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("ALTER TABLE agent_runs DROP COLUMN model_final_reply")
            connection.commit()
        self.store = OrderStore(self.db_path)
        old = self.store.get_run(run_id)
        self.assertEqual(old["status"], "completed")
        self.assertIsNone(old["modelFinalReply"])

    def test_timeout_fallback_and_stale_source_never_save_model_reply(self) -> None:
        draft_ready_id, snapshot = self.create_running()
        with self.assertRaises(ValueError):
            self.store.finish_run(
                draft_ready_id, "draft_ready", draft=self.api_draft(),
                source_snapshot=snapshot, model_final_reply="APIの期限切れ補足文",
            )
        self.assertTrue(self.store.finish_run(
            draft_ready_id, "draft_ready", draft=self.api_draft(),
            source_snapshot=snapshot,
        ))
        self.assertIsNone(self.store.get_run(draft_ready_id)["modelFinalReply"])

        stale_id, stale_snapshot = self.create_running()
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("UPDATE orders SET version = version + 1 WHERE id = ?", (ORDER_ID,))
            connection.commit()
        self.assertTrue(self.store.finish_run(
            stale_id, "completed", draft=self.api_draft(),
            source_snapshot=stale_snapshot, model_final_reply="受信したが古くなった返答",
        ))
        stale = self.store.get_run(stale_id)
        self.assertEqual((stale["status"], stale["errorCode"]), ("failed", "source_stale"))
        self.assertIsNone(stale["modelFinalReply"])

    def test_bounded_text_and_test_double_cannot_be_saved_as_model_reply(self) -> None:
        run_id, snapshot = self.create_running()
        for unsafe in ("あ" * (MAX_MODEL_FINAL_REPLY_BYTES // 3 + 1),
                       "Bearer secret-token-value-123456", "制御文字\x00を含む"):
            with self.subTest(unsafe=unsafe[:15]), self.assertRaises(ValueError):
                self.store.finish_run(
                    run_id, "completed", draft=self.api_draft(),
                    source_snapshot=snapshot, model_final_reply=unsafe,
                )
        self.assertEqual(self.store.get_run(run_id)["status"], "running")
        self.assertTrue(self.store.finish_run(run_id, "failed", error_code="test_cleanup"))

        test_id, test_snapshot = self.create_running("test_double")
        with self.assertRaises(ValueError):
            self.store.finish_run(
                test_id, "completed", draft=self.api_draft(),
                source_snapshot=test_snapshot, model_final_reply="模擬返答",
            )
        self.assertTrue(self.store.finish_run(
            test_id, "completed", draft=self.api_draft(), source_snapshot=test_snapshot,
        ))
        self.assertIsNone(self.store.get_run(test_id)["modelFinalReply"])


if __name__ == "__main__":
    unittest.main()
