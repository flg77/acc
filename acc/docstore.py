"""Document store — governed RAG over the vector backend (proposal 024 P3).

The substrate behind the ``doc_ingest`` / ``doc_retrieve`` skills (granted
by the ``document_store`` role flag) and the ``@acc/rag-roles`` pack.

Two tables extend the standard set (v0.1.0 §7.2 + 024):

* ``documents``   — one row per ingested document (row store, no vector).
* ``doc_chunks``  — one row per chunk, 384-dim MiniLM embedding.

Governance: every chunk is stamped with the ingesting agent's
``collective_id``.  ``retrieve`` scopes search to the caller's collective —
on TurboVec the scope travels INTO the SIMD kernel as an id allowlist
(``search_filtered``; F8: selective scopes are faster, not slower); on
backends without kernel filtering the store over-fetches and post-filters,
which is correct but weaker (documented fallback).  An empty scope returns
nothing — denial, not "no filter".

Skill adapters are instantiated by the registry WITHOUT constructor args
(see :class:`acc.skills.skill_runtime.Skill`), so the agent registers one
process-wide :class:`DocumentStore` at boot via
:func:`set_active_document_store`; adapters resolve it with
:func:`active_document_store`.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

logger = logging.getLogger("acc.docstore")

# Chunking defaults — paragraph-respecting sliding window.  Sized for
# 384-dim MiniLM (which truncates long inputs anyway) and for prompt
# budgets: ~1200 chars ≈ 300 tokens per retrieved chunk.
_CHUNK_CHARS = 1200
_CHUNK_OVERLAP = 200
# Over-fetch factor for backends without kernel allowlist filtering.
_POST_FILTER_FETCH_FACTOR = 4

DOCUMENTS_TABLE = "documents"
CHUNKS_TABLE = "doc_chunks"


def chunk_text(text: str, *, chunk_chars: int = _CHUNK_CHARS,
               overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Deterministic chunker: split on blank lines, pack paragraphs into
    ~*chunk_chars* windows, fall back to a hard sliding window (with
    *overlap*) for single oversized paragraphs."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(para) > chunk_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            step = chunk_chars - overlap
            for start in range(0, len(para), step):
                piece = para[start:start + chunk_chars]
                chunks.append(piece)
                if start + chunk_chars >= len(para):
                    break
            continue
        if buf and len(buf) + len(para) + 2 > chunk_chars:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


class DocumentStore:
    """Backend-agnostic governed document store.

    Args:
        vector: A :class:`acc.backends.VectorBackend`.
        embed_fn: Async ``text -> 384-dim vector`` (the agent's
            ``llm.embed``).
        collective_id: Governance scope stamped on every ingested chunk
            and enforced on every retrieval.
    """

    def __init__(
        self,
        vector: Any,
        embed_fn: Callable[[str], Awaitable[list[float]]],
        collective_id: str,
    ) -> None:
        self._vector = vector
        self._embed = embed_fn
        self.collective_id = collective_id

    # -- ingest ---------------------------------------------------------

    async def ingest(
        self,
        *,
        title: str,
        text: str,
        source: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Chunk, embed, and store one document under this collective's
        scope.  Returns ``{doc_id, chunks, title}``."""
        doc_id = str(uuid.uuid4())
        now = time.time()
        pieces = chunk_text(text)
        chunk_rows: list[dict] = []
        for seq, piece in enumerate(pieces):
            embedding = await self._embed(piece[:2000])
            chunk_rows.append({
                "id": f"{doc_id}:{seq}",
                "doc_id": doc_id,
                "collective_id": self.collective_id,
                "seq": seq,
                "text": piece,
                # Citation fields denormalized onto the chunk so retrieval
                # needs no second lookup — works on the bare 3-method
                # backend protocol (LanceDB/Milvus), not just backends with
                # a record-read extension.
                "title": title,
                "source": source,
                "created_at": now,
                "embedding": embedding,
            })
        self._vector.insert(DOCUMENTS_TABLE, [{
            "id": doc_id,
            "collective_id": self.collective_id,
            "title": title,
            "source": source,
            "tags_json": json.dumps(tags or []),
            "chunk_count": len(chunk_rows),
            "created_at": now,
        }])
        if chunk_rows:
            self._vector.insert(CHUNKS_TABLE, chunk_rows)
        logger.info(
            "docstore: ingested %r (%d chunks) scope=%s",
            title, len(chunk_rows), self.collective_id,
        )
        return {"doc_id": doc_id, "chunks": len(chunk_rows), "title": title}

    # -- retrieve -------------------------------------------------------

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        doc_id: str | None = None,
    ) -> dict[str, Any]:
        """Top-*k* chunks for *query*, scoped to this collective (and
        optionally to one document).  Result rows carry citation fields
        (doc_id, seq, title, source)."""
        query_embedding = await self._embed(query[:2000])
        search_filtered = getattr(self._vector, "search_filtered", None)
        if callable(search_filtered):
            # Kernel-enforced scope (TurboVec F8) — the allowlist is
            # computed from the record store, never from model input,
            # and an empty scope is a denial.
            rows = search_filtered(
                CHUNKS_TABLE, query_embedding, top_k,
                allow_ids=self._scoped_chunk_ids(doc_id),
            )
        else:
            # Fallback: over-fetch + post-filter on the stored
            # collective_id.  Correct but the filter runs above the
            # index; documented limitation for LanceDB/Milvus until
            # they grow an equivalent kernel seam.
            fetched = self._vector.search(
                CHUNKS_TABLE, query_embedding,
                top_k * _POST_FILTER_FETCH_FACTOR,
            )
            rows = [
                r for r in fetched
                if r.get("collective_id") == self.collective_id
                and (doc_id is None or r.get("doc_id") == doc_id)
            ][:top_k]
        results = [{
            "doc_id": r.get("doc_id", ""),
            "seq": r.get("seq", 0),
            "text": r.get("text", ""),
            "title": r.get("title", ""),
            "source": r.get("source", ""),
        } for r in rows]
        logger.info(
            "docstore: retrieve %d/%d scope=%s%s",
            len(results), top_k, self.collective_id,
            f" doc={doc_id}" if doc_id else "",
        )
        return {"results": results, "scope": self.collective_id}

    # -- internals ------------------------------------------------------

    def _scoped_chunk_ids(self, doc_id: str | None) -> list[str]:
        """Chunk ids visible to this collective — computed from the record
        store, never from LLM-supplied input (the allowlist is governance
        data, not a model argument).  Only meaningful on backends with
        ``get_records`` (the kernel-filter path)."""
        get_records = getattr(self._vector, "get_records", None)
        if not callable(get_records):
            return []
        rows = get_records(
            CHUNKS_TABLE,
            field="collective_id", value=self.collective_id, limit=100_000,
        )
        if doc_id:
            rows = [r for r in rows if r.get("doc_id") == doc_id]
        return [str(r["id"]) for r in rows if "id" in r]


# ---------------------------------------------------------------------------
# Process-wide handle for the no-arg skill adapters
# ---------------------------------------------------------------------------

_ACTIVE: DocumentStore | None = None


def set_active_document_store(store: DocumentStore | None) -> None:
    """Register the process-wide store (the agent calls this at boot;
    tests register fakes; ``None`` clears)."""
    global _ACTIVE
    _ACTIVE = store


def active_document_store() -> DocumentStore:
    """Resolve the registered store or raise a clear error the skill
    registry surfaces as a SkillInvocationError."""
    if _ACTIVE is None:
        raise RuntimeError(
            "document store not initialised — the agent registers it at "
            "boot (requires a vector backend + an embedding-capable LLM "
            "backend); doc_ingest/doc_retrieve cannot run outside an "
            "agent process"
        )
    return _ACTIVE
