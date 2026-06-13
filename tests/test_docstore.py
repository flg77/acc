"""Governed RAG document store — proposal 024 P3.

Covers chunking, ingest/retrieve, collective-scope enforcement on BOTH
paths (TurboVec kernel allowlist + a post-filter fallback backend), the
two skill adapters, and the ``document_store`` role-flag grant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.docstore import (
    CHUNKS_TABLE,
    DocumentStore,
    active_document_store,
    chunk_text,
    set_active_document_store,
)

_DIM = 384


def _kw_vec(text: str) -> list[float]:
    """Deterministic keyword-bucket embedding so retrieval ordering is
    predictable without a real model."""
    t = text.lower()
    v = [0.0] * _DIM
    buckets = ["alpha", "beta", "gamma", "delta", "epsilon"]
    for i, kw in enumerate(buckets):
        if kw in t:
            v[i] = 1.0
    if not any(v):
        v[7] = 1.0
    return v


async def _embed(text: str) -> list[float]:
    return _kw_vec(text)


class _PostFilterBackend:
    """A minimal VectorBackend WITHOUT search_filtered/get_records — forces
    the docstore's over-fetch + post-filter fallback path (the LanceDB/
    Milvus shape).  Stores rows in memory; search returns full dicts by
    cosine, newest-insert-wins on duplicate ids."""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    def create_table_if_absent(self, table, schema=None) -> None:  # noqa: ANN001
        pass

    def insert(self, table: str, records: list[dict]) -> int:
        for r in records:
            self._rows[f"{table}:{r['id']}"] = (table, r)
        return len(records)

    def search(self, table: str, embedding: list[float], top_k: int) -> list[dict]:
        scored = []
        for _, (tbl, row) in self._rows.items():
            if tbl != table:
                continue
            emb = row.get("embedding")
            if not emb:
                continue
            scored.append((sum(a * b for a, b in zip(embedding, emb)), row))
        scored.sort(key=lambda p: p[0], reverse=True)
        return [r for _, r in scored[:top_k]]


@pytest.fixture
def turbovec_store(tmp_path: Path):
    pytest.importorskip("turbovec")
    from acc.backends.vector_turbovec import TurboVecBackend

    backend = TurboVecBackend(str(tmp_path / "tv"))
    return DocumentStore(backend, _embed, collective_id="sol-01")


@pytest.fixture
def postfilter_store():
    return DocumentStore(_PostFilterBackend(), _embed, collective_id="sol-01")


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


class TestChunking:
    def test_short_text_one_chunk(self):
        assert chunk_text("just a sentence") == ["just a sentence"]

    def test_paragraphs_packed(self):
        text = "\n\n".join(["para one", "para two", "para three"])
        chunks = chunk_text(text, chunk_chars=20, overlap=5)
        assert len(chunks) >= 2
        assert all(c.strip() for c in chunks)

    def test_oversized_paragraph_sliding_window(self):
        big = "x" * 5000
        chunks = chunk_text(big, chunk_chars=1000, overlap=200)
        assert len(chunks) > 1
        assert all(len(c) <= 1000 for c in chunks)


# ---------------------------------------------------------------------------
# Ingest + retrieve (parametrized over both backend shapes)
# ---------------------------------------------------------------------------


@pytest.fixture(params=["turbovec", "postfilter"])
def store(request, turbovec_store, postfilter_store):
    return turbovec_store if request.param == "turbovec" else postfilter_store


class TestIngestRetrieve:
    async def test_ingest_returns_doc_id_and_chunks(self, store):
        out = await store.ingest(title="Alpha doc", text="alpha alpha alpha")
        assert out["chunks"] >= 1
        assert out["doc_id"]
        assert out["title"] == "Alpha doc"

    async def test_retrieve_finds_relevant_chunk(self, store):
        await store.ingest(title="Alpha", text="alpha content here")
        await store.ingest(title="Beta", text="beta content here")
        res = await store.retrieve("alpha", top_k=1)
        assert res["results"]
        assert "alpha" in res["results"][0]["text"].lower()
        assert res["results"][0]["title"] == "Alpha"

    async def test_retrieve_carries_citation_fields(self, store):
        await store.ingest(title="Cited", text="gamma material", source="http://x")
        res = await store.retrieve("gamma", top_k=1)
        row = res["results"][0]
        assert row["title"] == "Cited"
        assert row["source"] == "http://x"
        assert "doc_id" in row and "seq" in row

    async def test_scope_in_result(self, store):
        res = await store.retrieve("anything", top_k=3)
        assert res["scope"] == "sol-01"


class TestCollectiveScoping:
    """The governance contract — a role cannot retrieve another
    collective's chunks (G3).  Verified on BOTH enforcement paths."""

    async def test_other_collective_chunks_invisible(self, turbovec_store, tmp_path):
        # sol-01 ingests; a sol-99 store over the SAME backend ingests too.
        await turbovec_store.ingest(title="Mine", text="delta secret mine")
        backend = turbovec_store._vector
        other = DocumentStore(backend, _embed, collective_id="sol-99")
        await other.ingest(title="Theirs", text="delta secret theirs")

        mine = await turbovec_store.retrieve("delta", top_k=10)
        titles = {r["title"] for r in mine["results"]}
        assert "Mine" in titles
        assert "Theirs" not in titles  # kernel allowlist excluded it

    async def test_other_collective_invisible_postfilter(self, postfilter_store):
        await postfilter_store.ingest(title="Mine", text="delta secret mine")
        backend = postfilter_store._vector
        other = DocumentStore(backend, _embed, collective_id="sol-99")
        await other.ingest(title="Theirs", text="delta secret theirs")

        mine = await postfilter_store.retrieve("delta", top_k=10)
        titles = {r["title"] for r in mine["results"]}
        assert "Mine" in titles
        assert "Theirs" not in titles  # post-filter on collective_id excluded it

    async def test_doc_id_filter_restricts(self, turbovec_store):
        a = await turbovec_store.ingest(title="DocA", text="epsilon one")
        await turbovec_store.ingest(title="DocB", text="epsilon two")
        res = await turbovec_store.retrieve("epsilon", top_k=10, doc_id=a["doc_id"])
        assert {r["title"] for r in res["results"]} == {"DocA"}


