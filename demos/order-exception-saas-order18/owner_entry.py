"""Owner-only Tailnet HTTP boundary for the isolated OE-02 synthetic demo.

Run this on loopback behind Tailscale Serve. Serve supplies the requesting
user's login header; this process accepts only an explicit owner allowlist.
The OE-02 backend remains on loopback and never receives identity headers.
This boundary authenticates the evaluator, not the synthetic customer or
shipping-manager roles used inside the demo.
"""

from __future__ import annotations

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import copy
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, urlsplit


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "apps" / "webhost"))
from tailnet_access import TailnetViewPolicy  # noqa: E402


LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 5390
BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 5340
MOUNT = "/trial-order18"
MAX_REQUEST_BYTES = 16_384
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_BEARER_HEADER_BYTES = 8_192
RUN_ID = r"order-run-[0-9a-f]{32}"
ORDER_BASE = "/api/orders/SO-24018"
INVENTORY_COUNT = ORDER_BASE + "/inventory-count"
# RFC 6750 b64token. The HTTP auth scheme is case-insensitive; separators
# must be ASCII spaces. An exact, bounded header avoids forwarding a second
# credential or a value with control characters to the business backend.
BEARER = re.compile(r"Bearer +[A-Za-z0-9._~+/-]+=*\Z", re.ASCII | re.IGNORECASE)
ASSET = re.compile(r"/assets/[A-Za-z0-9_-]+\.(?:js|css|svg|png|woff2?)\Z")
RUN_QUERY = re.compile(rf"/\?run={RUN_ID}\Z")
RUN_DETAIL = re.compile(rf"/api/agent-runs/{RUN_ID}\Z")
TRIAL_ARCHIVE = re.compile(rf"{ORDER_BASE}/trial-archives/trial-archive-[0-9a-f]{{32}}\Z")
GET_PATHS = {
    "/", "/index.html", "/api/health", ORDER_BASE,
    ORDER_BASE + "/sources", ORDER_BASE + "/agent-runs", "/api/identity",
}
POST_PATHS = {
    ORDER_BASE + "/agent-runs",
    ORDER_BASE + "/new-trial",
    ORDER_BASE + "/agent-customer-response",
    ORDER_BASE + "/cost-source-review",
    ORDER_BASE + "/agent-decision",
    ORDER_BASE + "/cost-source-review-response",
}
HUMAN_POST_PATHS = {
    ORDER_BASE + "/agent-decision",
    ORDER_BASE + "/cost-source-review-response",
}
AUTH_REALM = "/auth/realms/tp-population-20261006"
AUTH_RESOURCE = re.compile(r"/auth/resources/[A-Za-z0-9_./-]+\Z")
PUBLIC_ISSUER = "https://omote-llm.tail47e89e.ts.net:12443/trial/auth/realms/tp-population-20261006"
OIDC_VALUE = re.compile(r"[A-Za-z0-9._~-]{1,2048}\Z", re.ASCII)


def internal_target(target: str) -> str | None:
    """Remove the public mount only after Serve has routed this request."""
    if target == MOUNT:
        return "/"
    if target.startswith(MOUNT + "/"):
        return target[len(MOUNT):]
    return None


def has_keycloak_auth_session(headers) -> bool:
    values = headers.get_all("Cookie", [])
    return len(values) == 1 and any(
        item.strip().startswith("AUTH_SESSION_ID=") and len(item.strip()) > len("AUTH_SESSION_ID=")
        for item in values[0].split(";")
    )


def allowed_auth_path(target: str) -> bool:
    # This trial uses the existing /trial/auth issuer through its own owner gate.
    # Do not expose a second Keycloak mount with different cookie scope.
    return False
    if len(target) > 4096 or "\\" in target or "#" in target:
        return False
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or "%" in parsed.path or ".." in parsed.path:
        return False
    return (parsed.path == AUTH_REALM or parsed.path.startswith(AUTH_REALM + "/") or
            AUTH_RESOURCE.fullmatch(parsed.path) is not None)


def allowed_oidc_callback(target: str) -> bool:
    """Allow only the Keycloak authorization-code return to this trial UI."""
    if len(target) > 4096 or not target.startswith("/?"):
        return False
    parsed = urlsplit(target)
    if parsed.path != "/" or parsed.fragment:
        return False
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=False,
                          strict_parsing=True, max_num_fields=4)
    except ValueError:
        return False
    values = dict(pairs)
    if (len(values) != len(pairs) or not {"state", "code"} <= values.keys() or
            not values.keys() <= {"state", "code", "session_state", "iss"}):
        return False
    if values.get("iss", PUBLIC_ISSUER) != PUBLIC_ISSUER:
        return False
    return all(OIDC_VALUE.fullmatch(value) is not None for key, value in values.items()
               if key != "iss")


