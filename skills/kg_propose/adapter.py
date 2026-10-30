"""kg_propose — SHACL-checked facts for the requester's private graph (RDF-05).

Validates here so the model gets the shape violations back to correct; the
arbiter validates again before it writes (it does not trust this check).
"""

from __future__ import annotations

from typing import Any

from acc.call_context import current
from acc.skills import Skill


class KgProposeSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        from acc.kg_shapes import ShapesError, load_shapes, validate  # noqa: PLC0415
        from acc.kg_writes import submit, write_request  # noqa: PLC0415

        ctx = current()
        if not ctx.ceiling:
            return {"accepted": False, "error": "kg_propose runs only inside a task"}
        if not ctx.requester:
            return {"accepted": False,
                    "error": "this task has no requester, so there is no private graph to write"}
        try:
            shapes, _ = load_shapes()
            verdict = validate(str(args["turtle"]), shapes)
        except ShapesError as exc:
            return {"accepted": False, "error": str(exc)}
        if not verdict.conforms:
            return {"accepted": False, "triples": verdict.triples, "messages": verdict.messages}
        share = bool(args.get("share", False))
        payload = write_request(verdict.graph.to_ntriples(), ctx, share=share,
                                note=str(args.get("note") or ""))
        sent = await submit(payload)
        return {"accepted": True, "sent": sent, "triples": verdict.triples, "share": share,
                **({} if sent else {"error": "no agent bus to send on; nothing was written"})}
