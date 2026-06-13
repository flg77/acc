"""doc_retrieve — governed RAG retrieval (proposal 024 P3).

Read-only nearest-chunk retrieval from the collective-scoped document
store.  The collective scope is taken from the registered store (the
agent's own collective_id), never from skill args — a role cannot widen
its own visibility through this surface.  Resolves the process-wide store
the same way as ``doc_ingest``.
"""

from __future__ import annotations

from typing import Any

from acc.docstore import active_document_store
from acc.skills import Skill


class DocRetrieveSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            store = active_document_store()
        except RuntimeError as exc:
            raise ValueError(f"doc_retrieve unavailable: {exc}") from exc
        return await store.retrieve(
            args["query"],
            top_k=int(args.get("top_k", 5)),
            doc_id=args.get("doc_id"),
        )
