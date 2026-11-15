"""Local, durable AgentDefinition registry and admission preflight.

This module does not launch an Agent or authenticate its callers. A production
entrypoint must supply a verified operator and requester before calling it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

import yaml
from jsonschema import Draft202012Validator

from validate_agent import MAX_YAML_BYTES, SCHEMA_FILE, StrictSafeLoader


class RegistryError(ValueError):
    """Invalid definition, transition, or deployment request."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AgentRegistry:
    """A single-repository local trial store; caller identity is not verified."""

    def __init__(self, db_path: Path, *, repository_name: str, checkout: Path):
        self.db_path = Path(db_path)
        self.repository_name = repository_name
        self.checkout = Path(checkout).resolve()
        if not repository_name or "/" not in repository_name:
            raise RegistryError("repository_name must be an explicit owner/repository")
        try:
            root = self._git("rev-parse", "--show-toplevel").decode().strip()
        except RegistryError as exc:
            raise RegistryError("checkout is not a Git repository") from exc
        if Path(root).resolve() != self.checkout:
            raise RegistryError("checkout must be the trusted Git repository root")
        schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db(write=True) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS definitions (
                    agent_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    use_case_id TEXT,
                    family TEXT NOT NULL,
                    owner_ref TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    artifact_path TEXT NOT NULL,
                    artifact_oid TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('draft','tested','approved','retired')),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (agent_id, version)
                );
                CREATE TABLE IF NOT EXISTS bindings (
                    deployment_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    principal_ref TEXT NOT NULL,
                    allowed_models_json TEXT NOT NULL,
                    allowed_tools_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('planned','active','suspended','retired')),
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (agent_id, version) REFERENCES definitions(agent_id, version)
                );
                CREATE TABLE IF NOT EXISTS state_events (
                    event_id TEXT PRIMARY KEY,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    old_state TEXT NOT NULL,
                    new_state TEXT NOT NULL,
                    actor_ref TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admissions (
                    admission_id TEXT PRIMARY KEY,
                    deployment_id TEXT NOT NULL,
                    requester_ref TEXT NOT NULL,
                    model_route TEXT NOT NULL,
                    tools_json TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('allowed','denied')),
                    reason TEXT NOT NULL,
                    agent_id TEXT,
                    version TEXT,
                    manifest_digest TEXT,
                    revision TEXT,
                    artifact_oid TEXT,
                    created_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def _db(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _git(self, *args: str) -> bytes:
        result = subprocess.run(
            ["git", "-C", str(self.checkout), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
        if result.returncode:
            raise RegistryError("the pinned Git artifact could not be verified")
        return result.stdout

    def _artifact(self, revision: str, path: str) -> tuple[str, str]:
        self._git("cat-file", "-e", f"{revision}^{{commit}}")
        oid = self._git("rev-parse", f"{revision}:{path}").decode().strip()
        kind = self._git("cat-file", "-t", oid).decode().strip()
        if kind not in {"blob", "tree"}:
            raise RegistryError("the pinned artifact must be a file or directory")
        entries = self._git("ls-tree", "-r", "-z", revision, "--", path).split(b"\0")
        entries = [entry for entry in entries if entry]
        if not entries:
            raise RegistryError("the pinned artifact is empty")
        for entry in entries:
            mode_and_object, _, name = entry.partition(b"\t")
            mode, _, object_type = mode_and_object.partition(b" ")
            if mode not in {b"100644", b"100755"} or not object_type.startswith(b"blob "):
                raise RegistryError("symlinks and submodules are not permitted in an artifact")
            if name != path.encode() and not name.startswith((path + "/").encode()):
                raise RegistryError("artifact path does not match the pinned tree")
        return oid, kind

    def register(self, manifest_path: Path) -> dict:
        path = Path(manifest_path)
        try:
            if path.stat().st_size > MAX_YAML_BYTES:
                raise RegistryError("manifest exceeds the YAML size limit")
            document = yaml.load(path.read_text(encoding="utf-8"), Loader=StrictSafeLoader)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise RegistryError("manifest could not be parsed safely") from exc
        errors = list(self.validator.iter_errors(document))
        if errors:
            raise RegistryError("invalid AgentDefinition: " + errors[0].message)
        meta = document["metadata"]
        implementation = document["spec"]["implementation"]
        if implementation["repository"] != self.repository_name:
            raise RegistryError("manifest repository does not match the trusted checkout")
        oid, kind = self._artifact(implementation["revision"], implementation["path"])
        canonical = _json(document)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        key = (meta["agentId"], meta["version"])
        with self._db(write=True) as db:
            prior = db.execute(
                "SELECT manifest_digest, artifact_oid FROM definitions WHERE agent_id=? AND version=?", key
            ).fetchone()
            if prior:
                if (prior["manifest_digest"], prior["artifact_oid"]) != (digest, oid):
                    raise RegistryError("an Agent version is immutable after registration")
            else:
                db.execute(
                    """INSERT INTO definitions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (*key, meta.get("useCaseId"), implementation["family"], meta["ownerRef"],
                     implementation["repository"], implementation["revision"], implementation["path"],
                     oid, kind, digest, canonical, "draft", _now()),
                )
        return self.definition(*key)

    def definition(self, agent_id: str, version: str) -> dict:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM definitions WHERE agent_id=? AND version=?", (agent_id, version)
            ).fetchone()
            if row is None:
                raise RegistryError("Agent version is not registered")
            result = dict(row)
            result["manifest"] = json.loads(result.pop("manifest_json"))
            return result

    def list_definitions(self, *, use_case_id: str | None = None) -> list[dict]:
        """List catalog entries without mutable deployment settings."""
        query = ("SELECT agent_id, version, use_case_id, family, owner_ref, revision, "
                 "artifact_path, artifact_oid, manifest_digest, state FROM definitions")
        parameters: tuple[str, ...] = ()
        if use_case_id is not None:
            query += " WHERE use_case_id=?"
            parameters = (use_case_id,)
        query += " ORDER BY agent_id, version"
        with self._db() as db:
            return [dict(row) for row in db.execute(query, parameters)]

    def change_definition_state(
        self, agent_id: str, version: str, new_state: str, *, actor_ref: str, reason: str
    ) -> None:
        transitions = {"draft": {"tested", "retired"}, "tested": {"approved", "retired"},
                       "approved": {"retired"}, "retired": set()}
        if not actor_ref or not reason:
            raise RegistryError("actor_ref and reason are required")
        with self._db(write=True) as db:
            old = db.execute(
                "SELECT state FROM definitions WHERE agent_id=? AND version=?", (agent_id, version)
            ).fetchone()
            if not old or new_state not in transitions[old["state"]]:
                raise RegistryError("invalid AgentDefinition state transition")
            db.execute(
                "UPDATE definitions SET state=? WHERE agent_id=? AND version=?",
                (new_state, agent_id, version),
            )
            self._state_event(db, "definition", f"{agent_id}@{version}", old["state"],
                              new_state, actor_ref, reason)

    @staticmethod
    def _state_event(db: sqlite3.Connection, target_type: str, target_id: str,
                     old: str, new: str, actor: str, reason: str) -> None:
        db.execute("INSERT INTO state_events VALUES (?,?,?,?,?,?,?,?)",
                   (uuid4().hex, target_type, target_id, old, new, actor, reason, _now()))

    def create_binding(self, deployment_id: str, agent_id: str, version: str,
                       *, environment: str, adapter: str, principal_ref: str,
                       allowed_models: list[str], allowed_tools: list[str]) -> dict:
        if not all((deployment_id, environment, principal_ref)):
            raise RegistryError("deployment, environment, and principal are required")
        if len(set(allowed_models)) != len(allowed_models) or len(set(allowed_tools)) != len(allowed_tools):
            raise RegistryError("duplicate allowed capabilities")
        with self._db(write=True) as db:
            definition = db.execute(
                "SELECT * FROM definitions WHERE agent_id=? AND version=?", (agent_id, version)
            ).fetchone()
            if definition is None or definition["state"] != "approved":
                raise RegistryError("only an approved Agent version can be bound")
            request = json.loads(definition["manifest_json"])["spec"]["requestedCapabilities"]
            if adapter != definition["family"] or adapter not in {"goose", "maf"}:
                raise RegistryError("no trusted adapter exists for the Agent family")
            if not set(allowed_models) <= set(request["modelRoutes"]):
                raise RegistryError("model permission exceeds the Agent request")
            if not set(allowed_tools) <= set(request["tools"]):
                raise RegistryError("Tool permission exceeds the Agent request")
            try:
                db.execute(
                    "INSERT INTO bindings VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (deployment_id, agent_id, version, environment, adapter, principal_ref,
                     _json(sorted(allowed_models)), _json(sorted(allowed_tools)), "planned", _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise RegistryError("deployment ID already exists") from exc
        return self.binding(deployment_id)

    def binding(self, deployment_id: str) -> dict:
        with self._db() as db:
            row = db.execute("SELECT * FROM bindings WHERE deployment_id=?", (deployment_id,)).fetchone()
            if row is None:
                raise RegistryError("deployment is not registered")
            result = dict(row)
            result["allowed_models"] = json.loads(result.pop("allowed_models_json"))
            result["allowed_tools"] = json.loads(result.pop("allowed_tools_json"))
            return result

    def change_binding_state(self, deployment_id: str, new_state: str,
                             *, actor_ref: str, reason: str) -> None:
        transitions = {"planned": {"active", "retired"},
                       "active": {"suspended", "retired"},
                       "suspended": {"active", "retired"}, "retired": set()}
        if not actor_ref or not reason:
            raise RegistryError("actor_ref and reason are required")
        with self._db(write=True) as db:
            old = db.execute("SELECT state FROM bindings WHERE deployment_id=?",
                             (deployment_id,)).fetchone()
            if not old or new_state not in transitions[old["state"]]:
                raise RegistryError("invalid deployment state transition")
            db.execute("UPDATE bindings SET state=? WHERE deployment_id=?", (new_state, deployment_id))
            self._state_event(db, "deployment", deployment_id, old["state"],
                              new_state, actor_ref, reason)

    def admit_request(self, deployment_id: str, *, requester_ref: str,
                      model_route: str, tools: list[str]) -> dict:
        """Save a preflight decision; no Agent process or Tool is started here."""
        if not all((deployment_id, requester_ref, model_route)):
            raise RegistryError("deployment, requester, and model route are required")
        if len(set(tools)) != len(tools):
            raise RegistryError("duplicate Tool names")
        with self._db(write=True) as db:
            row = db.execute(
                """SELECT b.*, d.state AS definition_state, d.manifest_digest,
                          d.revision, d.artifact_oid
                   FROM bindings b JOIN definitions d
                     ON d.agent_id=b.agent_id AND d.version=b.version
                   WHERE b.deployment_id=?""", (deployment_id,)
            ).fetchone()
            reason = "allowed"
            if row is None:
                reason = "deployment_not_found"
            elif row["state"] != "active":
                reason = "deployment_not_active"
            elif row["definition_state"] != "approved":
                reason = "definition_not_approved"
            elif row["principal_ref"] != requester_ref:
                reason = "requester_not_assigned"
            elif model_route not in json.loads(row["allowed_models_json"]):
                reason = "model_not_allowed"
            elif not set(tools) <= set(json.loads(row["allowed_tools_json"])):
                reason = "tool_not_allowed"
            result = {
                "admission_id": uuid4().hex,
                "deployment_id": deployment_id,
                "requester_ref": requester_ref,
                "model_route": model_route,
                "tools": sorted(tools),
                "decision": "allowed" if reason == "allowed" else "denied",
                "reason": reason,
                "agent_id": row["agent_id"] if row else None,
                "version": row["version"] if row else None,
                "manifest_digest": row["manifest_digest"] if row else None,
                "revision": row["revision"] if row else None,
                "artifact_oid": row["artifact_oid"] if row else None,
                "created_at": _now(),
            }
            db.execute("INSERT INTO admissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (result["admission_id"], deployment_id, requester_ref, model_route,
                        _json(result["tools"]), result["decision"], reason,
                        result["agent_id"], result["version"], result["manifest_digest"],
                        result["revision"], result["artifact_oid"], result["created_at"]))
            return result

    def admissions(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM admissions ORDER BY rowid").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["tools"] = json.loads(item.pop("tools_json"))
                result.append(item)
            return result

    def state_events(self) -> list[dict]:
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM state_events ORDER BY rowid")]
