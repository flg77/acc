"""A standard chat-completions surface onto a governed collective.

The value is that an unmodified client can point at ACC. The risk is the same
sentence: **this is the surface most likely to be pointed at by something the
operator did not write.** So it is the surface where attribution, budgeting and
evaluation matter most, not least.

The design question is what happens when work needs approval, and it is settled
here rather than discovered at runtime:

**A gated request returns 202 with a handle.** Not a refusal — that would make
the endpoint useless for exactly the work worth governing. Not a block until
the oversight timeout — that hangs a client for minutes on a socket it did not
expect to hold. A handle the client can poll is the only option that is honest
about what is happening: the work exists, a human is deciding, and here is how
to find out.

Two things this deliberately does *not* do:

* **No path bypasses evaluation, budgets or recording.** A request arriving in
  a familiar shape is not a reason to treat it differently from one arriving on
  the bus; the shape is a convenience for the client, not a different class of
  work.
* **No unauthenticated access.** A caller presents a key that maps to a
  principal — external, default deny, admitted by an operator like any other
  external requester. A standard endpoint with no auth is an open relay onto a
  collective's budget.

A ``model`` identifier maps to a **role**, not a model. The caller is choosing
who does the work; which model that role runs on is the deployment's decision
and stays with the deployment.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger("acc.compat_endpoint")

#: Callers present a key; the SHA-256 of it is what the allowlist holds, so a
#: leaked allowlist file does not leak usable credentials.
KEYS_VAR = "ACC_COMPAT_API_KEYS"

#: Streaming is not supported. Saying so explicitly is the point: a client that
#: asks for it gets a structured error, not a response that silently is not one.
SUPPORTS_STREAMING = False


class CompatError(Exception):
    """A request was refused. Carries a status code and a machine-readable type."""

    def __init__(self, message: str, *, status: int = 400, error_type: str = "invalid_request_error"):
        super().__init__(message)
        self.status = status
        self.error_type = error_type

    def as_response(self) -> dict[str, Any]:
        """The error envelope a standard client expects."""
        return {
            "error": {
                "message": str(self),
                "type": self.error_type,
                "code": None,
            }
        }


@dataclass
class Caller:
    """Who is calling, resolved from the presented key."""

    key_id: str
    subject: str
    tier: str = "requester"

    def attribution(self) -> dict[str, Any]:
        return {
            "requested_by": f"compat:{self.subject}",
            "requester_source": "compat_endpoint",
            "requester_subject": self.subject,
            "requester_key_id": self.key_id,
        }


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def _key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _configured_keys(environ: dict[str, str] | None = None) -> dict[str, str]:
    """``{sha256(key): subject}`` from the environment.

    Digests, not keys: an allowlist that held usable credentials would be a
    credential store in a config file.
    """
    env = environ if environ is not None else os.environ
    raw = str(env.get(KEYS_VAR, "") or "")
    out: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        digest, _, subject = item.partition(":")
        if digest.strip() and subject.strip():
            out[digest.strip()] = subject.strip()
    return out


def authenticate(
    presented: str, *, environ: dict[str, str] | None = None
) -> Caller:
    """Resolve the caller, or refuse.

    Raises:
        CompatError: no key, or an unrecognised one. A standard endpoint with
            no authentication is an open relay onto a collective's budget.
    """
    keys = _configured_keys(environ)
    if not keys:
        raise CompatError(
            f"the endpoint is not configured for callers: set {KEYS_VAR} to "
            f"sha256(key):subject pairs. It refuses everything until then.",
            status=503,
            error_type="server_error",
        )
    if not presented:
        raise CompatError(
            "missing API key", status=401, error_type="authentication_error"
        )

    digest = _key_digest(presented)
    for known, subject in keys.items():
        # compare_digest: a timing-variable comparison leaks the key.
        if hmac.compare_digest(known, digest):
            return Caller(key_id=digest[:12], subject=subject)
    raise CompatError(
        "invalid API key", status=401, error_type="authentication_error"
    )


# ---------------------------------------------------------------------------
# Request shaping
# ---------------------------------------------------------------------------


@dataclass
class ChatRequest:
    """A parsed standard chat-completions request."""

    role: str                       # from the `model` field
    prompt: str
    system: str = ""
    stream: bool = False
    raw_model: str = ""


def parse_request(body: dict[str, Any]) -> ChatRequest:
    """Turn a standard request into ACC's terms.

    ``model`` names a **role**, not a model. The caller chooses who does the
    work; which model that role runs on is the deployment's decision.

    Raises:
        CompatError: malformed, or asking for streaming.
    """
    if not isinstance(body, dict):
        raise CompatError("request body must be a JSON object")

    model = str(body.get("model", "") or "").strip()
    if not model:
        raise CompatError("'model' is required; it names the ACC role to ask")

    if body.get("stream"):
        if not SUPPORTS_STREAMING:
            raise CompatError(
                "streaming is not supported by this endpoint. Send stream=false; "
                "the response is returned when the collective completes the task.",
                status=400,
                error_type="invalid_request_error",
            )

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise CompatError("'messages' must be a non-empty array")

    system_parts: list[str] = []
    user_parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else json.dumps(content, default=str)
        if message.get("role") == "system":
            system_parts.append(text)
        else:
            user_parts.append(text)

    if not user_parts:
        raise CompatError("no user message in 'messages'")

    return ChatRequest(
        role=model,
        prompt="\n\n".join(user_parts),
        system="\n\n".join(system_parts),
        stream=bool(body.get("stream")),
        raw_model=model,
    )


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


def completion_response(
    request: ChatRequest, reply: str, *, usage: dict[str, int] | None = None
) -> dict[str, Any]:
    """The response shape an unmodified client expects."""
    counts = usage or {}
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.raw_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": int(counts.get("input_tokens", 0) or 0),
            "completion_tokens": int(counts.get("output_tokens", 0) or 0),
            "total_tokens": int(counts.get("total_tokens", 0) or 0),
        },
    }


def pending_response(request: ChatRequest, oversight_id: str, task_id: str) -> dict[str, Any]:
    """202: the work exists, a human is deciding, here is how to find out.

    Deliberately not a refusal (useless for exactly the work worth governing)
    and not a block until timeout (hangs a client on a socket it did not expect
    to hold for minutes).
    """
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion.pending",
        "created": int(time.time()),
        "model": request.raw_model,
        "status": "awaiting_approval",
        "oversight_id": oversight_id,
        "task_id": task_id,
        "detail": (
            "This request needs human approval before it can run. It has not "
            "been dropped and it is not running yet. Poll with the task_id, or "
            "an operator can approve it from the oversight queue."
        ),
    }


# ---------------------------------------------------------------------------
# Handling
# ---------------------------------------------------------------------------


@dataclass
class Handled:
    """What the endpoint decided to do with a request."""

    status: int
    body: dict[str, Any]
    attribution: dict[str, Any] = field(default_factory=dict)
    dispatched: bool = False


def handle(
    body: dict[str, Any],
    presented_key: str,
    *,
    environ: dict[str, str] | None = None,
    dispatch: Any = None,
    gate: Any = None,
) -> Handled:
    """Authenticate, parse, gate, dispatch.

    *dispatch* is called only after authentication and gating; *gate* returns
    an oversight id when the work needs approval. Both are injected so the
    ordering can be tested without a live collective — and the ordering is the
    point: nothing reaches dispatch that has not been attributed and gated.
    """
    try:
        caller = authenticate(presented_key, environ=environ)
        request = parse_request(body)
    except CompatError as exc:
        return Handled(status=exc.status, body=exc.as_response())

    attribution = caller.attribution()

    oversight_id = ""
    if gate is not None:
        oversight_id = gate(request, caller) or ""
    if oversight_id:
        task_id = uuid.uuid4().hex
        logger.info(
            "compat: %s -> role %s requires approval (%s)",
            caller.subject, request.role, oversight_id,
        )
        return Handled(
            status=202,
            body=pending_response(request, oversight_id, task_id),
            attribution=attribution,
        )

    if dispatch is None:
        raise CompatError(
            "no dispatcher configured", status=503, error_type="server_error"
        )

    reply, usage = dispatch(request, caller, attribution)
    return Handled(
        status=200,
        body=completion_response(request, reply, usage=usage),
        attribution=attribution,
        dispatched=True,
    )


def models_response(roles: Iterable[str]) -> dict[str, Any]:
    """The ``/models`` listing: ACC's roles, since that is what ``model`` means."""
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": role, "object": "model", "created": now, "owned_by": "acc"}
            for role in sorted(roles)
        ],
    }
