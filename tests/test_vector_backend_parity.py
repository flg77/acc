"""Backend-contract parity suite for the embedded vector backends.

Proposal 024 — the same assertions run against LanceDB AND TurboVec so a
turbovec version bump (exact-pin policy: pin and bump) or a backend code
change that breaks the :class:`acc.backends.VectorBackend` contract fails
loudly here.  TurboVec-specific guarantees (kernel allowlist filtering,
rebuild-from-SQLite, upsert semantics) live in their own class below and
skip cleanly when the optional ``turbovec`` extra is not installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.backends.vector_lancedb import LanceDBBackend

_DIM = 384


def _vec(hot: int, *components: tuple[int, float]) -> list[float]:
    """A deterministic 384-dim vector: 1.0 at *hot* plus optional extra
    (index, weight) components.  NOT normalized — backends must handle
    normalization at the seam (cosine semantics)."""
    v = [0.0] * _DIM
    v[hot] = 1.0
    for idx, w in components:
        v[idx] = w
    return v


def _episode(eid: str, embedding: list[float], payload: str = "{}") -> dict:
    return {
        "id": eid,
        "agent_id": "agent-1",
        "ts": 1718000000.0,
        "signal_type": "TASK_ASSIGN",
        "payload_json": payload,
        "embedding": embedding,
    }


def _memory_note(nid: str, embedding: list[float], summary: str) -> dict:
    return {
        "id": nid,
        "agent_id": "agent-1",
        "role_label": "coding_agent",
        "ts": 1718000000.0,
        "summary": summary,
        "source_count": 3,
        "confidence": 0.9,
        "embedding": embedding,
    }


def _role_definition(rid: str, purpose: str, purpose_embedding: list[float]) -> dict:
    return {
        "id": rid,
        "agent_id": "agent-1",
        "collective_id": "sol-01",
        "version": "0.1.0",
        "purpose": purpose,
        "persona": "concise",
        "seed_context": "",
        "task_types_json": "[]",
        "allowed_actions_json": "[]",
        "category_b_overrides_json": "{}",
        "created_at": 1718000000.0,
        "purpose_embedding": purpose_embedding,
    }


def _make_backend(kind: str, tmp_path: Path):
    if kind == "lancedb":
        return LanceDBBackend(str(tmp_path / "lance"))
    pytest.importorskip("turbovec")
    from acc.backends.vector_turbovec import TurboVecBackend

    return TurboVecBackend(str(tmp_path / "tv"))


@pytest.fixture(params=["lancedb", "turbovec"])
def backend(request, tmp_path: Path):
    return _make_backend(request.param, tmp_path)


class TestVectorBackendParity:
    """The shared contract: insert returns row count; search returns full
    record dicts ordered by cosine similarity descending."""

    def test_insert_returns_rowcount(self, backend):
        n = backend.insert("episodes", [
            _episode("e1", _vec(0)),
            _episode("e2", _vec(1)),
        ])
        assert n == 2

    def test_search_returns_nearest_full_record(self, backend):
        backend.insert("episodes", [
            _episode("e1", _vec(0), payload='{"task": "alpha"}'),
            _episode("e2", _vec(1), payload='{"task": "beta"}'),
            _episode("e3", _vec(2), payload='{"task": "gamma"}'),
        ])
        results = backend.search("episodes", _vec(1), top_k=1)
        assert len(results) == 1
        assert results[0]["id"] == "e2"
        assert results[0]["payload_json"] == '{"task": "beta"}'
        assert results[0]["agent_id"] == "agent-1"

    def test_topk_cosine_ordering(self, backend):
        # similarity to query e0: a=1.0 > b≈0.8 > c=0.0
        backend.insert("episodes", [
            _episode("far", _vec(1)),
            _episode("mid", _vec(0, (1, 0.75))),   # 0.8/0.6 after norm
            _episode("near", _vec(0)),
        ])
        results = backend.search("episodes", _vec(0), top_k=3)
        assert [r["id"] for r in results] == ["near", "mid", "far"]

    def test_scale_invariance_cosine_semantics(self, backend):
        """An un-normalized (scaled) stored vector must rank identically to
        its unit twin — the seam owns normalization (proposal 024 risk
        'wrong metric semantics')."""
        scaled = [x * 7.5 for x in _vec(3)]
        backend.insert("episodes", [
            _episode("scaled", scaled),
            _episode("other", _vec(4)),
        ])
        results = backend.search("episodes", _vec(3), top_k=2)
        assert results[0]["id"] == "scaled"

    def test_topk_caps_at_table_size(self, backend):
        backend.insert("episodes", [_episode("only", _vec(5))])
        results = backend.search("episodes", _vec(5), top_k=10)
        assert len(results) == 1

    def test_memory_notes_roundtrip(self, backend):
        backend.insert("memory_notes", [
            _memory_note("n1", _vec(10), "prefers small PRs"),
            _memory_note("n2", _vec(11), "flaky test in auth module"),
        ])
        results = backend.search("memory_notes", _vec(11), top_k=1)
        assert results[0]["id"] == "n2"
        assert results[0]["summary"] == "flaky test in auth module"

    def test_role_definitions_purpose_embedding_column(self, backend):
        """role_definitions searches over purpose_embedding, not embedding —
        the column every other table uses (UC3 semantic routing seed)."""
        backend.insert("role_definitions", [
            _role_definition("r1", "writes production code", _vec(20)),
            _role_definition("r2", "reviews security posture", _vec(21)),
        ])
        results = backend.search("role_definitions", _vec(21), top_k=1)
        assert results[0]["id"] == "r2"
        assert results[0]["purpose"] == "reviews security posture"


class TestTurboVecSpecifics:
    """Guarantees beyond the shared contract — TurboVec only."""

    @pytest.fixture()
    def tv(self, tmp_path: Path):
        pytest.importorskip("turbovec")
        from acc.backends.vector_turbovec import TurboVecBackend

        return TurboVecBackend(str(tmp_path / "tv"))

    def test_persistence_across_reopen(self, tmp_path: Path, tv):
        tv.insert("episodes", [_episode("e1", _vec(0))])
        tv.close()
        from acc.backends.vector_turbovec import TurboVecBackend

        tv2 = TurboVecBackend(str(tmp_path / "tv"))
        results = tv2.search("episodes", _vec(0), top_k=1)
        assert results and results[0]["id"] == "e1"
        tv2.close()

    def test_index_file_is_derived_data(self, tmp_path: Path, tv):
        """Deleting every .tvim file loses nothing: SQLite is the source of
        truth and the index rebuilds on the next start (F1: rebuild is
        cheap, no training pass)."""
        tv.insert("episodes", [_episode("e1", _vec(0)), _episode("e2", _vec(1))])
        tv.close()
        for f in (tmp_path / "tv").glob("*.tvim"):
            f.unlink()
        from acc.backends.vector_turbovec import TurboVecBackend

        tv2 = TurboVecBackend(str(tmp_path / "tv"))
        results = tv2.search("episodes", _vec(1), top_k=1)
        assert results and results[0]["id"] == "e2"
        tv2.close()

    def test_kernel_allowlist_filtering(self, tv):
        """search_filtered scopes results to the allowed ids (G3 governed
        retrieval) — and an empty scope is a denial, not 'no filter'."""
        tv.insert("episodes", [
            _episode("e1", _vec(0)),
            _episode("e2", _vec(0, (1, 0.2))),
            _episode("e3", _vec(1)),
        ])
        scoped = tv.search_filtered("episodes", _vec(0), 3, allow_ids=["e2", "e3"])
        assert [r["id"] for r in scoped] == ["e2", "e3"]  # e1 invisible
        assert tv.search_filtered("episodes", _vec(0), 3, allow_ids=[]) == []

    def test_zero_embedding_stored_not_indexed(self, tv):
        """The cognitive core writes zeroed placeholder embeddings when
        embedding fails; those rows must never poison similarity results."""
        tv.insert("episodes", [
            _episode("zero", [0.0] * _DIM),
            _episode("real", _vec(2)),
        ])
        results = tv.search("episodes", _vec(2), top_k=5)
        assert [r["id"] for r in results] == ["real"]

    def test_upsert_latest_wins(self, tv):
        tv.insert("memory_notes", [_memory_note("n1", _vec(0), "old summary")])
        tv.insert("memory_notes", [_memory_note("n1", _vec(0), "new summary")])
        results = tv.search("memory_notes", _vec(0), top_k=5)
        assert len(results) == 1
        assert results[0]["summary"] == "new summary"

    def test_row_store_only_table_inserts_and_searches_empty(self, tv):
        """role_audit has no vector column: inserts land in the record
        store, search returns [] instead of erroring."""
        n = tv.insert("role_audit", [{
            "id": "a1", "agent_id": "agent-1", "ts": 1718000000.0,
            "event_type": "loaded", "old_version": "", "new_version": "0.1.0",
            "diff_summary": "", "approver_id": "",
        }])
        assert n == 1
        assert tv.search("role_audit", _vec(0), top_k=5) == []

    def test_get_records_structured_read(self, tv):
        """The role_store load path (proposal 024): newest row first,
        filtered on a top-level JSON field, no vector involved."""
        tv.insert("role_definitions", [
            _role_definition("r1", "old purpose", _vec(0)),
        ])
        tv.insert("role_definitions", [
            _role_definition("r2", "new purpose", _vec(1)),
        ])
        rows = tv.get_records(
            "role_definitions", field="agent_id", value="agent-1", limit=1,
        )
        assert len(rows) == 1
        assert rows[0]["purpose"] == "new purpose"  # newest first
        assert tv.get_records(
            "role_definitions", field="agent_id", value="nobody", limit=5,
        ) == []

    def test_lazy_table_registration_for_custom_tables(self, tv):
        """Phase 3 doc tables (documents/doc_chunks) register lazily by
        convention: an ``embedding`` column, dim from the first record."""
        tv.insert("doc_chunks", [
            {"id": "c1", "text": "chapter one", "embedding": _vec(0)},
            {"id": "c2", "text": "chapter two", "embedding": _vec(1)},
        ])
        results = tv.search("doc_chunks", _vec(1), top_k=1)
        assert results[0]["id"] == "c2"
        assert results[0]["text"] == "chapter two"


class TestFactorySelection:
    """config-driven selection incl. the graceful LanceDB fallback."""

    def test_factory_builds_turbovec(self, tmp_path: Path):
        pytest.importorskip("turbovec")
        from acc.backends.vector_turbovec import TurboVecBackend
        from acc.config import ACCConfig, build_backends

        config = ACCConfig.model_validate({
            "vector_db": {
                "backend": "turbovec",
                "turbovec_path": str(tmp_path / "tv"),
            },
        })
        bundle = build_backends(config)
        assert isinstance(bundle.vector, TurboVecBackend)

    def test_rhoai_unset_backend_resolves_to_turbovec_store(self, tmp_path: Path):
        pytest.importorskip("turbovec")
        from acc.backends.vector_turbovec import TurboVecBackend
        from acc.config import ACCConfig, build_backends

        config = ACCConfig.model_validate({
            "deploy_mode": "rhoai",
            "vector_db": {"turbovec_path": str(tmp_path / "tv")},
            "llm": {"vllm_inference_url": "http://vllm:8000"},
        })
        assert config.vector_db.backend == "turbovec"
        bundle = build_backends(config)
        assert isinstance(bundle.vector, TurboVecBackend)
