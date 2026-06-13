"""doc_ingest — governed RAG ingestion (proposal 024 P3).

Chunks, embeds, and stores a document in the collective-scoped document
store.  The store is registered process-wide by the agent at boot (it
needs a vector backend + an embedding-capable LLM backend), so this
adapter resolves it via :func:`acc.docstore.active_document_store` rather
than taking constructor args (the registry instantiates skills argless).
"""

from __future__ import annotations

from typing import Any

from acc.docstore import active_document_store
from acc.skills import Skill


class DocIngestSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            store = active_document_store()
        except RuntimeError as exc:
            raise ValueError(f"doc_ingest unavailable: {exc}") from exc
        return await store.ingest(
            title=args["title"],
            text=args["text"],
            source=args.get("source", ""),
            tags=args.get("tags"),
        )
