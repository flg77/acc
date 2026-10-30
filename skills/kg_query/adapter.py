"""kg_query — a ceiling-bounded SPARQL read of the collective's knowledge store (RDF-05).

The ceiling and requester come from :mod:`acc.call_context`, set by
``capability_dispatch`` from the admitted task; the store is opened read-only
(the arbiter is its only writer).
"""

from __future__ import annotations

import os
from typing import Any

from acc.call_context import current
from acc.skills import Skill

_DEFAULT_LIMIT = 50


class KgQuerySkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        from acc.kg import KGError, KnowledgeStore, QueryRefused  # noqa: PLC0415

        ctx = current()
        if not ctx.ceiling:
            return {"form": "", "error": "kg_query runs only inside a task (no ceiling to read at)"}
        cid = ctx.collective_id or os.environ.get("ACC_COLLECTIVE_ID", "")
        limit = max(1, min(int(args.get("limit") or _DEFAULT_LIMIT), 200))
        try:
            store = KnowledgeStore(cid, read_only=True)
            result = store.query(str(args["sparql"]), reader_ceiling=ctx.ceiling, reader=ctx.requester)
        except QueryRefused as exc:
            return {"form": "", "error": f"refused: {exc}"}
        except KGError as exc:
            return {"form": "", "error": str(exc)}
        rows, triples = result.rows[:limit], [list(t) for t in result.triples[:limit]]
        return {
            "form": result.form, "variables": result.variables, "rows": rows,
            "boolean": result.boolean, "triples": triples,
            "truncated": len(result.rows) > limit or len(result.triples) > limit,
            "graphs_read": len(result.graphs_read),
        }
