"""A second choice when a provider fails.

ACC has had no notion of an alternate model.  When a provider fails the backend
retries the *same* model three times and raises, and the role stays bound to the
dead endpoint until a human edits configuration and restarts the agent.  That is
an availability defect: a governed runtime that stops thinking when one endpoint
blinks cannot be deployed where nobody is on shift.

This module is the resolver that sits in front of the backends (option C of the
change).  Backends stay dumb — they still just call one endpoint — and the
decision about *which* endpoint moves here, where the chain, the health record
and the policy gate live together.

Two properties beyond the mechanism, both required by the change:

* **Failover is visible.**  A collective silently running on its secondary is a
  deployment whose behaviour changed without anyone being told, so every hop is
  emitted as an event and the active model is reportable.
* **Recovery is automatic.**  A chain that never returns to the primary turns a
  transient outage into a permanent downgrade, so an unhealthy model is retried
  once its cooldown expires and the chain is always re-entered from the top.

Mechanism only — the policy is deliberately separate
----------------------------------------------------
Whether failover may cross a **trust or data-residency boundary** without human
approval is an operator decision that is explicitly still open.  Nothing here
pre-empts it: hops go through a :class:`PolicyGate`, and the default
(:class:`ZonePolicyGate`) refuses any hop that crosses a *declared* zone.

Deployments that never declared zones are unaffected, which is what keeps the
default from being useless: refusing every hop by default would ship a failover
feature that never fails over.  The moment an operator annotates one model with
a ``zone``, boundaries exist in the configuration and this gate starts enforcing
them.  Whatever the operator decides later is then additive — a different gate,
not a rewrite.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol, Sequence

from acc.backends import BackendConnectionError, LLMBackend, LLMCallError
from acc.models import ModelEntry

logger = logging.getLogger("acc.llm_failover")

#: How long a model stays out of rotation after a failure.  Short enough that a
#: brief outage does not pin the collective to its secondary for the rest of the
#: day, long enough that a hard-down endpoint is not re-probed on every task.
DEFAULT_COOLDOWN_S = 60.0


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


def is_retryable(exc: BaseException) -> bool:
    """Should *exc* move the chain on to the next model?

    A wrong answer here is costly in both directions: treat a fatal error as
    retryable and a bad API key silently burns through every model in the chain
    before reporting anything useful; treat a retryable one as fatal and the
    outage this module exists to survive still takes the collective down.

    ``LLMCallError`` already carries the distinction (429/5xx retryable,
    4xx client errors not), so this defers to it rather than re-deriving a
    second classification that could disagree with the backend's own.
    """
    if isinstance(exc, LLMCallError):
        return bool(exc.retryable)
    if isinstance(exc, BackendConnectionError):
        return True
    # A connection that never established, or one that timed out, is exactly
    # the endpoint-down case — but only recognise the transport errors, never a
    # bare Exception, which would make every bug look like an outage.
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    return False


# ---------------------------------------------------------------------------
# Policy gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """Whether a hop may proceed, and why."""

    allowed: bool
    reason: str


class PolicyGate(Protocol):
    """Decides whether failover may move from one model to another."""

    def allow(self, frm: ModelEntry, to: ModelEntry) -> Decision:
        ...


class ZonePolicyGate:
    """Refuse a hop that crosses a **declared** trust/residency zone.

    The zone comes from ``ModelEntry.zone`` in ``models.yaml``.  The rules:

    * neither model declares a zone — the deployment has not adopted zones, so
      no boundary is being crossed as far as the configuration expresses one:
      **allow**;
    * both declare the same zone: **allow**;
    * anything else — different zones, or one declared and one not — the
      operator has said boundaries exist and this hop cannot be shown to stay
      inside one: **refuse**.

    The third rule is the restrictive default the change requires.  It fails
    closed and names the reason rather than proceeding quietly.
    """

    def allow(self, frm: ModelEntry, to: ModelEntry) -> Decision:
        a = (getattr(frm, "zone", "") or "").strip()
        b = (getattr(to, "zone", "") or "").strip()
        if not a and not b:
            return Decision(True, "no zones declared")
        if a and a == b:
            return Decision(True, f"same zone {a!r}")
        return Decision(
            False,
            f"hop {frm.model_id!r} -> {to.model_id!r} crosses a trust/residency "
            f"boundary ({a or '<undeclared>'} -> {b or '<undeclared>'}); "
            f"automatic cross-boundary failover is not enabled",
        )


class AllowAllGate:
    """Permit every hop.

    Only for a deployment that has decided cross-boundary failover is
    acceptable, and for tests.  Not the default.
    """

    def allow(self, frm: ModelEntry, to: ModelEntry) -> Decision:
        return Decision(True, "policy: all hops permitted")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class Availability:
    """Which models are currently worth trying.

    A model that fails is put out of rotation for ``cooldown_s``.  Nothing here
    marks a model permanently dead: the cooldown expiring *is* the automatic
    recovery path, so a primary that comes back is picked up on the next task
    without operator action.
    """

    def __init__(self, cooldown_s: float = DEFAULT_COOLDOWN_S, *, clock: Callable[[], float] | None = None) -> None:
        self._cooldown_s = cooldown_s
        self._clock = clock or time.monotonic
        self._down: dict[str, float] = {}

    def is_available(self, model_id: str) -> bool:
        until = self._down.get(model_id)
        if until is None:
            return True
        if self._clock() >= until:
            # Cooldown expired — drop the record so the model gets a clean
            # attempt rather than carrying its old failure forever.
            del self._down[model_id]
            return True
        return False

    def record_failure(self, model_id: str) -> None:
        self._down[model_id] = self._clock() + self._cooldown_s

    def record_success(self, model_id: str) -> None:
        self._down.pop(model_id, None)

    def snapshot(self) -> dict[str, float]:
        """Remaining cooldown per unavailable model, for status output."""
        now = self._clock()
        return {m: round(t - now, 1) for m, t in self._down.items() if t > now}


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailoverEvent:
    """One hop, for the durable record and for status output."""

    role: str
    from_model: str
    to_model: str
    reason: str
    kind: str = "failover"  # failover | refused | recovered

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "role": self.role,
            "from_model": self.from_model,
            "to_model": self.to_model,
            "reason": self.reason,
        }


EventSink = Callable[[FailoverEvent], None]


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------


@dataclass
class FailoverBackend:
    """An :class:`~acc.backends.LLMBackend` that walks a chain of models.

    Attempts each entry in order, skipping models in cooldown, and moves on only
    when the error is retryable and the policy gate permits the hop.  The chain
    is re-entered from the top on every call, which is what makes recovery
    automatic: as soon as the primary's cooldown lapses it is tried first again.

    Attributes:
        role: the role this chain belongs to (for events).
        entries: ordered model entries; ``entries[0]`` is the primary.
        build: turns a :class:`ModelEntry` into a live backend.
        gate: decides whether a hop may proceed.
        availability: health record, shared across calls.
        on_event: receives every hop, refusal and recovery.
    """

    role: str
    entries: Sequence[ModelEntry]
    build: Callable[[ModelEntry], LLMBackend]
    gate: PolicyGate = field(default_factory=ZonePolicyGate)
    availability: Availability = field(default_factory=Availability)
    on_event: EventSink | None = None
    _active: str = ""
    _clients: dict[str, LLMBackend] = field(default_factory=dict)

    # -- introspection ----------------------------------------------------

    @property
    def primary(self) -> str:
        return self.entries[0].model_id if self.entries else ""

    @property
    def active_model(self) -> str:
        """The model that last served a call (empty before the first)."""
        return self._active

    def status(self) -> dict[str, Any]:
        """What an operator needs to see: is this running on its primary?

        Reported even when nothing has failed, because "we are on the primary"
        is the answer that makes the absence of an alert meaningful.
        """
        return {
            "role": self.role,
            "primary": self.primary,
            "active": self._active or self.primary,
            "on_primary": (self._active or self.primary) == self.primary,
            "chain": [e.model_id for e in self.entries],
            "cooldown": self.availability.snapshot(),
        }

    # -- LLMBackend -------------------------------------------------------

    async def complete(
        self,
        system: str,
        user: str,
        response_schema: dict | None = None,
        cache_prefix: bool = False,
    ) -> dict:
        return await self._attempt(
            "complete",
            lambda c: c.complete(system, user, response_schema, cache_prefix),
        )

    async def embed(self, text: str) -> list[float]:
        return await self._attempt("embed", lambda c: c.embed(text))

    # -- internals --------------------------------------------------------

    def _client(self, entry: ModelEntry) -> LLMBackend:
        if entry.model_id not in self._clients:
            self._clients[entry.model_id] = self.build(entry)
        return self._clients[entry.model_id]

    def _emit(self, event: FailoverEvent) -> None:
        logger.warning(
            "llm_failover: %s role=%s %s -> %s (%s)",
            event.kind, event.role, event.from_model, event.to_model, event.reason,
        )
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # pragma: no cover — a sink must never break a call
                logger.exception("llm_failover: event sink raised")

    async def _attempt(self, op: str, call: Callable[[LLMBackend], Any]) -> Any:
        if not self.entries:
            raise LLMCallError("no models in the chain", retryable=False)

        last_exc: BaseException | None = None
        previous: ModelEntry | None = None
        skipped: list[str] = []

        for entry in self.entries:
            if previous is not None:
                decision = self.gate.allow(previous, entry)
                if not decision.allowed:
                    # Fail closed, naming the reason.  Do NOT keep walking: a
                    # refused boundary is a policy answer, not a bad endpoint,
                    # and trying the entry after it would cross the same line.
                    self._emit(
                        FailoverEvent(
                            self.role, previous.model_id, entry.model_id,
                            decision.reason, kind="refused",
                        )
                    )
                    raise LLMCallError(
                        f"failover refused: {decision.reason}", retryable=False
                    ) from last_exc

            if not self.availability.is_available(entry.model_id):
                skipped.append(entry.model_id)
                previous = entry
                continue

            try:
                result = await call(self._client(entry))
            except Exception as exc:  # noqa: BLE001 — classified immediately
                if not is_retryable(exc):
                    # Fatal (auth, malformed request): the next model would fail
                    # the same way, and burning the chain would hide the cause.
                    raise
                self.availability.record_failure(entry.model_id)
                last_exc = exc
                previous = entry
                logger.warning(
                    "llm_failover: %s failed on %s (%s); advancing",
                    op, entry.model_id, exc,
                )
                continue

            self.availability.record_success(entry.model_id)
            if entry.model_id != self.primary and self._active != entry.model_id:
                self._emit(
                    FailoverEvent(
                        self.role, self.primary, entry.model_id,
                        f"{op} failed on the primary: {last_exc}",
                    )
                )
            elif entry.model_id == self.primary and self._active not in ("", self.primary):
                self._emit(
                    FailoverEvent(
                        self.role, self._active, entry.model_id,
                        "primary recovered", kind="recovered",
                    )
                )
            self._active = entry.model_id
            return result

        detail = f" (skipped, in cooldown: {', '.join(skipped)})" if skipped else ""
        raise LLMCallError(
            f"every model in the chain for role {self.role!r} failed{detail}",
            retryable=True,
        ) from last_exc


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def resolve_entries(
    chain: Iterable[str], registry: dict[str, ModelEntry]
) -> list[ModelEntry]:
    """Map model ids to registry entries, dropping ids the registry lacks.

    A chain naming a model that does not exist is a configuration fault, but it
    must not take the collective down: the known entries still work, and the
    unknown one is logged.  ``acc-cli config check`` is where it should be
    reported.
    """
    out: list[ModelEntry] = []
    for model_id in chain:
        entry = registry.get(model_id)
        if entry is None:
            logger.warning(
                "llm_failover: chain names unknown model %r — skipping", model_id
            )
            continue
        out.append(entry)
    return out


def build_failover_backend(
    role: str,
    chain: Sequence[str],
    registry: dict[str, ModelEntry],
    build: Callable[[ModelEntry], LLMBackend],
    *,
    gate: PolicyGate | None = None,
    cooldown_s: float = DEFAULT_COOLDOWN_S,
    on_event: EventSink | None = None,
) -> FailoverBackend | None:
    """A :class:`FailoverBackend`, or ``None`` when there is nothing to fail over to.

    Returning ``None`` for a chain of fewer than two usable models is what keeps
    the no-chain case byte-identical to today's behaviour: the caller keeps the
    backend it already built and nothing in the call path changes.
    """
    entries = resolve_entries(chain, registry)
    if len(entries) < 2:
        return None
    return FailoverBackend(
        role=role,
        entries=entries,
        build=build,
        gate=gate or ZonePolicyGate(),
        availability=Availability(cooldown_s),
        on_event=on_event,
    )


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _llm_overlay(entry: ModelEntry) -> dict[str, str]:
    """The ``LLMConfig`` fields that pin a call to *entry*.

    Deliberately the same mapping as :func:`acc.models.model_env`, which does
    this for a *container's* environment.  Same routing rules, different
    target: that function boots a new agent on a model, this one points an
    in-process client at it.
    """
    out: dict[str, str] = {"backend": entry.backend}
    if entry.backend == "anthropic":
        if entry.model:
            out["anthropic_model"] = entry.model
    elif entry.backend == "ollama":
        if entry.model:
            out["ollama_model"] = entry.model
        if entry.base_url:
            out["ollama_base_url"] = entry.base_url
    else:  # openai_compat / vllm / llama_stack — universal fields
        if entry.model:
            out["model"] = entry.model
        if entry.base_url:
            out["base_url"] = entry.base_url
        if entry.api_key_env:
            out["api_key_env"] = entry.api_key_env
    return out


def backend_for_entry(entry: ModelEntry, config: Any) -> LLMBackend:
    """Build a live client for one registry entry.

    Goes through the existing :func:`acc.config.build_llm_backend` rather than
    constructing backends here, so there is one place that knows how each
    backend is wired and this module cannot drift from it.
    """
    from acc.config import build_llm_backend  # noqa: PLC0415 — avoids an import cycle

    llm = config.llm.model_copy(update=_llm_overlay(entry))
    return build_llm_backend(config.model_copy(update={"llm": llm}))


def wrap_for_role(
    base: LLMBackend,
    role: str | None,
    config: Any,
    *,
    models_path: Any = None,
    on_event: EventSink | None = None,
) -> LLMBackend:
    """Return *base* wrapped in a chain, or *base* itself.

    The unwrapped return is the important half: a deployment that has not
    declared an alternate keeps the exact backend it already had, so the
    no-chain path is not merely equivalent to today's behaviour — it *is*
    today's behaviour, with no failover code in the call path at all.
    """
    if not role:
        return base
    try:
        from acc.models import load_models, load_role_chains  # noqa: PLC0415

        chain = load_role_chains(models_path).get(role) or []
        if len(chain) < 2:
            return base
        registry = {m.model_id: m for m in load_models(models_path)}
        wrapped = build_failover_backend(
            role,
            chain,
            registry,
            lambda e: backend_for_entry(e, config),
            on_event=on_event,
        )
    except Exception:
        # Configuration problems must not stop an agent from booting on its
        # primary; the chain is an availability improvement, not a dependency.
        logger.exception("llm_failover: could not build a chain for role %r", role)
        return base
    if wrapped is None:
        return base
    logger.info(
        "llm_failover: role %r has a chain: %s",
        role, " -> ".join(e.model_id for e in wrapped.entries),
    )
    return wrapped
