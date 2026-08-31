"""The OpenAI-compatible surface, served (HG-24).

``acc.compat_endpoint`` decided what a completion means when the responder is a
governed collective; it just had no socket.  This is the socket.

Three routes:

* ``POST /v1/chat/completions`` — the request an unmodified client sends.
* ``GET  /v1/models`` — ACC's roles, because ``model`` names a role here.
* ``GET  /v1/tasks/{task_id}`` — the poll route the 202 body promises.

That third one is not optional.  ``pending_response()`` tells the caller to
"poll with the task_id"; without a route to poll, a 202 is a dropped request
with better prose.

**Mounted only when keys are configured.**  Absent keys mean the router is not
registered at all, rather than registered and returning 401 — a 401 still
advertises that ACC is listening here, and this is the surface most likely to be
probed by something the operator did not write.

**This endpoint serves non-gated work only.**  HG-24 allowed either that or a
pending handle; the handle is not currently truthful, because ACC has no
pre-execution oversight gate on an ordinary completion -- it gates *actions*
(capabilities, assistant proposals), while ``cognitive_core`` classifies a
prompt's EU AI Act risk *after* the work, for the audit record.  So a
HIGH-or-above role is refused rather than dispatched.  See :func:`_make_gate`.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from acc import compat_endpoint as compat
from acc.webgui.deps import get_hub

logger = logging.getLogger("acc.webgui.compat")

router = APIRouter(prefix="/v1", tags=["openai-compat"])

#: How long a pending handle is retained before the poll route reports it
#: expired.  A task nobody ever approves must not poll `awaiting_approval`
#: forever — that is indistinguishable from a queue that is merely slow.
PENDING_TTL_S = 24 * 3600

#: Long-poll ceiling for a dispatched request. Beyond this the client gets a
#: task_id to poll rather than a held socket.
DISPATCH_TIMEOUT_S = 120


class PendingStore:
    """Where a 202 handle lives until someone decides.

    In-process. That is a real limitation and is stated rather than hidden: a
    restart loses pending handles, and a second replica does not see the
    first's. Both are acceptable for a single-deployment endpoint that is off by
    default; neither is acceptable for a fleet, and moving this to Redis is the
    obvious next step (the assistant-proposal cache in ``agent.py`` already
    follows that pattern).
    """

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def put(self, task_id: str, record: dict[str, Any]) -> None:
        record["created"] = time.time()
        self._items[task_id] = record

    def get(self, task_id: str) -> dict[str, Any] | None:
        record = self._items.get(task_id)
        if record is None:
            return None
        if time.time() - record["created"] > PENDING_TTL_S:
            self._items.pop(task_id, None)
            return None
        return record

    def resolve(self, task_id: str, **fields: Any) -> None:
        record = self._items.get(task_id)
        if record is not None:
            record.update(fields)


_pending = PendingStore()


def _presented_key(authorization: str | None, api_key: str | None) -> str:
    """Pull the key from either header an OpenAI client might use."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return (api_key or "").strip()


def _collective_id(request: Request) -> str:
    hub = get_hub(request)
    ids = list(hub.collective_ids)
    if not ids:
        raise HTTPException(status_code=503, detail="no collective is attached")
    return ids[0]


async def _make_gate(collective_id: str, hub: Any):
    """Refuse work this endpoint cannot govern.

    HG-24 offered two acceptable answers -- "the endpoint supports only
    non-gated work, or returns a pending handle".  This ships the first, because
    the second is not currently truthful.

    ACC gates **actions**, not text generation.  ``capability_dispatch`` submits
    to the oversight queue before invoking a capability, and ``agent.py`` does
    the same for assistant proposals.  But for an ordinary prompt,
    ``cognitive_core`` classifies EU AI Act risk *after* the work, to fill the
    audit record -- it does not hold execution.  There is no pre-execution
    oversight gate on a completion to call.

    So a HIGH-or-above role is refused here rather than dispatched.  Refusing is
    the conservative half of the pair: it never runs HIGH-risk work outside
    oversight.  Returning a handle would require a gate that does not exist, and
    a 202 pointing at an oversight item nobody created is worse than a refusal
    -- it is a lie the client cannot detect.

    The 202 machinery in ``compat_endpoint`` stays: when a real pre-flight gate
    lands, this function returns its oversight id and the rest already works.
    """
    from acc.compliance.eu_ai_act import EUAIActClassifier  # noqa: PLC0415

    classifier = EUAIActClassifier()

    async def gate(request: compat.ChatRequest, caller: compat.Caller) -> str:
        risk = classifier.classify(request.role, "TASK_ASSIGN")
        if not classifier.is_high_or_above(risk):
            return ""
        raise compat.CompatError(
            f"role '{request.role}' is classified {risk} under the EU AI Act and "
            "requires human oversight, which this endpoint cannot yet arrange. "
            "The request was not run. Use a lower-risk role, or drive this work "
            "through the TUI or web GUI where the oversight queue is reachable.",
            status=403,
            error_type="policy_violation",
        )

    return gate


