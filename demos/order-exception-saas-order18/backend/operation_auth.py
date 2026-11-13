"""Isolated OE-02 reviewer/manager boundary using existing OIDC and OpenFGA clients.

Only the two synthetic human write routes use this authorizer. The caller's
actorRole is never an authority. This does not authenticate real employees.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openfga_sdk.client.models import ClientCheckRequest
from openfga_sdk.sync import OpenFgaClient

from identity_registry import IdentityError
from inventory_auth import InventoryAuthorizer


class OperationAccessError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class OperationAuthorizer(InventoryAuthorizer):
    RELATIONS = {
        "contract_reviewer": "can_review_cost",
        "shipping_manager": "can_approve_shipping",
    }

    @classmethod
    def from_config(cls, path: Path) -> "OperationAuthorizer":
        config = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or set(config) != {
                "issuer", "backchannelUrl", "audience", "registry", "fgaUrl", "storeId", "modelId"}:
            raise ValueError("Authorization configuration fields are invalid")
        return cls(config)

    def authorize_operation(self, headers: Any, order_id: str,
                            role: str) -> dict[str, str]:
        if role not in self.RELATIONS or order_id != "SO-24018":
            raise OperationAccessError(403, "authorization_denied",
                                       "対象の合成受注を操作できません。")
        actor = self.authenticate(headers)
        allowed = self._check(actor, order_id, role)
        if not allowed:
            raise OperationAccessError(403, "authorization_denied",
                                       "この受注の操作権限がありません。")
        return actor

    def authenticate(self, headers: Any) -> dict[str, str]:
        bearer = headers.get_all("Authorization", [])
        if len(bearer) != 1:
            raise OperationAccessError(401, "authentication_required",
                                       "ログインした担当者のtokenが必要です。")
        try:
            actor = self.verifier.authenticate({"Authorization": bearer[0]})
        except IdentityError as error:
            status = error.status if error.status in (401, 403, 503) else 503
            code = "authentication_unavailable" if status == 503 else (
                "authorization_denied" if status == 403 else "authentication_required")
            raise OperationAccessError(status, code, "担当者の本人確認に失敗しました。") from None
        return actor

    def _check(self, actor: dict[str, str], order_id: str, role: str) -> bool:
        try:
            with OpenFgaClient(self.fga) as client:
                answer = client.check(ClientCheckRequest(
                    user=actor["fgaUser"], relation=self.RELATIONS[role],
                    object=f"order:{order_id}"),
                    {"consistency": "HIGHER_CONSISTENCY"})
            if type(answer.allowed) is not bool:
                raise ValueError("invalid OpenFGA response")
        except Exception:
            raise OperationAccessError(503, "authorization_unavailable",
                                       "受注の操作権限を確認できません。") from None
        return answer.allowed

    def identity(self, headers: Any) -> dict[str, Any]:
        actor = self.authenticate(headers)
        order_id = "SO-24018"
        roles = {role: self._check(actor, order_id, role) for role in self.RELATIONS}
        return {
            "subject": actor["subject"],
            "displayName": actor["virtualUserId"],
            "roles": [role for role, allowed in roles.items() if allowed],
            "canRespondSourceReview": roles["contract_reviewer"],
            "canApproveShipment": roles["shipping_manager"],
            "authenticationMode": "keycloak_oidc_openfga_local_trial",
            "dataMode": "synthetic_fixture",
        }