def allowed_path(path: str, method: str) -> bool:
    """Permit only routes used to operate and inspect one synthetic order."""
    if method in ("GET", "HEAD"):
        return path in GET_PATHS or ASSET.fullmatch(path) is not None or bool(
            RUN_QUERY.fullmatch(path) or allowed_oidc_callback(path) or
            RUN_DETAIL.fullmatch(path) or TRIAL_ARCHIVE.fullmatch(path)
        )
    return method == "POST" and path in POST_PATHS


def _port(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        port = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer TCP port") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be a TCP port in 1..65535")
    return port


def inventory_authorization(headers) -> str | None:
    """Return one bounded Bearer value only for the inventory-count route."""
    values = headers.get_all("Authorization", [])
    if len(values) != 1:
        return None
    value = values[0]
    if len(value) > MAX_BEARER_HEADER_BYTES or BEARER.fullmatch(value) is None:
        return None
    return value


class Handler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, *_args: object) -> None:
        pass  # No login, request body, run ID, or provider data in access logs.

    def respond(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                         "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def auth_request(self, lengths) -> None:
        # Only the one synthetic realm and its static assets are exposed.
        # Keycloak retains its own protocol, cookies and page CSP.
        if self.command not in ("GET", "HEAD", "POST"):
            return self.respond(405, b'{"error":"Method not allowed"}')
        local_path = internal_target(self.path)
        auth_action = local_path is not None and urlsplit(local_path).path.startswith(AUTH_REALM + "/login-actions/")
        allowed_origins = ([], [self.server.access.origin])
        # Keycloak's no-referrer login form sends Origin: null in Chromium.
        # Its one-time auth-session code/cookie protects this form action.
        if auth_action and has_keycloak_auth_session(self.headers):
            allowed_origins += (["null"],)
        if self.command == "POST" and self.headers.get_all("Origin", []) not in allowed_origins:
            return self.respond(403, b'{"error":"Wrong origin"}')
        try:
            length = int(lengths[0]) if lengths else 0
        except ValueError:
            return self.respond(400, b'{"error":"Invalid body size"}')
        if length < 0 or length > MAX_REQUEST_BYTES or (self.command != "POST" and length):
            return self.respond(413, b'{"error":"Invalid body size"}')
        body = self.rfile.read(length) if length else None
        if length and (body is None or len(body) != length):
            return self.respond(400, b'{"error":"Incomplete body"}')
        headers = {
            "Host": "127.0.0.1:19580",
            "X-Forwarded-Host": self.server.access.host,
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Port": "12443",
            "X-Forwarded-For": "127.0.0.1",
        }
        for name in ("Cookie", "Accept", "Content-Type", "Origin", "Referer"):
            values = self.headers.get_all(name, [])
            if len(values) > 1 or (values and len(values[0]) > 8192):
                return self.respond(400, b'{"error":"Invalid header"}')
            if values:
                headers[name] = values[0]
        upstream = HTTPConnection("127.0.0.1", 19580, timeout=12)
        try:
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            content = response.read(MAX_RESPONSE_BYTES + 1)
            if len(content) > MAX_RESPONSE_BYTES:
                return self.respond(502, b'{"error":"Auth response too large"}')
            location = response.getheader("Location")
            if location and not (location.startswith(self.server.access.origin + MOUNT + "/") or
                                 location.startswith(MOUNT + "/auth/") or location == MOUNT + "/"):
                return self.respond(502, b'{"error":"Unexpected auth redirect"}')
            # Keycloak must scope cookies to this mounted trial. A root cookie
            # could be sent to the public business app on the same origin.
            for name, value in response.getheaders():
                if name.lower() == "set-cookie":
                    cookie_path = re.search(r"(?:^|;)\s*Path=([^;]+)", value, re.IGNORECASE)
                    if cookie_path and not (cookie_path.group(1).strip() == MOUNT + "/auth" or
                                            cookie_path.group(1).strip().startswith(MOUNT + "/auth/")):
                        return self.respond(502, b'{"error":"Unsafe auth cookie path"}')
            self.close_connection = True
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() in {"content-type", "set-cookie", "location", "content-security-policy",
                                    "x-frame-options", "cache-control", "referrer-policy"}:
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(content)
        except (OSError, TimeoutError):
            return self.respond(503, b'{"error":"Auth service unavailable"}')
        finally:
            upstream.close()

    def handle_request(self) -> None:
        # Authorize before exposing route or error details. Only Serve can add a
        # trustworthy login for a remote user; direct loopback calls are trusted.
        policy_headers = copy.deepcopy(self.headers)
        local_path = internal_target(self.path)
        if (self.command == "POST" and local_path is not None and
                urlsplit(local_path).path.startswith(AUTH_REALM + "/login-actions/")
                and self.headers.get_all("Origin", []) == ["null"] and
                has_keycloak_auth_session(self.headers)):
            policy_headers.replace_header("Origin", self.server.access.origin)
        if not self.server.access.allows(policy_headers, self.client_address):
            return self.respond(403, b'{"error":"Access denied"}')
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding") or len(lengths) > 1:
            return self.respond(400, b'{"error":"Invalid request framing"}')
        if self.command not in ("GET", "HEAD", "POST"):
            return self.respond(405, b'{"error":"Method not allowed"}')
        if local_path is None:
            return self.respond(404, b'{"error":"Not found"}')
        if allowed_auth_path(local_path):
            return self.auth_request(lengths)
        if not allowed_path(local_path, self.command):
            return self.respond(404, b'{"error":"Not found"}')

        body = None
        authorization = None
        if self.command == "POST":
            # Browser POSTs must come from the same Tailnet HTTPS origin. The
            # existing TailnetViewPolicy also rejects duplicate/wrong Origin.
            if self.headers.get_all("Origin", []) != [self.server.access.origin]:
                return self.respond(403, b'{"error":"Same-origin request required"}')
            if local_path in HUMAN_POST_PATHS:
                authorization = inventory_authorization(self.headers)
                if authorization is None:
                    return self.respond(401, b'{"error":"One bounded Bearer token required"}')
            if self.headers.get_content_type() != "application/json":
                return self.respond(415, b'{"error":"JSON required"}')
            try:
                length = int(lengths[0]) if lengths else 0
            except ValueError:
                length = 0
            if length < 1 or length > MAX_REQUEST_BYTES:
                return self.respond(413, b'{"error":"Invalid JSON body size"}')
            body = self.rfile.read(length)
            if len(body) != length:
                return self.respond(400, b'{"error":"Incomplete body"}')
        elif lengths and lengths[0] != "0":
            return self.respond(400, b'{"error":"Read requests must have no body"}')
        if local_path == "/api/identity":
            authorization = inventory_authorization(self.headers)
            if authorization is None:
                return self.respond(401, b'{"error":"One bounded Bearer token required"}')

        upstream = HTTPConnection(BACKEND_HOST, self.server.backend_port, timeout=15)
        try:
            # The backend's local same-origin check sees only a fresh local
            # origin. Serve identity, cookies and forwarded claims never pass.
            # Only identity and the two authenticated human routes receive a Bearer token.
            headers = {"Host": f"{BACKEND_HOST}:{self.server.backend_port}"}
            if body is not None:
                headers["Origin"] = f"http://{BACKEND_HOST}:{self.server.backend_port}"
                headers["Content-Type"] = "application/json"
            if authorization is not None:
                headers["Authorization"] = authorization
            upstream.request("GET" if self.command == "HEAD" else self.command,
                             local_path, body=body, headers=headers)
            response = upstream.getresponse()
            content = response.read(MAX_RESPONSE_BYTES + 1)
            if len(content) > MAX_RESPONSE_BYTES or 300 <= response.status < 400:
                return self.respond(502, b'{"error":"Unexpected demo response"}')
            if local_path == "/api/health" and response.status == 200:
                try:
                    health = json.loads(content)
                    if not isinstance(health, dict):
                        raise ValueError("health response must be an object")
                    backend_auth = health.get("authenticationMode")
                    health["authenticationMode"] = "tailnet_owner_identity"
                    health["backendAuthenticationMode"] = backend_auth
                    health["businessRoleMode"] = health.get("reviewDecisionAuthenticationMode", "unavailable")
                    content = json.dumps(health, ensure_ascii=False, separators=(",", ":")).encode()
                except (UnicodeDecodeError, ValueError):
                    return self.respond(502, b'{"error":"Unexpected demo health response"}')
            return self.respond(response.status, content,
                                "application/json; charset=utf-8" if local_path == "/api/health" and
                                response.status == 200 else response.getheader("Content-Type", "application/octet-stream"))
        except (OSError, TimeoutError):
            return self.respond(503, b'{"error":"Demo unavailable"}')
        finally:
            upstream.close()

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = handle_request


def make_server(port: int | None = None, backend_port: int | None = None) -> ThreadingHTTPServer:
    listener = ThreadingHTTPServer((LISTEN_HOST, port if port is not None else
                                    _port("OE02_ENTRY_PORT", DEFAULT_LISTEN_PORT)), Handler)
    try:
        listener.access = TailnetViewPolicy()
        listener.backend_port = backend_port if backend_port is not None else _port(
            "OE02_BACKEND_PORT", DEFAULT_BACKEND_PORT)
    except Exception:
        listener.server_close()
        raise
    return listener


def main() -> None:
    listener = make_server()
    try:
        print(f"OE-02 owner entry: {LISTEN_HOST}:{listener.server_port}", flush=True)
        listener.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        listener.server_close()


if __name__ == "__main__":
    main()
