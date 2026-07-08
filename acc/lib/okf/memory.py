"""Index an OKF bundle into the collective-scoped document store (OKF P2).

An OKF bundle is ACC's *curated* knowledge tier; the existing RAG document
store (``acc.docstore``) is the retrieval substrate.  :func:`index_bundle`
feeds every concept in a bundle through ``DocumentStore.ingest`` — one document
per concept — carrying the OKF front-matter forward as tags.

Crucially, each concept's ``type`` is stamped as an ``okf-type:<Type>`` tag (and
its bundle path as ``okf-path:<rel_path>``).  That metadata is what the P3
retrieval-boundary filter (proposal §7 — per-domain / per-type / per-sensitivity
scoping) will key on; P2 deliberately ships the *shared corpus first* (operator
decision §14) and leaves the filtering to P3, but lays the tag groundwork now.

The store is duck-typed: anything exposing
``async ingest(*, title, text, source, tags)`` works, so tests can pass a stub
and real callers pass :func:`acc.docstore.active_document_store`.
"""

from __future__ import annotations

from typing import Any, Protocol

from acc.lib.okf.models import Bundle
from acc.lib.okf.parse import load_bundle


class _IngestStore(Protocol):
    async def ingest(self, *, title: str, text: str, source: str = ...,
                     tags: list[str] | None = ...) -> dict[str, Any]: ...


def concept_tags(concept, extra: list[str] | None = None) -> list[str]:
    """The tag set stamped on a concept's document: its own front-matter tags,
    the OKF ``type`` / ``path`` markers, and — when the front matter declares
    them — the ``okf-domain:`` / ``okf-sensitivity:`` markers the P3 retrieval
    boundary (``acc.docstore.RetrievalBoundary``) filters on (+ any *extra*)."""
    tags = [str(t) for t in (concept.frontmatter.get("tags") or [])]
    tags.append(f"okf-type:{concept.type or 'untyped'}")
    tags.append(f"okf-path:{concept.rel_path}")
    domain = str(concept.frontmatter.get("domain", "") or "").strip()
    if domain:
        tags.append(f"okf-domain:{domain}")
    sensitivity = str(concept.frontmatter.get("sensitivity", "") or "").strip()
    if sensitivity:
        tags.append(f"okf-sensitivity:{sensitivity}")
    tags.extend(extra or [])
    return tags


async def index_bundle(store: _IngestStore, bundle: Bundle, *,
                       source_prefix: str = "okf",
                       extra_tags: list[str] | None = None,
                       skip_nonconformant: bool = False) -> dict[str, Any]:
    """Ingest every concept in *bundle* into *store* (one document each).

    Returns ``{"indexed": N, "skipped": M, "documents": [...]}``.  With
    ``skip_nonconformant`` set, concepts that fail OKF rules 1/2 (no parseable
    front matter / no ``type``) are skipped rather than indexed.
    """
    documents: list[dict[str, Any]] = []
    skipped = 0
    for c in bundle.concepts:
        if skip_nonconformant and not c.is_conformant:
            skipped += 1
            continue
        text = (c.body or "").strip() or (c.title or c.rel_path)
        res = await store.ingest(
            title=c.title or c.rel_path,
            text=text,
            source=f"{source_prefix}:{c.rel_path}",
            tags=concept_tags(c, extra_tags),
        )
        documents.append({
            "rel_path": c.rel_path,
            "type": c.type,
            "doc_id": (res or {}).get("doc_id", ""),
            "chunks": (res or {}).get("chunks", 0),
        })
    return {"indexed": len(documents), "skipped": skipped, "documents": documents}


async def index_bundle_path(store: _IngestStore, root, **kwargs) -> dict[str, Any]:
    """Convenience: :func:`load_bundle` *root* then :func:`index_bundle`."""
    return await index_bundle(store, load_bundle(root), **kwargs)
