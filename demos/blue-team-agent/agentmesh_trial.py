"""AgentMesh 4.1.0 の登録・失効 API を使うローカル実証。

Run with the repository's pinned SDK Python, for example::

    .artifacts/rq03-baseline-20261101/venv/bin/python \
        demos/blue-team-agent/agentmesh_trial.py

The registry is in memory. Credential fields below are explicitly unverified
fixtures; this trial does not issue an SVID, authenticate Goose, persist the
registry, or authorize a real Goose process, Gateway request, or Tool call.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from agentmesh.services.registry.agent_registry import AgentRegistry, AgentRegistryEntry


AGENT_DID = "did:mesh:goose-blue-team-local-poc"
AGENT_VERSION = "0.1.0"
AGENT_OWNER = "blue-team-owner@example.invalid"
CAPABILITY = "security:propose-fix"


@dataclass(frozen=True)
class RegistrationCheck:
    metadata_match: bool
    reason: str


async def check_registration_metadata(
    registry: AgentRegistry,
    *,
    did: str,
    version: str,
    owner: str,
    capability: str,
) -> RegistrationCheck:
    """Compare a requested configuration with the local registry record.

    A matching record is not proof of identity or permission to start Goose.
    Production admission needs verified credentials, a mandatory controller
    check, and identity binding through Gateway and Tool execution.
    """
    entry = await registry.get(did)
    if entry is None:
        return RegistrationCheck(False, "unregistered")
    if entry.status != "active":
        return RegistrationCheck(False, f"status:{entry.status}")
    if entry.metadata.get("agent_version") != version:
        return RegistrationCheck(False, "version_mismatch")
    if entry.sponsor_email != owner:
        return RegistrationCheck(False, "owner_mismatch")
    if capability not in entry.capabilities:
        return RegistrationCheck(False, "capability_missing")
    return RegistrationCheck(True, "registry_record_matches")


async def run_trial() -> dict[str, object]:
    registry = AgentRegistry()
    entry = AgentRegistryEntry(
        did=AGENT_DID,
        name="Goose Blue Team local trial",
        sponsor_email=AGENT_OWNER,
        sponsor_verified=False,
        capabilities=[CAPABILITY],
        public_key_fingerprint="unverified-local-fixture",
        svid_serial_number="unverified-local-fixture",
        current_credential_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        metadata={"agent_version": AGENT_VERSION},
    )
    await registry.register(entry)
    readback = await registry.get(AGENT_DID)
    assert readback is not None
    assert readback.did == AGENT_DID
    assert readback.metadata.get("agent_version") == AGENT_VERSION
    assert readback.sponsor_email == AGENT_OWNER
    assert CAPABILITY in readback.capabilities

    correct = await check_registration_metadata(
        registry, did=AGENT_DID, version=AGENT_VERSION,
        owner=AGENT_OWNER, capability=CAPABILITY,
    )
    wrong_version = await check_registration_metadata(
        registry, did=AGENT_DID, version="0.2.0",
        owner=AGENT_OWNER, capability=CAPABILITY,
    )
    await registry.update_status(AGENT_DID, "revoked", "local trial revocation")
    revoked = await check_registration_metadata(
        registry, did=AGENT_DID, version=AGENT_VERSION,
        owner=AGENT_OWNER, capability=CAPABILITY,
    )
    assert correct == RegistrationCheck(True, "registry_record_matches")
    assert wrong_version == RegistrationCheck(False, "version_mismatch")
    assert revoked == RegistrationCheck(False, "status:revoked")

    return {
        "sdk": "agent-governance-toolkit-core==4.1.0",
        "registry": "AgentMesh AgentRegistry (local in-memory)",
        "registered": {
            "did": readback.did,
            "version": readback.metadata["agent_version"],
            "owner": readback.sponsor_email,
            "capabilities": readback.capabilities,
        },
        "registration_check_before_revocation": asdict(correct),
        "registration_check_wrong_version": asdict(wrong_version),
        "registration_check_after_revocation": asdict(revoked),
        "credential_verified": False,
        "runtime_start_authorized": False,
        "runtime_gateway_tool_enforced": False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_trial()), ensure_ascii=False, indent=2))
