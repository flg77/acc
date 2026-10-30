"""How a ``kg_propose`` write travels: agent -> arbiter -> store (RDF-05).

Operator decisions 2026-10-03:

* **Private graph, proposal to share.**  Accepted triples land in the
  requester's private graph (``private-<owner>-<ceiling>``), readable by that
  person alone; making them readable to others is a ``kg_share`` proposal a
  person approves.
* **One writer.**  The collective's store sits on a volume every agent mounts;
  agents open it read-only, and the arbiter alone writes.  An agent therefore
  never writes: it validates (a courtesy to the model), then *sends* the
  triples on ``acc.{cid}.kg.write``, signed with its bus key, and the arbiter
  verifies the sender, re-validates and writes.

What the arbiter can prove is the sending **agent** (PA-09 signature) and that
its role's signed definition carries ``knowledge_graph``.  The requester and
ceiling are what that agent says it was serving -- the same trust every
episode the agent attributes already rests on.  A compromised agent can write
into the private graph of a requester it serves; it cannot widen a ceiling the
task did not carry, nor write a shared graph without a person's approval.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Awaitable, Callable, Iterator, TypeVar

from acc.call_context import CallContext

logger = logging.getLogger("acc.kg_writes")

#: Set by the agent at boot: signs and publishes one KG_WRITE.  None outside
#: an agent (tests, the CLI), where ``kg_propose`` can only validate.
_SUBMITTER: Callable[[dict[str, Any]], Awaitable[bool]] | None = None


def register_submitter(fn: Callable[[dict[str, Any]], Awaitable[bool]] | None) -> None:
    global _SUBMITTER
    _SUBMITTER = fn


async def submit(payload: dict[str, Any]) -> bool:
    if _SUBMITTER is None:
        return False
    return await _SUBMITTER(payload)


def write_request(ntriples: str, ctx: CallContext, *, share: bool = False,
                  note: str = "") -> dict[str, Any]:
    """The KG_WRITE payload.  Requester, ceiling and task come from the call
    context the governance layer set, never from the model's arguments; the
    sending agent adds who it is and signs it (``Agent._submit_kg_write``)."""
    from acc.signals import SIG_KG_WRITE  # noqa: PLC0415

    return {
        "signal_type": SIG_KG_WRITE, "collective_id": ctx.collective_id,
        "requester": ctx.requester, "ceiling": ctx.ceiling or "CRITICAL",
        "task_id": ctx.task_id, "ntriples": ntriples, "share": bool(share),
        "note": str(note or "")[:500], "ts": time.time(),
    }


@contextlib.contextmanager
def writer(collective_id: str, path: Any = None) -> Iterator[Any]:
    """The store opened for writing, for one batch, then released -- so the
    operator's ``acc-cli kg sync`` can take the lock between batches."""
    from acc.kg import KnowledgeStore  # noqa: PLC0415

    store = KnowledgeStore(collective_id, path)
    try:
        yield store
    finally:
        try:
            store.store.flush()
        except Exception:  # noqa: BLE001
            pass
        del store


#: How long the arbiter waits for the store's lock before giving a write up.
#: ``acc-cli kg sync`` holds it for ~11 s on a 3k-row ledger (lighthouse,
#: 2026-10-03); a write that arrives meanwhile must wait, not vanish.
LOCK_WAIT_S = 60.0

T = TypeVar("T")


def _is_lock_error(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and "lock" in str(exc).lower()


async def with_writer(collective_id: str, fn: Callable[[Any], T], *, path: Any = None,
                      wait_s: float = LOCK_WAIT_S, sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep) -> T:
    """Run *fn(store)* with the store open for writing, waiting -- without
    blocking the event loop -- while another process holds the lock.

    Found on the edge host: with the lock held (an operator's ``kg sync``),
    the write was refused and dropped while the agent had already been told
    it was sent.  Now it waits up to *wait_s*, then raises the lock error so
    the caller logs a loud refusal.  Any other error is raised at once.
    """
    deadline, delay = time.monotonic() + wait_s, 0.25
    while True:
        try:
            with writer(collective_id, path) as store:
                return fn(store)
        except Exception as exc:  # noqa: BLE001
            if not _is_lock_error(exc) or time.monotonic() >= deadline:
                raise
            logger.info("kg: store %s is locked by another process; waiting %.2fs", collective_id, delay)
            await sleep(delay)
            delay = min(delay * 2, 5.0)


def apply_write(store: Any, payload: dict[str, Any], shapes: Any) -> tuple[str, int]:
    """Re-validate and append to the requester's private graph.

    Returns ``(graph name, triples written)``; raises
    :class:`acc.kg_shapes.ShapesError` when the triples do not conform.
    """
    from acc.kg_shapes import ShapesError, validate  # noqa: PLC0415

    requester = str(payload.get("requester") or "").strip()
    if not requester:
        raise ShapesError("no requester: a private graph needs an owner")
    verdict = validate(str(payload.get("ntriples") or ""), shapes)
    if not verdict.conforms:
        raise ShapesError("does not conform: " + "; ".join(verdict.messages[:5]))
    ceiling = str(payload.get("ceiling") or "CRITICAL").upper()
    name = store.private_graph_name(requester, ceiling)
    store.put(name, verdict.graph, ceiling=ceiling, scope="private", append=True,
              owner=requester, bnode_scope=f"{name}:{payload.get('task_id', '')}:{payload.get('ts', '')}",
              source=f"kg_propose by {payload.get('from_agent', '?')} for task {payload.get('task_id', '?')}")
    return name, verdict.triples


def share_proposal(payload: dict[str, Any], private_graph: str):
    """The ``kg_share`` proposal for an accepted write the agent asked to share."""
    from acc.assistant_proposal import PROPOSAL_KG_SHARE, AssistantProposal  # noqa: PLC0415

    ceiling = str(payload.get("ceiling") or "CRITICAL").upper()
    return AssistantProposal(
        kind=PROPOSAL_KG_SHARE,
        params={"ntriples": payload.get("ntriples", ""), "ceiling": ceiling,
                "from_private_graph": private_graph, "task_id": payload.get("task_id", ""),
                "requester": payload.get("requester", ""),
                "destination_graph": f"shared-{ceiling.lower()}"},
        summary=(f"Share {str(payload.get('ntriples', '')).count(chr(10)) or 1} fact(s) from a private "
                 f"knowledge graph into shared-{ceiling.lower()} (readable at {ceiling} and above)"),
        rationale=str(payload.get("note") or "kg_propose with share=true"),
        collective_id=str(payload.get("collective_id") or ""),
        agent_id=str(payload.get("from_agent") or ""),
    )


def apply_share(store: Any, params: dict[str, Any], shapes: Any) -> tuple[str, int]:
    """Dispatch an approved ``kg_share``: re-validate, append to the shared
    graph at the write's ceiling."""
    from acc.kg_shapes import ShapesError, validate  # noqa: PLC0415

    verdict = validate(str(params.get("ntriples") or ""), shapes)
    if not verdict.conforms:
        raise ShapesError("no longer conforms: " + "; ".join(verdict.messages[:5]))
    ceiling = str(params.get("ceiling") or "CRITICAL").upper()
    name = f"shared-{ceiling.lower()}"
    store.put(name, verdict.graph, ceiling=ceiling, scope="shared", append=True,
              bnode_scope=f"{name}:{params.get('task_id', '')}:{time.time()}",
              source=f"kg_share of task {params.get('task_id', '?')}, approved")
    return name, verdict.triples
