"""What is actually running, and on what.

Answering "is the collective healthy" has meant reading logs per container.
This collects it once: every agent's role, whether it is running, the model it
actually resolved to, and how long since it was last heard from.

Three distinctions here are load-bearing, and each is easy to get wrong in a
way that wastes an operator's afternoon.

**Mapped-but-absent is not the same as failing.** A role listed in
``role_models`` with no agent running it has not crashed — it was never
deployed. Reporting those identically sends people to read logs that do not
exist.

**The resolved model must be read from the backend-appropriate variable.**
``anthropic`` publishes its model in ``ACC_ANTHROPIC_MODEL``; the
OpenAI-compatible backends use ``ACC_LLM_MODEL``. Reading the wrong one makes a
perfectly good mapping look broken, and the operator goes hunting for a fault
that is not there.

**A bus that will not answer is a finding, not an error.** If this command
cannot reach signalling it still reports what configuration *declares*, clearly
marked as unconfirmed, because "I cannot tell you anything" is the least useful
possible answer during an incident.

Read-only, no TTY required, safe to run on any cadence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("acc.status")

#: An agent is stale when nothing has been heard from it for longer than this.
#: The default heartbeat interval is 30s, so this tolerates two missed beats
#: before calling an agent unhealthy.
STALE_AFTER_S = 75.0

#: How long to listen for heartbeats before reporting. Slightly more than one
#: heartbeat interval so a healthy agent cannot be missed by bad luck.
DEFAULT_LISTEN_S = 35.0

#: Seconds to wait for the bus before calling it unreachable.
CONNECT_TIMEOUT_S = 3.0

#: Which environment variable carries the model id, per backend. Getting this
#: wrong is the "correct mapping looks broken" failure described above.
MODEL_VAR_BY_BACKEND = {
    "anthropic": "ACC_ANTHROPIC_MODEL",
    "ollama": "ACC_OLLAMA_MODEL",
    "openai_compat": "ACC_LLM_MODEL",
    "vllm": "ACC_LLM_MODEL",
    "llama_stack": "ACC_LLM_MODEL",
}


def model_var_for(backend: str) -> str:
    """The variable that carries the model id for *backend*."""
    return MODEL_VAR_BY_BACKEND.get((backend or "").strip(), "ACC_LLM_MODEL")


class AgentState(str):
    """Free-form agent state as reported on the bus."""


@dataclass
class AgentStatus:
    """One agent, as configuration declares it and the bus confirms it."""

    role: str
    agent_id: str = ""
    deployed: bool = False
    state: str = ""
    backend: str = ""
    model: str = ""
    model_id: str = ""          # the registry id the role is bound to
    chain: list[str] = field(default_factory=list)
    last_seen: float | None = None
    age_s: float | None = None

    @property
    def healthy(self) -> bool:
        """Deployed, heard from recently, and not in a failed state."""
        if not self.deployed:
            return False
        if self.age_s is not None and self.age_s > STALE_AFTER_S:
            return False
        return self.state.lower() not in ("failed", "error", "crashed")

    @property
    def condition(self) -> str:
        """One word for the report; distinguishes absent from unhealthy."""
        if not self.deployed:
            return "not-deployed"
        if self.age_s is not None and self.age_s > STALE_AFTER_S:
            return "stale"
        if self.state.lower() in ("failed", "error", "crashed"):
            return "failed"
        return "running"

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "agent_id": self.agent_id,
            "condition": self.condition,
            "healthy": self.healthy,
            "state": self.state,
            "backend": self.backend,
            "model": self.model,
            "model_id": self.model_id,
            "chain": self.chain,
            "age_s": round(self.age_s, 1) if self.age_s is not None else None,
        }


@dataclass
class CollectiveStatus:
    """The whole picture, plus what could not be determined."""

    collective_id: str
    agents: list[AgentStatus] = field(default_factory=list)
    bus_reachable: bool = False
    bus_detail: str = ""
    memory_reachable: bool | None = None
    memory_detail: str = ""
    oversight_pending: int | None = None
    key_names_present: dict[str, bool] = field(default_factory=dict)
    credential_pools: list[dict[str, Any]] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        """Healthy means: the bus answered and every mapped role is running.

        A role that was never deployed still counts as unhealthy for the exit
        code — the operator asked about a collective that configuration says
        should have it.
        """
        return self.bus_reachable and all(a.healthy for a in self.agents)

    def as_dict(self) -> dict[str, Any]:
        return {
            "collective_id": self.collective_id,
            "healthy": self.healthy,
            "bus": {"reachable": self.bus_reachable, "detail": self.bus_detail},
            "working_memory": {
                "reachable": self.memory_reachable,
                "detail": self.memory_detail,
            },
            "oversight_pending": self.oversight_pending,
            "key_names_present": self.key_names_present,
            "credential_pools": self.credential_pools,
            "agents": [a.as_dict() for a in self.agents],
        }


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def _declared_agents(collective_id: str) -> dict[str, AgentStatus]:
    """What configuration says should exist, before the bus is consulted.

    This is the baseline the bus confirms or fails to confirm; it is also the
    whole answer when the bus is unreachable.
    """
    from acc.models import load_models, load_role_chains  # noqa: PLC0415

    import os  # noqa: PLC0415

    registry = {m.model_id: m for m in load_models()}
    # The collective-wide default, for roles with no explicit binding. Read
    # from the variable that belongs to the CONFIGURED backend: an anthropic
    # binding publishes its model somewhere different from an openai_compat
    # one, and reading the wrong variable shows a blank where a perfectly good
    # model is configured.
    default_backend = os.environ.get("ACC_LLM_BACKEND", "").strip()
    default_model = os.environ.get(model_var_for(default_backend), "").strip()

    out: dict[str, AgentStatus] = {}
    for role, chain in sorted(load_role_chains().items()):
        primary = chain[0] if chain else ""
        entry = registry.get(primary)
        out[role] = AgentStatus(
            role=role,
            model_id=primary,
            chain=list(chain),
            backend=(entry.backend if entry else default_backend),
            model=(entry.model if entry else default_model),
        )
    return out


async def _gather_heartbeats(
    collective_id: str, listen_s: float
) -> tuple[dict[str, dict], bool, str]:
    """Listen briefly for heartbeats. Returns (by_role, reachable, detail)."""
    import nats  # noqa: PLC0415

    from acc.cli._common import decode_payload, nats_url  # noqa: PLC0415
    from acc.signals import subject_heartbeat  # noqa: PLC0415

    seen: dict[str, dict] = {}
    # nats-py logs a full connection traceback at ERROR when the bus is down.
    # "cannot reach the bus" is a one-line finding this command reports itself;
    # a stack trace on stderr buries the report it is meant to introduce.
    nats_logger = logging.getLogger("nats.aio.client")
    previous_level = nats_logger.level
    nats_logger.setLevel(logging.CRITICAL)
    try:
        # A BOUNDED probe, not the shared connect helper. That helper keeps
        # nats-py's reconnect defaults, which spend ~4 minutes retrying before
        # giving up — and a status command that hangs for four minutes when the
        # bus is down is unusable precisely when it is needed. Fail fast and
        # report "unreachable"; that is the finding, not an error to survive.
        # wait_for is the actual bound. nats-py's own connect_timeout governs a
        # single attempt, not the loop around them, so on a closed port it can
        # still spend minutes before raising.
        nc = await asyncio.wait_for(
            nats.connect(
                nats_url(),
                connect_timeout=CONNECT_TIMEOUT_S,
                allow_reconnect=False,
                max_reconnect_attempts=0,
            ),
            timeout=CONNECT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return seen, False, f"timed out after {CONNECT_TIMEOUT_S:.0f}s"
    except Exception as exc:  # noqa: BLE001 — reported, not raised
        return seen, False, f"{type(exc).__name__}: {exc}"
    finally:
        nats_logger.setLevel(previous_level)

    try:
        async def _on(msg: Any) -> None:
            try:
                payload = decode_payload(msg.data)
            except Exception:  # pragma: no cover — malformed frame
                return
            role = str(payload.get("role") or "")
            if not role:
                return
            previous = seen.get(role)
            if previous is None or payload.get("ts", 0) >= previous.get("ts", 0):
                seen[role] = payload

        sub = await nc.subscribe(subject_heartbeat(collective_id), cb=_on)
        await asyncio.sleep(listen_s)
        await sub.unsubscribe()
        return seen, True, "connected"
    except Exception as exc:  # noqa: BLE001
        return seen, False, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await nc.close()
        except Exception:  # pragma: no cover
            pass


def _apply_heartbeats(
    declared: dict[str, AgentStatus], beats: dict[str, dict]
) -> list[AgentStatus]:
    now = time.time()
    for role, payload in beats.items():
        agent = declared.setdefault(role, AgentStatus(role=role))
        agent.deployed = True
        agent.agent_id = str(payload.get("agent_id") or "")
        agent.state = str(payload.get("state") or "")
        ts = payload.get("ts")
        if isinstance(ts, (int, float)):
            agent.last_seen = float(ts)
            agent.age_s = max(0.0, now - float(ts))
        info = payload.get("llm_backend") or {}
        if isinstance(info, dict):
            # The bus reports what the agent ACTUALLY resolved, which is the
            # thing worth knowing; configuration only says what it should be.
            agent.backend = str(info.get("backend") or agent.backend)
            agent.model = str(info.get("model") or agent.model)
    return [declared[k] for k in sorted(declared)]


def _key_names(collective_id: str) -> dict[str, bool]:
    """Presence, by name, of every key a bound model needs. Never values."""
    import os  # noqa: PLC0415

    from acc.models import load_models, load_role_chains  # noqa: PLC0415

    bound: set[str] = set()
    for chain in load_role_chains().values():
        bound.update(chain)
    out: dict[str, bool] = {}
    for entry in load_models():
        if entry.model_id not in bound:
            continue
        name = (entry.api_key_env or "").strip()
        if name:
            out[name] = bool(str(os.environ.get(name, "")).strip())
    return out


def _memory_and_oversight(collective_id: str) -> tuple[bool | None, str, int | None]:
    """Working-memory reachability and oversight queue depth, best effort."""
    import os  # noqa: PLC0415

    url = os.environ.get("ACC_REDIS_URL", "").strip()
    if not url:
        return None, "ACC_REDIS_URL not set", None
    try:
        import redis  # noqa: PLC0415

        client = redis.from_url(url, socket_connect_timeout=3)
        client.ping()
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}", None
    try:
        depth = client.llen(f"acc:{collective_id}:oversight:pending")
    except Exception:  # noqa: BLE001 — reachable but the key may not exist
        depth = None
    return True, "connected", depth


def collect(
    collective_id: str | None = None, *, listen_s: float = DEFAULT_LISTEN_S
) -> CollectiveStatus:
    """Gather the whole picture. Never raises; unknowns are reported as such."""
    from acc.cli._common import default_collective  # noqa: PLC0415

    cid = collective_id or default_collective()
    declared = _declared_agents(cid)

    try:
        beats, reachable, detail = asyncio.run(_gather_heartbeats(cid, listen_s))
    except Exception as exc:  # noqa: BLE001
        beats, reachable, detail = {}, False, f"{type(exc).__name__}: {exc}"

    agents = _apply_heartbeats(declared, beats)
    memory_ok, memory_detail, pending = _memory_and_oversight(cid)
    return CollectiveStatus(
        collective_id=cid,
        agents=agents,
        bus_reachable=reachable,
        bus_detail=detail,
        memory_reachable=memory_ok,
        memory_detail=memory_detail,
        oversight_pending=pending,
        key_names_present=_key_names(cid),
        credential_pools=_pool_health(),
    )


def _pool_health() -> list[dict[str, Any]]:
    """Credential-pool health, so a faulted key is visible here too.

    A pool that silently masks a dead credential is how an operator finds out
    at renewal that only one of four ever worked; status is where they look.
    """
    try:
        from acc import credential_pool  # noqa: PLC0415

        return [
            row
            for row in credential_pool.status()
            if row["health"] != credential_pool.Health.HEALTHY or not row["present"]
        ]
    except Exception:  # pragma: no cover — pools are optional
        return []


def to_json(status: CollectiveStatus) -> str:
    return json.dumps(status.as_dict(), indent=2)
