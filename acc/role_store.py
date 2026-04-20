"""ACC RoleStore — three-tier role definition persistence and hot-reload.

Load precedence at startup (first source that succeeds wins):
    1. File at ACC_ROLE_CONFIG_PATH (default: /app/acc-role.yaml)
    2. Redis key acc:{collective_id}:{agent_id}:role
    3. LanceDB role_definitions table (most recent row for agent_id)
    4. role_definition block in acc-config.yaml (in-process default)

At runtime, ROLE_UPDATE signals are applied after arbiter countersign validation.
A successful update notifies CognitiveCore via asyncio.Event (no restart required).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from acc.config import ACCConfig, RoleDefinitionConfig
from acc.signals import redis_role_key, redis_collective_key

logger = logging.getLogger("acc.role_store")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RoleUpdateRejectedError(Exception):
    """Raised when a ROLE_UPDATE payload fails validation or arbiter check."""


# ---------------------------------------------------------------------------
# RoleStore
# ---------------------------------------------------------------------------


class RoleStore:
    """Manages role definition lifecycle for one agent.

    Args:
        config: Validated ACC configuration.
        agent_id: The agent's unique identifier.
        redis_client: Optional Redis client (redis.asyncio or redis.Redis).
                      If None, Redis tier is skipped.
        vector: LanceDB backend instance (must have insert/query methods).
    """

    def __init__(
        self,
        config: ACCConfig,
        agent_id: str,
        redis_client: Optional[Any] = None,
        vector: Optional[Any] = None,
    ) -> None:
        self._config = config
        self._agent_id = agent_id
        self._collective_id = config.agent.collective_id
        self._redis = redis_client
        self._vector = vector
        self._current: RoleDefinitionConfig = RoleDefinitionConfig()
        self._role_updated = asyncio.Event()

    # ------------------------------------------------------------------
    # Startup load
    # ------------------------------------------------------------------

    def load_at_startup(self) -> RoleDefinitionConfig:
        """Load role definition from the highest-priority source available.

        Returns:
            The resolved :class:`RoleDefinitionConfig`.
        """
        # 1 — ConfigMap / file
        role = self._try_load_from_file()
        if role is not None:
            logger.info(
                "role_store: loaded role from file (agent_id=%s version=%s)",
                self._agent_id,
                role.version,
            )
            self._current = role
            self._append_audit("loaded", "", role.version, "source=file", "")
            return role

        # 2 — Redis
        role = self._try_load_from_redis()
        if role is not None:
            logger.info(
                "role_store: loaded role from Redis (agent_id=%s version=%s)",
                self._agent_id,
                role.version,
            )
            self._current = role
            self._append_audit("loaded", "", role.version, "source=redis", "")
            return role

        # 3 — LanceDB
        role = self._try_load_from_lancedb()
        if role is not None:
            logger.info(
                "role_store: loaded role from LanceDB (agent_id=%s version=%s)",
                self._agent_id,
                role.version,
            )
            self._current = role
            self._append_audit("loaded", "", role.version, "source=lancedb", "")
            return role

        # 4 — In-config default
        role = self._config.role_definition
        logger.info(
            "role_store: loaded role from config default (agent_id=%s version=%s)",
            self._agent_id,
            role.version,
        )
        self._current = role
        self._append_audit("loaded", "", role.version, "source=config_default", "")
        return role

    def _try_load_from_file(self) -> Optional[RoleDefinitionConfig]:
        path = os.environ.get("ACC_ROLE_CONFIG_PATH", "/app/acc-role.yaml")
        try:
            with open(path) as fh:
                data = yaml.safe_load(fh) or {}
            return RoleDefinitionConfig.model_validate(data)
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.warning("role_store: file load failed (%s): %s", path, exc)
            return None

    def _try_load_from_redis(self) -> Optional[RoleDefinitionConfig]:
        if self._redis is None:
            return None
        key = redis_role_key(self._collective_id, self._agent_id)
        try:
            raw = self._redis.get(key)
            if raw is None:
                return None
            data = json.loads(raw)
            return RoleDefinitionConfig.model_validate(data)
        except Exception as exc:
            logger.warning("role_store: Redis load failed (key=%s): %s", key, exc)
            return None

    def _try_load_from_lancedb(self) -> Optional[RoleDefinitionConfig]:
        if self._vector is None:
            return None
        try:
            tbl = self._vector._db.open_table("role_definitions")
            rows = (
                tbl.search()
                .where(f"agent_id = '{self._agent_id}'")
                .limit(1)
                .to_list()
            )
            if not rows:
                return None
            row = rows[0]
            return RoleDefinitionConfig(
                purpose=row.get("purpose", ""),
                persona=row.get("persona", "concise"),
                task_types=json.loads(row.get("task_types_json", "[]")),
                seed_context=row.get("seed_context", ""),
                allowed_actions=json.loads(row.get("allowed_actions_json", "[]")),
                category_b_overrides=json.loads(row.get("category_b_overrides_json", "{}")),
                version=row.get("version", "0.1.0"),
            )
        except Exception as exc:
            logger.warning("role_store: LanceDB load failed (agent_id=%s): %s", self._agent_id, exc)
            return None

    # ------------------------------------------------------------------
    # Runtime access
    # ------------------------------------------------------------------

    def get_current(self) -> RoleDefinitionConfig:
        """Return the active role definition.

        Tries Redis fast path; falls back to in-memory cache on cache miss.
        """
        redis_role = self._try_load_from_redis()
        if redis_role is not None:
            self._current = redis_role
        return self._current

    def get_history(self, n: int = 10) -> list[dict]:
        """Return the *n* most recent role audit entries.

        Args:
            n: Maximum number of entries to return.

        Returns:
            List of raw dicts from the ``role_audit`` LanceDB table,
            ordered by timestamp descending.
        """
        if self._vector is None:
            return []
        try:
            tbl = self._vector._db.open_table("role_audit")
            rows = (
                tbl.search()
                .where(f"agent_id = '{self._agent_id}'")
                .limit(n)
                .to_list()
            )
            rows.sort(key=lambda r: r.get("ts", 0), reverse=True)
            return rows
        except Exception as exc:
            logger.warning("role_store: get_history failed: %s", exc)
            return []

    @property
    def role_updated_event(self) -> asyncio.Event:
        """asyncio.Event signalled whenever the active role changes."""
        return self._role_updated

    # ------------------------------------------------------------------
    # Runtime update (NATS ROLE_UPDATE)
    # ------------------------------------------------------------------

    def apply_update(self, payload: dict) -> None:
        """Apply a ROLE_UPDATE payload after arbiter countersign validation.

        Args:
            payload: Decoded ROLE_UPDATE signal dict.

        Raises:
            RoleUpdateRejectedError: If validation fails (logged to role_audit).
        """
        approver_id: str = payload.get("approver_id", "")
        signature: str = payload.get("signature", "")
        new_role_data: dict = payload.get("role_definition", {})

        # Validate new role definition fields
        try:
            new_role = RoleDefinitionConfig.model_validate(new_role_data)
        except ValidationError as exc:
            self._append_audit(
                "rejected",
                self._current.version,
                new_role_data.get("version", ""),
                f"invalid_payload: {exc}",
                approver_id,
            )
            raise RoleUpdateRejectedError(f"Invalid role definition: {exc}") from exc

        # Validate arbiter identity
        if not approver_id:
            self._append_audit(
                "rejected",
                self._current.version,
                new_role.version,
                "missing_approver_id",
                "",
            )
            raise RoleUpdateRejectedError("ROLE_UPDATE rejected: approver_id is empty")

        if not signature:
            self._append_audit(
                "rejected",
                self._current.version,
                new_role.version,
                "missing_signature",
                approver_id,
            )
            raise RoleUpdateRejectedError("ROLE_UPDATE rejected: signature is empty")

        expected_arbiter = self._get_arbiter_id()
        if expected_arbiter and approver_id != expected_arbiter:
            self._append_audit(
                "rejected",
                self._current.version,
                new_role.version,
                f"approver_mismatch: got={approver_id} expected={expected_arbiter}",
                approver_id,
            )
            raise RoleUpdateRejectedError(
                f"ROLE_UPDATE rejected: approver {approver_id!r} is not the registered arbiter"
            )

        # --- Apply update ---
        old_version = self._current.version
        self._current = new_role

        # Write to Redis
        if self._redis is not None:
            try:
                key = redis_role_key(self._collective_id, self._agent_id)
                self._redis.set(key, new_role.model_dump_json())
            except Exception as exc:
                logger.warning("role_store: Redis write failed after update: %s", exc)

        # Persist to LanceDB
        if self._vector is not None:
            try:
                self._vector.insert("role_definitions", [self._role_to_lancedb_row(new_role)])
            except Exception as exc:
                logger.warning("role_store: LanceDB role_definitions write failed: %s", exc)

        self._append_audit(
            "updated",
            old_version,
            new_role.version,
            f"approver={approver_id}",
            approver_id,
        )

        logger.info(
            "role_store: role updated (agent_id=%s %s→%s approver=%s)",
            self._agent_id,
            old_version,
            new_role.version,
            approver_id,
        )

        # Signal CognitiveCore without restart
        self._role_updated.set()
        self._role_updated.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_arbiter_id(self) -> str:
        """Return the registered arbiter agent_id from Redis, or empty string."""
        if self._redis is None:
            return ""
        try:
            key = redis_collective_key(self._collective_id)
            raw = self._redis.get(key)
            if raw is None:
                return ""
            data = json.loads(raw)
            return data.get("arbiter_id", "")
        except Exception:
            return ""

    def _append_audit(
        self,
        event_type: str,
        old_version: str,
        new_version: str,
        diff_summary: str,
        approver_id: str,
    ) -> None:
        """Append a row to the role_audit LanceDB table."""
        if self._vector is None:
            return
        try:
            self._vector.insert("role_audit", [{
                "id": str(uuid.uuid4()),
                "agent_id": self._agent_id,
                "ts": time.time(),
                "event_type": event_type,
                "old_version": old_version,
                "new_version": new_version,
                "diff_summary": diff_summary,
                "approver_id": approver_id,
            }])
        except Exception as exc:
            logger.warning("role_store: audit write failed: %s", exc)

    def _role_to_lancedb_row(self, role: RoleDefinitionConfig) -> dict:
        """Convert a RoleDefinitionConfig to a role_definitions table row dict."""
        return {
            "id": str(uuid.uuid4()),
            "agent_id": self._agent_id,
            "collective_id": self._collective_id,
            "version": role.version,
            "purpose": role.purpose,
            "persona": role.persona,
            "seed_context": role.seed_context,
            "task_types_json": json.dumps(role.task_types),
            "allowed_actions_json": json.dumps(role.allowed_actions),
            "category_b_overrides_json": json.dumps(role.category_b_overrides),
            "created_at": time.time(),
            "purpose_embedding": [0.0] * 384,  # seeded by CognitiveCore on first task
        }
