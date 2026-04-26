"""ACC domain registry and rubric validation (ACC-11).

This module implements the **grandmother cell** domain model:

* :class:`DomainRegistry` — tracks the shared **domain centroid** (EMA of all
  GOOD-outcome output embeddings for a domain_id) and the registered rubric
  criteria for each domain.  Maintained by the arbiter; persisted in Redis.

* :class:`RubricValidator` — validates and hashes ``eval_rubric.yaml`` files,
  and validates that ``EVAL_OUTCOME.rubric_scores`` keys are all present in the
  registered criteria for the emitting agent's domain.

Biological analogy
------------------
The domain centroid is the **abstract invariant representation** shared by all
cells of the same type — the "concept" that every grandmother cell for a given
domain encodes.  An agent with high ``domain_drift_score`` is internally
consistent (low ``role_drift_score``) but has drifted away from what the domain
collectively considers good — analogous to a concept cell that still fires
reliably but has started recognising the wrong concept.

Usage::

    from acc.domain import DomainRegistry, RubricValidator

    # In the arbiter's EVAL_OUTCOME subscriber:
    registry = DomainRegistry(redis_client=redis, collective_id="sol-01")
    new_centroid = await registry.update_domain_centroid(
        "software_engineering", embedding, is_good_outcome=True
    )

    # Validate an EVAL_OUTCOME payload:
    valid, reason = registry.validate_eval_outcome(payload, "software_engineering")
    if not valid:
        raise GovernanceError(reason)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger("acc.domain")

# EMA decay factor — same as CognitiveCore._CENTROID_ALPHA
_CENTROID_ALPHA: float = 0.1


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return cosine similarity in [−1, 1] between vectors *a* and *b*."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


class DomainRegistry:
    """Tracks domain centroids and rubric schemas across agents.

    The registry is maintained by the arbiter and persisted in Redis.  Each
    domain has:

    * A **centroid vector** — the EMA of all GOOD-outcome output embeddings
      from agents in that domain.  Only updated on ``is_good_outcome=True``.
    * A **rubric schema** — the hash and criteria list from the domain's
      canonical ``eval_rubric.yaml``.

    Args:
        redis_client: An async Redis client (e.g. ``redis.asyncio.StrictRedis``).
            When ``None`` the registry operates in-memory only (no persistence).
        collective_id: The collective this registry belongs to.  Used as the
            Redis key namespace prefix.
    """

    def __init__(
        self,
        redis_client: Optional[Any] = None,
        collective_id: str = "sol-01",
    ) -> None:
        self._redis = redis_client
        self._cid = collective_id
        # In-memory caches (populated from Redis on first access)
        self._centroids: dict[str, list[float]] = {}
        self._rubrics: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Domain centroid
    # ------------------------------------------------------------------

    async def update_domain_centroid(
        self,
        domain_id: str,
        new_embedding: list[float],
        is_good_outcome: bool,
    ) -> list[float]:
        """Update the domain centroid using an EMA and return the new value.

        Only GOOD outcomes contribute to the domain centroid.  BAD / PARTIAL
        outcomes are ignored so that the centroid tracks the collective's
        standard of good work, not an average of all work.

        Args:
            domain_id: The domain to update (e.g. ``"software_engineering"``).
            new_embedding: The output embedding from a completed task.
            is_good_outcome: Only True when ``EVAL_OUTCOME.outcome == "GOOD"``.

        Returns:
            The updated centroid vector, or the unchanged vector when
            ``is_good_outcome`` is False.
        """
        if not is_good_outcome:
            return await self.get_domain_centroid(domain_id)

        if all(v == 0.0 for v in new_embedding):
            return await self.get_domain_centroid(domain_id)

        centroid = await self.get_domain_centroid(domain_id)

        if all(v == 0.0 for v in centroid):
            # No centroid yet — seed from the first good embedding
            new_centroid = list(new_embedding)
        else:
            # EMA update: new = (1 − α) × centroid + α × embedding
            new_centroid = [
                (1.0 - _CENTROID_ALPHA) * c + _CENTROID_ALPHA * e
                for c, e in zip(centroid, new_embedding)
            ]

        await self._save_centroid(domain_id, new_centroid)
        self._centroids[domain_id] = new_centroid
        logger.debug(
            "domain_registry: updated centroid for '%s' (alpha=%.2f)",
            domain_id,
            _CENTROID_ALPHA,
        )
        return new_centroid

    async def get_domain_centroid(self, domain_id: str) -> list[float]:
        """Return the current domain centroid vector, or a zero vector if unset.

        Attempts to load from Redis on cache miss.  Returns a zero vector when
        Redis is unavailable or the domain has never been updated.

        Args:
            domain_id: The domain identifier.

        Returns:
            List of floats; an empty list ``[]`` when the domain has no centroid.
        """
        if domain_id in self._centroids:
            return self._centroids[domain_id]

        if self._redis is not None:
            from acc.signals import redis_domain_centroid_key
            key = redis_domain_centroid_key(self._cid, domain_id)
            try:
                raw = await self._redis.get(key)
                if raw is not None:
                    centroid = json.loads(raw)
                    self._centroids[domain_id] = centroid
                    return centroid
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "domain_registry: Redis read failed for centroid '%s': %s",
                    domain_id, exc,
                )

        return []

    def compute_domain_drift(
        self, output_embedding: list[float], domain_centroid: list[float]
    ) -> float:
        """Return the cosine distance between *output_embedding* and *domain_centroid*.

        Returns:
            Float in [0.0, 1.0]; 0.0 = no drift, 1.0 = maximally drifted.
            Returns 0.0 when either vector is empty or all-zeros.
        """
        if not output_embedding or not domain_centroid:
            return 0.0
        if all(v == 0.0 for v in domain_centroid):
            return 0.0
        similarity = _cosine_similarity(output_embedding, domain_centroid)
        return max(0.0, min(1.0, 1.0 - similarity))

    # ------------------------------------------------------------------
    # Rubric schema
    # ------------------------------------------------------------------

    async def register_rubric(
        self,
        domain_id: str,
        rubric_hash: str,
        criteria: list[str],
    ) -> None:
        """Register the rubric schema for a domain.

        Called by the arbiter when it issues a ``DOMAIN_DIFFERENTIATION`` signal
        or when a new role with a previously unknown ``domain_id`` registers.

        Args:
            domain_id: The domain identifier.
            rubric_hash: SHA-256 hex digest of the canonical rubric YAML.
            criteria: Ordered list of valid criterion names.
        """
        schema = {"hash": rubric_hash, "criteria": criteria}
        self._rubrics[domain_id] = schema

        if self._redis is not None:
            from acc.signals import redis_domain_rubric_key
            key = redis_domain_rubric_key(self._cid, domain_id)
            try:
                await self._redis.set(key, json.dumps(schema))
                logger.debug(
                    "domain_registry: registered rubric for '%s' (%d criteria)",
                    domain_id, len(criteria),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "domain_registry: Redis write failed for rubric '%s': %s",
                    domain_id, exc,
                )

    def get_rubric_criteria(self, domain_id: str) -> list[str]:
        """Return the registered criterion names for a domain.

        Returns an empty list when the domain has no registered rubric (which
        means no criteria validation is performed by Cat-A rule A-015).

        Args:
            domain_id: The domain identifier.

        Returns:
            List of criterion name strings.
        """
        schema = self._rubrics.get(domain_id, {})
        return schema.get("criteria", [])

    def validate_eval_outcome(
        self,
        eval_payload: dict[str, Any],
        domain_id: str,
    ) -> tuple[bool, str]:
        """Validate that an EVAL_OUTCOME payload's rubric_scores only use registered criteria.

        This is the Python-side enforcement of Cat-A rule A-015.  The OPA gate
        enforces the same rule; this method allows the arbiter to reject outcomes
        without waiting for the OPA evaluation round-trip.

        Args:
            eval_payload: The ``EVAL_OUTCOME`` signal payload dict.
            domain_id: The emitting agent's ``domain_id``.

        Returns:
            ``(True, "")`` when valid.
            ``(False, reason)`` when an unknown criterion is found.
        """
        registered = self.get_rubric_criteria(domain_id)
        if not registered:
            # No rubric registered for domain — cannot validate; allow through
            return True, ""

        rubric_scores: dict[str, Any] = eval_payload.get("rubric_scores", {})
        for criterion in rubric_scores:
            if criterion not in registered:
                return (
                    False,
                    f"criterion '{criterion}' not in registered rubric for domain "
                    f"'{domain_id}' (registered: {registered})",
                )
        return True, ""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _save_centroid(self, domain_id: str, centroid: list[float]) -> None:
        if self._redis is None:
            return
        from acc.signals import redis_domain_centroid_key
        key = redis_domain_centroid_key(self._cid, domain_id)
        try:
            await self._redis.set(key, json.dumps(centroid))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "domain_registry: Redis write failed for centroid '%s': %s",
                domain_id, exc,
            )


class RubricValidator:
    """Validates and hashes ``eval_rubric.yaml`` files.

    Used by :class:`~acc.role_loader.RoleLoader` to compute
    ``RoleDefinitionConfig.eval_rubric_hash`` and by the arbiter to verify
    that a role's rubric matches the authoritative domain hash.

    Usage::

        validator = RubricValidator()
        rubric_data = validator.load_rubric(Path("roles/coding_agent/eval_rubric.yaml"))
        digest = validator.compute_hash(rubric_data)
        is_valid = validator.validate(
            {"correctness": 0.9, "test_coverage": 0.8},
            registered_criteria=["correctness", "test_coverage", "security"],
        )
    """

    def load_rubric(self, rubric_path: Path) -> dict[str, Any]:
        """Load and return the parsed rubric YAML as a dict.

        Args:
            rubric_path: Path to the ``eval_rubric.yaml`` file.

        Returns:
            Parsed YAML dict.  Empty dict ``{}`` when the file does not exist
            or cannot be parsed.
        """
        if not rubric_path.exists():
            return {}
        try:
            with rubric_path.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            return data or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("RubricValidator: failed to load %s: %s", rubric_path, exc)
            return {}

    def compute_hash(self, rubric_data: dict[str, Any]) -> str:
        """Compute the SHA-256 hash of a rubric data dict.

        The hash is computed over the **canonical** YAML serialisation
        (``yaml.dump`` with ``sort_keys=True``) so that semantically equivalent
        rubrics with different key orderings produce the same hash.

        Args:
            rubric_data: Parsed rubric dict (from :meth:`load_rubric`).

        Returns:
            64-character lowercase hex digest, or ``""`` for empty input.
        """
        if not rubric_data:
            return ""
        canonical = yaml.dump(rubric_data, sort_keys=True, default_flow_style=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def validate(
        self,
        rubric_scores: dict[str, Any],
        registered_criteria: list[str],
    ) -> bool:
        """Return True when all keys in *rubric_scores* are in *registered_criteria*.

        Args:
            rubric_scores: The ``rubric_scores`` dict from an EVAL_OUTCOME payload.
            registered_criteria: Criterion names accepted for the emitting domain.

        Returns:
            ``True`` when valid; ``False`` when any unknown criterion is found.
            Always returns ``True`` when *registered_criteria* is empty.
        """
        if not registered_criteria:
            return True
        return all(c in registered_criteria for c in rubric_scores)