async def _make_dispatch(collective_id: str, hub: Any):
    """Submit to the collective and wait for the reply."""
    from acc.channels.webgui import WebPromptChannel  # noqa: PLC0415

    async def dispatch(
        request: compat.ChatRequest,
        caller: compat.Caller,
        attribution: dict[str, Any],
    ) -> tuple[str, dict[str, int]]:
        obs = hub.observer(collective_id)
        if obs is None:
            raise compat.CompatError(
                f"collective {collective_id} is not attached",
                status=503, error_type="server_error",
            )
        prompt = request.prompt
        if request.system:
            prompt = f"{request.system}\n\n{prompt}"

        channel = WebPromptChannel(
            obs, collective_id=collective_id,
            from_agent=f"compat:{caller.subject}",
        )
        task_id = await channel.send(
            prompt=prompt,
            target_role=request.role,
            # Naming the thread is all a client may do; the turns themselves
            # are replayed from the tracelog. Empty when the caller sent no
            # session header, which leaves the agent's task_id fallback and
            # therefore the pre-existing one-turn behaviour.
            session_id=request.session_id or None,
        )
        try:
            reply = await channel.receive(task_id, timeout=DISPATCH_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise compat.CompatError(
                f"no reply within {DISPATCH_TIMEOUT_S}s; the task is still "
                f"running as {task_id}",
                status=504, error_type="server_error",
            )
        finally:
            await channel.close()

        if reply.blocked:
            # Blocked during execution rather than at the gate — a compliance
            # layer said no once it saw the work. Surfaced as a refusal, not a
            # completion, so a client cannot mistake it for an answer.
            raise compat.CompatError(
                f"the collective refused this request: {reply.block_reason}",
                status=403, error_type="policy_violation",
            )

        usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        for key in usage:
            value = getattr(reply, key, None)
            if isinstance(value, int):
                usage[key] = value
        return reply.output, usage

    return dispatch


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    x_acc_session: str | None = Header(default=None, alias="x-acc-session"),
) -> Any:
    """A completion, optionally continuing a thread.

    The thread is named by an ``X-ACC-Session`` header rather than a body field,
    because the OpenAI schema has no session concept and adding one would make
    the request non-standard for every other client.

    A caller may reuse another principal's session id, and that is handled where
    it belongs: replay is filtered on the same ``acc.memory_scope`` key episodes
    use, and `compat_endpoint` is ISOLATED there, so a foreign thread replays
    **empty** — never partially, and never as an error that would confirm the
    thread exists.
    """
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    body = await request.json()
    hub = get_hub(request)
    collective_id = _collective_id(request)

    try:
        result = await compat.handle_async(
            body,
            _presented_key(authorization, x_api_key),
            gate=await _make_gate(collective_id, hub),
            dispatch=await _make_dispatch(collective_id, hub),
            session_id=(x_acc_session or "").strip(),
        )
    except compat.CompatError as exc:
        return JSONResponse(status_code=exc.status, content=exc.as_response())

    if result.status == 202:
        _pending.put(
            result.body["task_id"],
            {
                "status": "awaiting_approval",
                "oversight_id": result.body.get("oversight_id", ""),
                "subject": result.attribution.get("requested_by", ""),
                "model": result.body.get("model", ""),
            },
        )
    return JSONResponse(status_code=result.status, content=result.body)


@router.get("/models")
async def list_models(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> Any:
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    try:
        compat.authenticate(_presented_key(authorization, x_api_key))
    except compat.CompatError as exc:
        return JSONResponse(status_code=exc.status, content=exc.as_response())

    from acc.role_store import RoleStore  # noqa: PLC0415

    try:
        roles = sorted(RoleStore().list_roles())
    except Exception:  # noqa: BLE001 — a role-store failure must not 500 here
        roles = []
    return compat.models_response(roles)


@router.get("/tasks/{task_id}")
async def task_status(
    task_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> Any:
    """The poll route the 202 body promises.

    A caller may poll only its own task. A foreign or unknown task_id both
    return 404 — 403 would confirm the task exists, which is a disclosure to
    anyone guessing ids.
    """
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    try:
        caller = compat.authenticate(_presented_key(authorization, x_api_key))
    except compat.CompatError as exc:
        return JSONResponse(status_code=exc.status, content=exc.as_response())

    record = _pending.get(task_id)
    if record is None or record.get("subject") != caller.attribution().get("requested_by"):
        return JSONResponse(
            status_code=404,
            content={"error": {"message": f"no such task: {task_id}",
                               "type": "invalid_request_error"}},
        )

    return {
        "id": task_id,
        "object": "chat.completion.task",
        "status": record["status"],
        "oversight_id": record.get("oversight_id", ""),
        "model": record.get("model", ""),
        "result": record.get("result"),
    }


def is_enabled(environ: dict[str, str] | None = None) -> bool:
    """True when at least one key→principal mapping is configured.

    The router is not mounted otherwise. See the module docstring on why absent
    beats 401 here.
    """
    return bool(compat._configured_keys(environ))