# ---------------------------------------------------------------------------
# Skill adapters + role-flag grant
# ---------------------------------------------------------------------------


class TestDocSkills:
    _SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills"

    @pytest.fixture
    def registry(self):
        from acc.skills import SkillRegistry

        reg = SkillRegistry()
        reg.load_from(self._SKILLS_ROOT)
        return reg

    @pytest.fixture(autouse=True)
    def _clear_active(self):
        set_active_document_store(None)
        yield
        set_active_document_store(None)

    def test_both_skills_registered(self, registry):
        ids = registry.list_skill_ids()
        assert "doc_ingest" in ids and "doc_retrieve" in ids
        assert registry.manifest("doc_ingest").risk_level == "MEDIUM"
        assert registry.manifest("doc_retrieve").risk_level == "LOW"

    async def test_adapters_resolve_active_store(self, registry, postfilter_store):
        set_active_document_store(postfilter_store)
        # registry.invoke validates args + output against the manifests.
        out = await registry.invoke(
            "doc_ingest", {"title": "T", "text": "alpha body"}
        )
        assert out["chunks"] >= 1
        got = await registry.invoke("doc_retrieve", {"query": "alpha", "top_k": 1})
        assert got["results"][0]["title"] == "T"

    async def test_adapter_clear_error_when_unset(self, registry):
        with pytest.raises(Exception, match="doc_retrieve unavailable"):
            await registry.invoke("doc_retrieve", {"query": "x"})

    def test_document_store_flag_grants_skills(self):
        from acc.config import RoleDefinitionConfig

        role = RoleDefinitionConfig(document_store=True)
        assert "doc_ingest" in role.allowed_skills
        assert "doc_retrieve" in role.allowed_skills
        assert "doc_ingest" in role.default_skills
        assert "doc_retrieve" in role.default_skills

    def test_flag_off_grants_nothing(self):
        from acc.config import RoleDefinitionConfig

        role = RoleDefinitionConfig()
        assert "doc_ingest" not in role.allowed_skills
        assert "doc_retrieve" not in role.allowed_skills
