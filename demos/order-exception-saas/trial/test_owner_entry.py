"""Fixed boundary tests for the isolated owner entry; backend is a test double."""

from __future__ import annotations

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import unittest
from unittest.mock import patch

from owner_entry import make_server


class FakeBackend(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.server.last_headers = dict(self.headers)
        if self.path != "/api/health":
            self.send_error(404)
            return
        body = json.dumps({"authenticationMode": "none_local_trial", "modelConfigured": False}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class OwnerEntryBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        environment = patch.dict("os.environ", {
            "TP_TAILNET_ORIGIN": "https://owner-demo.ts.net:12443",
            "TP_TAILNET_ALLOWED_LOGINS": "owner@example.test",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.backend = ThreadingHTTPServer(("127.0.0.1", 0), FakeBackend)
        self.backend.last_headers = None
        self.entry = make_server(port=0, backend_port=self.backend.server_port)
        self.threads = [Thread(target=server.serve_forever, daemon=True)
                        for server in (self.backend, self.entry)]
        for thread in self.threads:
            thread.start()
        self.addCleanup(self.stop_servers)

    def stop_servers(self) -> None:
        for server in (self.entry, self.backend):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=3)

    def request(self, method: str, path: str, *, login: str | None = "owner@example.test",
                headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        sent = {"Host": "owner-demo.ts.net:12443"}
        if login is not None:
            sent["Tailscale-User-Login"] = login
        sent.update(headers or {})
        connection = HTTPConnection("127.0.0.1", self.entry.server_port, timeout=3)
        try:
            connection.request(method, path, headers=sent)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_identity_and_route_rejections_precede_backend(self) -> None:
        self.assertEqual(self.request("GET", "/trial/api/health", login=None)[0], 403)
        self.assertEqual(self.request("GET", "/trial/api/health", login="other@example.test")[0], 403)
        self.assertEqual(self.request("GET", "/trial/api/orders/other")[0], 404)
        self.assertEqual(self.request("POST", "/trial/api/orders/SO-24017/inventory-count",
                                      headers={"Origin": "https://owner-demo.ts.net:12443"})[0], 401)
        self.assertIsNone(self.backend.last_headers)

    def test_allowed_health_hides_identity_from_backend_and_labels_scope(self) -> None:
        status, body = self.request("GET", "/trial/api/health")
        self.assertEqual(status, 200)
        health = json.loads(body)
        self.assertEqual(health["authenticationMode"], "tailnet_owner_identity")
        self.assertEqual(health["backendAuthenticationMode"], "none_local_trial")
        self.assertEqual(health["businessRoleMode"], "declared_role_fixture")
        self.assertEqual(self.backend.last_headers["Host"], f"127.0.0.1:{self.backend.server_port}")
        self.assertNotIn("Tailscale-User-Login", self.backend.last_headers)


if __name__ == "__main__":
    unittest.main()
