"""Tests for acc/audit.py — AuditBroker, file backend, HMAC chain (ACC-12)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acc.audit import (
    AuditBroker,
    AuditRecord,
    FileAuditBackend,
    MultiAuditBackend,
    _compute_evidence_hash,
    _compute_chain_hash,
)


# ---------------------------------------------------------------------------
# AuditRecord
# ---------------------------------------------------------------------------


class TestAuditRecord:
    def test_default_timestamp_is_recent(self):
        rec = AuditRecord()
        assert abs(rec.timestamp_ms - int(time.time() * 1000)) < 5000

    def test_evidence_hash_excludes_hash_fields(self):
        rec = AuditRecord(agent_id="test-agent", task_id="task-01")
        h1 = _compute_evidence_hash(rec)
        rec.evidence_hash = "some-old-hash"
        h2 = _compute_evidence_hash(rec)
        assert h1 == h2  # hash field itself is excluded

    def test_evidence_hash_changes_with_content(self):
        r1 = AuditRecord(agent_id="agent-A", task_id="task-01")
        r2 = AuditRecord(agent_id="agent-B", task_id="task-01")
        assert _compute_evidence_hash(r1) != _compute_evidence_hash(r2)


# ---------------------------------------------------------------------------
# HMAC chain
# ---------------------------------------------------------------------------


class TestHMACChain:
    def test_chain_hash_deterministic(self):
        key = b"test-signing-key"
        prev = "0" * 64
        record_json = '{"test": "data"}'
        h1 = _compute_chain_hash(prev, record_json, key)
        h2 = _compute_chain_hash(prev, record_json, key)
        assert h1 == h2

    def test_chain_hash_differs_with_different_key(self):
        prev = "0" * 64
        record_json = '{"test": "data"}'
        h1 = _compute_chain_hash(prev, record_json, b"key-A")
        h2 = _compute_chain_hash(prev, record_json, b"key-B")
        assert h1 != h2

    def test_chain_links_records(self):
        """Second record's chain_hash depends on first's chain_hash."""
        key = b"chain-key"
        prev = "0" * 64
        json1 = '{"task_id": "t1"}'
        json2 = '{"task_id": "t2"}'
        h1 = _compute_chain_hash(prev, json1, key)
        h2_with_chain = _compute_chain_hash(h1, json2, key)
        h2_without_chain = _compute_chain_hash(prev, json2, key)
        assert h2_with_chain != h2_without_chain


# ---------------------------------------------------------------------------
# FileAuditBackend
# ---------------------------------------------------------------------------


class TestFileAuditBackend:
    @pytest.mark.asyncio
    async def test_writes_jsonl_line(self, tmp_path):
        backend = FileAuditBackend(base_path=str(tmp_path), retention_days=7)
        await backend.write('{"test": "record"}')
        files = list(tmp_path.glob("audit-*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text()
        assert '{"test": "record"}' in content

    @pytest.mark.asyncio
    async def test_multiple_writes_appended(self, tmp_path):
        backend = FileAuditBackend(base_path=str(tmp_path))
        await backend.write('{"task_id": "t1"}')
        await backend.write('{"task_id": "t2"}')
        files = list(tmp_path.glob("audit-*.jsonl"))
        content = files[0].read_text()
        assert '"t1"' in content
        assert '"t2"' in content
        assert content.count("\n") == 2

    @pytest.mark.asyncio
    async def test_directory_created_if_missing(self, tmp_path):
        nested = tmp_path / "audit" / "subdir"
        backend = FileAuditBackend(base_path=str(nested))
        await backend.write('{"created": true}')
        assert nested.exists()

    @pytest.mark.asyncio
    async def test_old_files_deleted_on_rotation(self, tmp_path):
        """Files older than retention_days are removed during rotation."""
        backend = FileAuditBackend(base_path=str(tmp_path), retention_days=0)
        # Create a stale file
        stale = tmp_path / "audit-2020-01-01.jsonl"
        stale.write_text('{"old": true}\n')
        # Touch it to make it old (mtime = past)
        past_time = time.time() - (2 * 86400)
        os.utime(stale, (past_time, past_time))

        await backend.write('{"new": true}')
        # Stale file should have been deleted
        assert not stale.exists()


# ---------------------------------------------------------------------------
# AuditBroker
# ---------------------------------------------------------------------------


class TestAuditBroker:
    @pytest.mark.asyncio
    async def test_record_writes_to_backend(self, tmp_path):
        backend = FileAuditBackend(base_path=str(tmp_path))
        broker = AuditBroker(backend=backend, agent_id="analyst-9c1d")
        rec = AuditRecord(
            agent_id="analyst-9c1d",
            collective_id="sol-01",
            task_id="task-001",
        )
        await broker.record(rec)
        files = list(tmp_path.glob("audit-*.jsonl"))
        assert len(files) == 1
        line = json.loads(files[0].read_text().strip())
        assert line["task_id"] == "task-001"

    @pytest.mark.asyncio
    async def test_record_stamps_evidence_hash(self, tmp_path):
        backend = FileAuditBackend(base_path=str(tmp_path))
        broker = AuditBroker(backend=backend, agent_id="analyst-9c1d")
        rec = AuditRecord(agent_id="analyst-9c1d", task_id="task-002")
        await broker.record(rec)
        assert len(rec.evidence_hash) == 64  # SHA-256 hex

    @pytest.mark.asyncio
    async def test_record_stamps_chain_hash(self, tmp_path):
        backend = FileAuditBackend(base_path=str(tmp_path))
        broker = AuditBroker(backend=backend, agent_id="analyst-9c1d")
        rec = AuditRecord(agent_id="analyst-9c1d", task_id="task-003")
        await broker.record(rec)
        assert len(rec.chain_hash) == 64

    @pytest.mark.asyncio
    async def test_chain_hashes_differ_per_record(self, tmp_path):
        backend = FileAuditBackend(base_path=str(tmp_path))
        broker = AuditBroker(backend=backend, agent_id="analyst-9c1d")
        r1 = AuditRecord(agent_id="analyst-9c1d", task_id="task-A")
        r2 = AuditRecord(agent_id="analyst-9c1d", task_id="task-B")
        await broker.record(r1)
        await broker.record(r2)
        assert r1.chain_hash != r2.chain_hash

    @pytest.mark.asyncio
    async def test_backend_failure_does_not_raise(self, tmp_path):
        """AuditBroker swallows backend errors — never blocks task processing."""
        backend = FileAuditBackend(base_path="/nonexistent_path_xyz/audit")
        broker = AuditBroker(backend=backend, agent_id="analyst-9c1d")
        rec = AuditRecord(agent_id="analyst-9c1d", task_id="task-X")
        # Should not raise even if file write fails
        await broker.record(rec)

    @pytest.mark.asyncio
    async def test_frameworks_injected_into_empty_record(self, tmp_path):
        backend = FileAuditBackend(base_path=str(tmp_path))
        broker = AuditBroker(
            backend=backend,
            agent_id="analyst-9c1d",
            frameworks=["EU_AI_ACT", "SOC2"],
        )
        rec = AuditRecord(agent_id="analyst-9c1d", task_id="task-001")
        await broker.record(rec)
        assert rec.compliance_frameworks == ["EU_AI_ACT", "SOC2"]

    @pytest.mark.asyncio
    async def test_agent_id_injected_when_empty(self, tmp_path):
        backend = FileAuditBackend(base_path=str(tmp_path))
        broker = AuditBroker(backend=backend, agent_id="analyst-9c1d")
        rec = AuditRecord(task_id="task-X")  # agent_id empty
        await broker.record(rec)
        assert rec.agent_id == "analyst-9c1d"


# ---------------------------------------------------------------------------
# MultiAuditBackend
# ---------------------------------------------------------------------------


class TestMultiAuditBackend:
    @pytest.mark.asyncio
    async def test_writes_to_all_backends(self, tmp_path):
        b1 = FileAuditBackend(base_path=str(tmp_path / "b1"))
        b2 = FileAuditBackend(base_path=str(tmp_path / "b2"))
        multi = MultiAuditBackend([b1, b2])
        await multi.write('{"event": "test"}')
        assert len(list((tmp_path / "b1").glob("*.jsonl"))) == 1
        assert len(list((tmp_path / "b2").glob("*.jsonl"))) == 1

    @pytest.mark.asyncio
    async def test_one_backend_failure_does_not_stop_others(self, tmp_path):
        """If one backend fails, the other still receives the record."""

        class FailingBackend:
            async def write(self, _):
                raise RuntimeError("simulated failure")

            async def flush(self):
                return 0

        b_good = FileAuditBackend(base_path=str(tmp_path))
        multi = MultiAuditBackend([FailingBackend(), b_good])
        await multi.write('{"resilience": true}')
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
