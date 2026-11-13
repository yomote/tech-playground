"""Isolated OE-02 warehouse-count boundary using existing OIDC verification and OpenFGA SDK.

The body role is never read here. The verified Keycloak subject is resolved
through the existing registry to the OpenFGA user ID. This is a local
synthetic trial, not the public deployment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from openfga_sdk.client import ClientConfiguration
from openfga_sdk.client.models import ClientCheckRequest
from openfga_sdk.sync import OpenFgaClient


from identity_registry import IdentityError, IdentityRegistry, OIDCAuthenticator, _read_json  # noqa: E402


class InventoryAccessError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _loopback_url(value: Any, *, issuer: bool = False) -> str:
    parsed = urlsplit(value) if isinstance(value, str) else None
    if (parsed is None or parsed.scheme != "http" or parsed.hostname not in
            ("127.0.0.1", "localhost", "::1") or not parsed.port or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            (not issuer and parsed.path.strip("/"))):
        raise ValueError("Local authorization requires a loopback URL")
    return value.rstrip("/")


class InventoryAuthorizer:
    def __init__(self, config: dict[str, str]):
        self.issuer = config["issuer"]
        expected = "https://omote-llm.tail47e89e.ts.net:12443/trial/auth/realms/tp-population-20261006"
        if self.issuer != expected:
            raise ValueError("Trial issuer must match the fixed Tailnet HTTPS origin")
        backchannel = _loopback_url(config["backchannelUrl"], issuer=True)
        if backchannel != "http://127.0.0.1:19580":
            raise ValueError("Trial Keycloak backchannel must be fixed loopback")
        self.fga_url = _loopback_url(config["fgaUrl"])
        self.store_id, self.model_id = config["storeId"], config["modelId"]
        if not all(isinstance(item, str) and item for item in
                   (config["audience"], config["registry"], self.store_id, self.model_id)):
            raise ValueError("Authorization configuration is incomplete")
        def read_public_keycloak_json(url: str):
            # Keep the issuer and JWKS URL validation in OIDCAuthenticator.
            # Only the two fixed Keycloak documents use a local transport.
            if url not in (self.issuer + "/.well-known/openid-configuration",
                           self.issuer + "/protocol/openid-connect/certs"):
                raise IdentityError(503, "Unexpected Keycloak backchannel URL")
            # Serve strips /trial for the business entry, while the local
            # Keycloak itself uses /trial/auth to preserve browser cookie Path.
            path = urlsplit(url).path
            return _read_json(backchannel + path)

        self.verifier = OIDCAuthenticator(self.issuer, config["audience"],
                                          IdentityRegistry(config["registry"]),
                                          read_json=read_public_keycloak_json)
        self.fga = ClientConfiguration(api_url=self.fga_url, store_id=self.store_id,
                                       authorization_model_id=self.model_id)

    @classmethod
    def from_config(cls, path: Path) -> "InventoryAuthorizer":
        config = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or set(config) != {
                "issuer", "backchannelUrl", "audience", "registry", "fgaUrl", "storeId", "modelId"}:
            raise ValueError("Authorization configuration fields are invalid")
        return cls(config)

    def authorize(self, headers: Any, warehouse_id: Any) -> dict[str, str]:
        # HTTPMessage can contain duplicate headers; a plain mapping would hide them.
        bearer = headers.get_all("Authorization", [])
        if len(bearer) != 1:
            raise InventoryAccessError(401, "authentication_required", "ログインした担当者のtokenが必要です。")
        try:
            actor = self.verifier.authenticate({"Authorization": bearer[0]})
        except IdentityError as error:
            status = error.status if error.status in (401, 403, 503) else 503
            code = "authentication_unavailable" if status == 503 else "authentication_required"
            raise InventoryAccessError(status, code, "担当者の本人確認に失敗しました。") from None
        if warehouse_id not in ("warehouse-a", "warehouse-b"):
            raise InventoryAccessError(400, "validation_error", "対象倉庫を確認してください。")
        try:
            with OpenFgaClient(self.fga) as client:
                answer = client.check(ClientCheckRequest(
                    user=actor["fgaUser"], relation="can_count",
                    object=f"warehouse:{warehouse_id}"),
                    {"consistency": "HIGHER_CONSISTENCY"})
            if type(answer.allowed) is not bool:
                raise ValueError("invalid OpenFGA check response")
        except Exception:
            raise InventoryAccessError(503, "authorization_unavailable",
                                       "倉庫の操作権限を確認できません。") from None
        if not answer.allowed:
            raise InventoryAccessError(403, "authorization_denied",
                                       "この倉庫の在庫補正権限がありません。")
        return actor
