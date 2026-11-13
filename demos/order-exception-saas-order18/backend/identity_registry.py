"""Registered virtual users and verified OIDC access-token authentication.

The registry contains identity mappings only. Passwords, tokens, email addresses,
and client-supplied user IDs are never used to select the authenticated actor.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class IdentityError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _issuer_url(value: str) -> str:
    parsed = urlsplit(value)
    if (not parsed.netloc or parsed.username or parsed.password or parsed.query
            or parsed.fragment or value.endswith("/")):
        raise IdentityError(503, "OIDC configuration is invalid")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise IdentityError(503, "OIDC requires HTTPS or a loopback issuer")
    return value


class IdentityRegistry:
    """SQLite mapping with a stable issuer/subject and private local storage."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().absolute()
        if self.path.is_symlink():
            raise IdentityError(503, "Identity registry path is invalid")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Do not change a shared parent's permissions; only the database is ours.
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS virtual_users (
                issuer TEXT NOT NULL,
                subject TEXT NOT NULL,
                user_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                virtual_user_id TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL CHECK(active IN (0, 1)),
                PRIMARY KEY(issuer, subject),
                UNIQUE(tenant_id, user_id)
            )""")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            with connection:
                yield connection
        finally:
            connection.close()

    def register(self, issuer: str, subject: str, user_id: str, tenant_id: str,
                 virtual_user_id: str, active: bool = True) -> dict[str, str]:
        _issuer_url(issuer)
        if not isinstance(subject, str) or not subject or len(subject) > 512:
            raise IdentityError(400, "Identity subject is invalid")
        if any(not isinstance(value, str) or not _ID.fullmatch(value)
               for value in (user_id, tenant_id, virtual_user_id)):
            raise IdentityError(400, "Identity identifier is invalid")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM virtual_users WHERE issuer=? AND subject=?", (issuer, subject)
            ).fetchone()
            if existing and any(existing[key] != value for key, value in (
                ("user_id", user_id), ("tenant_id", tenant_id), ("virtual_user_id", virtual_user_id)
            )):
                raise IdentityError(409, "Registered identity cannot be reassigned")
            try:
                connection.execute("""INSERT INTO virtual_users VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(issuer, subject) DO UPDATE SET active=excluded.active""",
                    (issuer, subject, user_id, tenant_id, virtual_user_id, int(active)))
            except sqlite3.IntegrityError:
                raise IdentityError(409, "Identity identifier is already registered") from None
        return self.resolve(issuer, subject) if active else self._mapping(issuer, subject)

    @staticmethod
    def _principal(row: Mapping[str, Any]) -> dict[str, str]:
        return {"issuer": row["issuer"], "subject": row["subject"],
                "userId": row["user_id"], "tenantId": row["tenant_id"],
                "fgaUser": f"user:{row['tenant_id']}/{row['user_id']}",
                "virtualUserId": row["virtual_user_id"]}

    def _mapping(self, issuer: str, subject: str) -> dict[str, str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM virtual_users WHERE issuer=? AND subject=?", (issuer, subject)
            ).fetchone()
        if row is None:
            raise IdentityError(403, "Identity is not registered or active")
        return self._principal(row)

    def resolve(self, issuer: str, subject: str) -> dict[str, str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM virtual_users WHERE issuer=? AND subject=? AND active=1",
                (issuer, subject)).fetchone()
        if row is None:
            raise IdentityError(403, "Identity is not registered or active")
        return self._principal(row)

    def set_active(self, issuer: str, subject: str, active: bool) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE virtual_users SET active=? WHERE issuer=? AND subject=?",
                (int(active), issuer, subject))
        if cursor.rowcount != 1:
            raise IdentityError(404, "Identity is not registered")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_json(url: str) -> dict[str, Any]:
    try:
        with build_opener(ProxyHandler({}), _NoRedirect).open(
            Request(url, headers={"Accept": "application/json"}), timeout=5
        ) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("oversized document")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("invalid document")
        return value
    except Exception:
        raise IdentityError(503, "OIDC verification service is unavailable") from None


class OIDCAuthenticator:
    """Fixed issuer/audience verification; never follows a token-supplied URL."""

    def __init__(self, issuer: str, audience: str, registry: IdentityRegistry,
                 *, read_json=None):
        self.issuer = _issuer_url(issuer)
        if not audience or len(audience) > 512:
            raise IdentityError(503, "OIDC configuration is invalid")
        self.audience = audience
        self.registry = registry
        self._read_json = read_json or _read_json
        self._keys: dict[str, Any] = {}
        self._refreshed_at = 0.0
        self._lock = threading.Lock()

    def _refresh_keys(self) -> None:
        import jwt
        configuration = self._read_json(self.issuer + "/.well-known/openid-configuration")
        if configuration.get("issuer") != self.issuer:
            raise IdentityError(503, "OIDC discovery issuer does not match configuration")
        uri = configuration.get("jwks_uri")
        if not isinstance(uri, str) or urlsplit(uri)[:2] != urlsplit(self.issuer)[:2]:
            raise IdentityError(503, "OIDC discovery key endpoint is invalid")
        keys = self._read_json(uri).get("keys")
        if not isinstance(keys, list):
            raise IdentityError(503, "OIDC verification keys are invalid")
        selected = {}
        for key in keys:
            if (isinstance(key, dict) and key.get("kty") == "RSA"
                    and key.get("use", "sig") == "sig" and key.get("alg", "RS256") == "RS256"
                    and isinstance(key.get("kid"), str)):
                try:
                    selected[key["kid"]] = jwt.PyJWK.from_dict(key, algorithm="RS256").key
                except Exception:
                    raise IdentityError(503, "OIDC verification keys are invalid") from None
        if not selected:
            raise IdentityError(503, "OIDC verification keys are invalid")
        self._keys = selected
        self._refreshed_at = time.monotonic()

    def authenticate(self, headers: Mapping[str, str]) -> dict[str, str]:
        try:
            import jwt
        except ImportError:
            raise IdentityError(503, "OIDC verification dependency is unavailable") from None
        authorizations = [value for key, value in headers.items()
                          if key.lower() == "authorization"]
        if len(authorizations) != 1:
            raise IdentityError(401, "A single bearer access token is required")
        authorization = authorizations[0]
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            raise IdentityError(401, "A bearer access token is required")
        token = authorization[7:]
        if not token or len(token) > 32768 or any(character.isspace() for character in token):
            raise IdentityError(401, "Access token is invalid")
        try:
            header = jwt.get_unverified_header(token)
            if (header.get("alg") != "RS256" or not isinstance(header.get("kid"), str)
                    or "crit" in header):
                raise ValueError("unsupported token")
        except Exception:
            raise IdentityError(401, "Access token is invalid") from None
        with self._lock:
            if time.monotonic() - self._refreshed_at > 60 or header["kid"] not in self._keys:
                self._refresh_keys()
            key = self._keys.get(header["kid"])
        if key is None:
            raise IdentityError(401, "Access token is invalid")
        try:
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.audience,
                                issuer=self.issuer,
                                options={"require": ["exp", "iat", "iss", "aud", "sub"]})
            # Keycloak access tokens have payload typ=Bearer. JWT access tokens
            # following RFC 9068 instead identify their header with typ=at+jwt.
            if claims.get("typ") != "Bearer" and header.get("typ") != "at+jwt":
                raise ValueError("not an access token")
            if not isinstance(claims.get("sub"), str) or not claims["sub"]:
                raise ValueError("missing subject")
        except Exception:
            raise IdentityError(401, "Access token is invalid") from None
        return self.registry.resolve(self.issuer, claims["sub"])


_AUTHENTICATORS: dict[tuple[str, str, str], OIDCAuthenticator] = {}
_AUTH_LOCK = threading.Lock()


def authentication_enabled() -> bool:
    return os.getenv("MAF_AUTH_MODE", "local") == "oidc"


def require_active_principal(principal: Mapping[str, str]) -> dict[str, str]:
    """Recheck saved actor on every worker/Tool operation, without a saved token."""
    path = os.getenv("MAF_IDENTITY_REGISTRY", "")
    issuer = os.getenv("MAF_OIDC_ISSUER", "")
    if not path or principal.get("issuer") != issuer:
        raise IdentityError(403, "Identity is not registered or active")
    resolved = IdentityRegistry(path).resolve(issuer, principal.get("subject", ""))
    if any(principal.get(key) != value for key, value in resolved.items()):
        raise IdentityError(403, "Registered identity does not match saved actor")
    return resolved


def authenticate(headers: Mapping[str, str]) -> dict[str, str]:
    """Server boundary used only when MAF_AUTH_MODE=oidc is explicitly enabled."""
    if not authentication_enabled():
        raise IdentityError(503, "OIDC authentication is not enabled")
    issuer = os.getenv("MAF_OIDC_ISSUER", "")
    audience = os.getenv("MAF_OIDC_AUDIENCE", "")
    path = os.getenv("MAF_IDENTITY_REGISTRY", "")
    if not issuer or not audience or not path:
        raise IdentityError(503, "OIDC configuration is incomplete")
    configuration = (issuer, audience, path)
    with _AUTH_LOCK:
        verifier = _AUTHENTICATORS.get(configuration)
        if verifier is None:
            verifier = OIDCAuthenticator(issuer, audience, IdentityRegistry(path))
            _AUTHENTICATORS[configuration] = verifier
    return verifier.authenticate(headers)
