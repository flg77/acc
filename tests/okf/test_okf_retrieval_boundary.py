"""OKF P3 — the per-domain / sensitivity retrieval boundary.

A *shared* corpus, filtered on retrieval by the retrieving role's boundary
(governance-sourced, never model input).  Untagged (non-OKF) documents are
treated as shared and always pass, so turning a boundary on never hides an
ordinary RAG doc.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.docstore import DocumentStore, RetrievalBoundary
from acc.lib.okf import Bundle, Concept, index_bundle


class _FakeVec:
    """Fallback-path backend (no search_filtered/get_records): stores rows,
    returns every chunk row so the docstore's own collective + boundary
    post-filters are what's under test (ranking is irrelevant here)."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, dict]] = []

    def insert(self, table: str, records: list[dict]) -> int:
        self.rows.extend((table, dict(r)) for r in records)
        return len(records)

    def search(self, table: str, embedding: list[float], top_k: int) -> list[dict]:
        return [r for (t, r) in self.rows if t == table]


async def _embed(_text: str) -> list[float]:
    return [0.1] * 384


def _store(boundary: RetrievalBoundary | None = None) -> DocumentStore:
    return DocumentStore(vector=_FakeVec(), embed_fn=_embed,
                         collective_id="c1", boundary=boundary)


# --- RetrievalBoundary.permits (unit) --------------------------------------

def test_permits_domain_gate():
    b = RetrievalBoundary(domains=frozenset({"finance"}))
    assert b.permits(["okf-domain:finance", "okf-type:Reference"]) is True
    assert b.permits(["okf-domain:sre"]) is False
    assert b.permits(["okf-type:Reference"]) is True   # no domain tag → shared


def test_permits_sensitivity_gate():
    b = RetrievalBoundary(max_sensitivity="internal")
    assert b.permits(["okf-sensitivity:public"]) is True
    assert b.permits(["okf-sensitivity:internal"]) is True
    assert b.permits(["okf-sensitivity:secret"]) is False
    assert b.permits([]) is True                        # untagged → shared


def test_inactive_boundary_permits_everything():
    b = RetrievalBoundary()
    assert b.is_active is False
    assert b.permits(["okf-domain:anything", "okf-sensitivity:secret"]) is True


# --- retrieve filtering (integration) --------------------------------------

@pytest.mark.asyncio
async def test_retrieve_filters_out_of_domain_and_over_clearance():
    store = _store(RetrievalBoundary(domains=frozenset({"finance"}),
                                     max_sensitivity="internal"))
    await store.ingest(title="fin", text="alpha finance", source="s1",
                       tags=["okf-domain:finance"])
    await store.ingest(title="sre", text="alpha sre", source="s2",
                       tags=["okf-domain:sre"])
    await store.ingest(title="secret-fin", text="alpha secret", source="s3",
                       tags=["okf-domain:finance", "okf-sensitivity:secret"])
    await store.ingest(title="shared", text="alpha shared", source="s4", tags=[])

    out = await store.retrieve("alpha", top_k=10)
    titles = {r["title"] for r in out["results"]}
    assert titles == {"fin", "shared"}   # sre out-of-domain; secret over clearance


@pytest.mark.asyncio
async def test_retrieve_without_boundary_is_unfiltered():
    store = _store(None)
    await store.ingest(title="fin", text="alpha", source="s1", tags=["okf-domain:finance"])
    await store.ingest(title="sre", text="alpha", source="s2", tags=["okf-domain:sre"])
    out = await store.retrieve("alpha", top_k=10)
    assert {r["title"] for r in out["results"]} == {"fin", "sre"}


@pytest.mark.asyncio
async def test_index_bundle_frontmatter_drives_the_boundary():
    # The P2→P3 seam: concept_tags stamps okf-domain from front matter, and the
    # boundary filters on it.
    bundle = Bundle(root=Path("."), concepts=[
        Concept("fin.md", {"type": "Reference", "domain": "finance", "title": "Fin"},
                "alpha finance body"),
        Concept("sre.md", {"type": "Runbook", "domain": "sre", "title": "Sre"},
                "alpha sre body"),
    ])
    store = _store(RetrievalBoundary(domains=frozenset({"finance"})))
    await index_bundle(store, bundle)
    titles = {r["title"] for r in (await store.retrieve("alpha", top_k=10))["results"]}
    assert "Fin" in titles and "Sre" not in titles
