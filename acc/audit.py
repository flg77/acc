"""ACC Audit Broker — tamper-evident per-task audit records (ACC-12).

Produces a signed, HMAC-chained audit record for every ``process_task()``
invocation. Supports three backends:

backend   edge?   description
────────  ──────  ──────────────────────────────────────────────────────
file      ✅ Yes  Rotating JSONL files at ``{audit_file_path}/audit-YYYY-MM-DD.jsonl``
kafka     ❌ RHOAI AMQ Streams / Confluent Kafka topic ``acc-audit-{collective_id}``
multi     ✅/✅   Fan-out to both file and Kafka simultaneously

Tamper evidence:
- ``evidence_hash``: SHA-256 of the record JSON (excluding hash fields themselves)
- ``chain_hash``:    HMAC-SHA256(prev_chain_hash ‖ record_json, signing_key)
  — links records in an ordered chain; chain break → integrity alert

Usage::

    broker = AuditBroker.from_config(compliance_config, agent_id, collective_id)
    await broker.record(AuditRecord(...))
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("acc.audit")


# ---------------------------------------------------------------------------
# AuditRecord
# ---------------------------------------------------------------------------


@dataclass
class AuditRecord:
    """Single audit event for one ``process_task()`` invocation."""

    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    agent_id: str = ""
    collective_id: str = ""
    task_id: str = ""
    signal_type: str = "TASK_ASSIGN"

    # Guardrail outcomes
    guardrail_results: list[str] = field(default_factory=list)
    """OWASP violation codes triggered, e.g. ``['LLM01', 'LLM06']``."""

    cat_a_result: str = "PASS"
    """Cat-A evaluation outcome: ``'PASS'`` | ``'BLOCK:{rule_id}'`` | ``'OBSERVED:{reason}'``."""

    # Compliance metadata
    compliance_frameworks: list[str] = field(default_factory=list)
    control_ids: list[str] = field(default_factory=list)
    """Active compliance control IDs, e.g. ``['A-005', 'HIPAA-164.312b', 'SOC2-CC7']``."""

    outcome: str = "PROCESSED"
    """Task outcome: PROCESSED | BLOCKED | ESCALATED | OVERSIGHT_BYPASSED | OBSERVED."""

    risk_level: str = "MINIMAL"
    """EU AI Act risk classification: MINIMAL | LIMITED | HIGH | UNACCEPTABLE."""

    oversight_id: str = ""
    """Human oversight queue item ID (non-empty when task was queued for oversight)."""

    # Integrity fields (computed by AuditBroker, not caller)
    evidence_hash: str = ""
    chain_hash: str = ""


def _compute_evidence_hash(record: AuditRecord) -> str:
    """SHA-256 of record JSON excluding integrity fields."""
    data = asdict(record)
    data.pop("evidence_hash", None)
    data.pop("chain_hash", None)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _compute_chain_hash(prev_chain_hash: str, record_json: str, key: bytes) -> str:
    """HMAC-SHA256(prev_chain_hash ‖ record_json, key)."""
    message = (prev_chain_hash + record_json).encode()
    return hmac.new(key, message, digestmod=hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class FileAuditBackend:
    """Rotating JSONL file audit backend.

    Files are stored at ``{base_path}/audit-{YYYY-MM-DD}.jsonl`` and rotated
    at midnight UTC.  Old files beyond ``retention_days`` are deleted on rotation.
    Writes are atomic (temp file → ``os.replace``).
    """

    def __init__(
        self,
        base_path: str = "/app/data/audit",
        retention_days: int = 7,
    ) -> None:
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)
        self._retention = retention_days
        self._current_date: str = ""
        self._current_file: Optional[Path] = None

    def _rotate_if_needed(self) -> Path:
        """Return the current day's file path; rotate if the date changed."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            self._current_date = today
            self._current_file = self._base / f"audit-{today}.jsonl"
            self._delete_old_files()
        return self._current_file  # type: ignore[return-value]

    def _delete_old_files(self) -> None:
        """Remove audit files older than retention_days."""
        cutoff_ts = time.time() - (self._retention * 86400)
        for p in self._base.glob("audit-*.jsonl"):
            try:
                if p.stat().st_mtime < cutoff_ts:
                    p.unlink()
                    logger.info("audit: deleted old audit file %s", p.name)
            except OSError:
                pass

    async def write(self, line: str) -> None:
        """Append a JSONL line atomically."""
        target = self._rotate_if_needed()
        tmp = target.with_suffix(".tmp")
        try:
            # Append mode: read existing content, append new line
            existing = target.read_text() if target.exists() else ""
            tmp.write_text(existing + line + "\n")
            os.replace(tmp, target)
        except OSError as exc:
            logger.error("audit: file write failed: %s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    async def flush(self) -> int:
        return 0  # file backend is always flushed


class KafkaAuditBackend:
    """Kafka / AMQ Streams audit backend.

    Uses ``confluent-kafka`` (optional dependency).  When the broker is
    unreachable, records are queued in a Redis ring buffer
    (``acc:{collective_id}:audit:pending``) and flushed on reconnect.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        topic_prefix: str = "acc-audit",
        collective_id: str = "sol-01",
        redis_client: Any = None,
        max_queue: int = 10_000,
    ) -> None:
        self._topic = f"{topic_prefix}-{collective_id}"
        self._collective_id = collective_id
        self._redis = redis_client
        self._queue_key = f"acc:{collective_id}:audit:pending"
        self._max_queue = max_queue
        self._producer: Any = None
        self._available: bool = False

        try:
            from confluent_kafka import Producer  # noqa: WPS433
            self._producer = Producer({"bootstrap.servers": bootstrap_servers})
            self._available = True
            logger.info("audit: Kafka backend connected to %s", bootstrap_servers)
        except ImportError:
            logger.warning(
                "audit: confluent-kafka not installed; Kafka audit backend disabled. "
                "Install with: pip install confluent-kafka"
            )
        except Exception as exc:
            logger.error("audit: Kafka producer init failed: %s", exc)

    async def write(self, line: str) -> None:
        if self._available and self._producer is not None:
            try:
                self._producer.produce(
                    self._topic,
                    key=self._collective_id.encode(),
                    value=line.encode(),
                )
                self._producer.poll(0)
                return
            except Exception as exc:
                logger.warning("audit: Kafka produce failed: %s — queueing", exc)

        # Queue to Redis ring buffer
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.rpush(self._queue_key, line)
                pipe.ltrim(self._queue_key, -self._max_queue, -1)
                await pipe.execute()
            except Exception as exc:
                logger.error("audit: Redis queue failed: %s", exc)
        else:
            logger.warning("audit: no Redis available for offline Kafka queue")

    async def flush(self) -> int:
        """Flush queued records from Redis to Kafka. Returns count flushed."""
        if not self._available or self._redis is None or self._producer is None:
            return 0
        flushed = 0
        try:
            while True:
                line = await self._redis.lpop(self._queue_key)
                if line is None:
                    break
                self._producer.produce(
                    self._topic,
                    key=self._collective_id.encode(),
                    value=line if isinstance(line, bytes) else line.encode(),
                )
                flushed += 1
            if flushed:
                self._producer.flush(timeout=5)
                logger.info("audit: flushed %d queued records to Kafka", flushed)
        except Exception as exc:
            logger.error("audit: Kafka flush error: %s", exc)
        return flushed


class MultiAuditBackend:
    """Fan-out backend: writes to all configured sub-backends simultaneously."""

    def __init__(self, backends: list) -> None:
        self._backends = backends

    async def write(self, line: str) -> None:
        import asyncio
        await asyncio.gather(
            *[b.write(line) for b in self._backends],
            return_exceptions=True,
        )

    async def flush(self) -> int:
        import asyncio
        results = await asyncio.gather(
            *[b.flush() for b in self._backends],
            return_exceptions=True,
        )
        return sum(r for r in results if isinstance(r, int))


# ---------------------------------------------------------------------------
# AuditBroker
# ---------------------------------------------------------------------------


class AuditBroker:
    """High-level audit broker — stamps integrity fields and delegates to backend.

    Args:
        backend:        One of ``FileAuditBackend``, ``KafkaAuditBackend``,
                        or ``MultiAuditBackend``.
        signing_key:    HMAC signing key bytes.  When empty, a deterministic
                        key derived from ``agent_id`` is used.
        agent_id:       Owning agent identifier (used for key derivation).
        frameworks:     Active compliance framework names (added to every record).
    """

    def __init__(
        self,
        backend: Any,
        signing_key: bytes = b"",
        agent_id: str = "",
        frameworks: Optional[list[str]] = None,
    ) -> None:
        self._backend = backend
        self._agent_id = agent_id
        self._frameworks = frameworks or []
        self._prev_chain_hash: str = "0" * 64  # genesis block
        # Derive key from agent_id when no external key provided
        if not signing_key and agent_id:
            self._signing_key = hashlib.sha256(agent_id.encode()).digest()
        else:
            self._signing_key = signing_key or b"acc-audit-default-key"

    @classmethod
    def from_config(
        cls,
        config: Any,           # ComplianceConfig
        agent_id: str,
        collective_id: str,
        redis_client: Any = None,
    ) -> "AuditBroker":
        """Construct AuditBroker from ComplianceConfig."""
        # Signing key from env
        signing_key = b""
        if config.evidence_signing_key_env:
            raw = os.environ.get(config.evidence_signing_key_env, "")
            if raw:
                signing_key = raw.encode()

        # Build backend
        if config.audit_backend == "kafka":
            backend: Any = KafkaAuditBackend(
                bootstrap_servers=config.audit_kafka_bootstrap,
                topic_prefix=config.audit_kafka_topic,
                collective_id=collective_id,
                redis_client=redis_client,
            )
        elif config.audit_backend == "multi":
            backend = MultiAuditBackend([
                FileAuditBackend(config.audit_file_path, config.audit_retention_days),
                KafkaAuditBackend(
                    bootstrap_servers=config.audit_kafka_bootstrap,
                    topic_prefix=config.audit_kafka_topic,
                    collective_id=collective_id,
                    redis_client=redis_client,
                ),
            ])
        else:
            backend = FileAuditBackend(config.audit_file_path, config.audit_retention_days)

        return cls(
            backend=backend,
            signing_key=signing_key,
            agent_id=agent_id,
            frameworks=list(config.frameworks),
        )

    async def record(self, rec: AuditRecord) -> None:
        """Stamp integrity fields and write the record.

        Args:
            rec: ``AuditRecord`` with all fields populated except ``evidence_hash``
                 and ``chain_hash``, which are computed here.
        """
        if not rec.agent_id:
            rec.agent_id = self._agent_id
        if not rec.compliance_frameworks:
            rec.compliance_frameworks = list(self._frameworks)

        # Compute evidence hash
        rec.evidence_hash = _compute_evidence_hash(rec)

        # Compute chain hash
        record_json = json.dumps(asdict(rec), sort_keys=True, separators=(",", ":"))
        rec.chain_hash = _compute_chain_hash(
            self._prev_chain_hash, record_json, self._signing_key
        )
        self._prev_chain_hash = rec.chain_hash

        # Serialize final record
        final_json = json.dumps(asdict(rec), separators=(",", ":"))

        try:
            await self._backend.write(final_json)
        except Exception as exc:
            logger.error("audit: backend write failed: %s", exc)
            # Audit failure MUST NOT block task processing (REQ-COMP-018)

    async def flush(self) -> int:
        """Flush any queued records (Kafka offline buffer). Returns count."""
        return await self._backend.flush()
