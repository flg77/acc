"""Objectives that persist across turns, under limits the operator sets.

An operator wants "keep the dependency set current" to survive the turn it was
asked in. The governance shape is the whole specification, and two rules carry
it.

**A ceiling is mandatory.** An objective declares a bound — turns, tokens,
wall-clock, or a combination — and stops when it is reached. "Until met", judged
by the agent, is not a termination condition: it is the absence of one, and it
is how a persistent objective becomes an unbounded spend nobody authorised. An
objective with no ceiling is refused at creation.

**An objective does not raise the autonomy level.** This is the rule that makes
persistence safe. A gated action inside an objective is still gated: the
objective **waits** rather than escalating. Otherwise "pursue this across turns"
would be a way to obtain approval-free execution by phrasing, and the governed
path would be the one that was easy to avoid.

Everything is attributed, so an objective's cost is measurable rather than
inferred from a bill at the end of the month.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from acc._atomic_write import atomic_write_text

logger = logging.getLogger("acc.objectives")

STORE_PATH_VAR = "ACC_OBJECTIVES_PATH"
DEFAULT_STORE_FILE = "objectives.json"


class ObjectiveError(Exception):
    """An objective operation was refused. The message is operator-facing."""


class State:
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    DONE = "done"


@dataclass
class Ceiling:
    """The bound an objective stops at. At least one limit is required."""

    max_turns: int = 0
    max_tokens: int = 0
    max_seconds: float = 0.0

    def declared(self) -> bool:
        return bool(self.max_turns or self.max_tokens or self.max_seconds)

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_turns": self.max_turns,
            "max_tokens": self.max_tokens,
            "max_seconds": self.max_seconds,
        }


@dataclass
class Consumption:
    """What an objective has actually used."""

    turns: int = 0
    tokens: int = 0
    started_at: float = 0.0

    def elapsed(self, now: float | None = None) -> float:
        if not self.started_at:
            return 0.0
        return max(0.0, (now if now is not None else time.time()) - self.started_at)

    def as_dict(self) -> dict[str, Any]:
        return {
            "turns": self.turns,
            "tokens": self.tokens,
            "started_at": self.started_at,
            "elapsed_s": round(self.elapsed(), 1),
        }


@dataclass
class Objective:
    """One persistent objective."""

    id: str
    statement: str
    ceiling: Ceiling
    owner: str = ""
    role: str = ""
    state: str = State.ACTIVE
    consumption: Consumption = field(default_factory=Consumption)
    stop_reason: str = ""
    waiting_on: str = ""       # oversight id this objective is blocked behind
    created_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "ceiling": self.ceiling.as_dict(),
            "owner": self.owner,
            "role": self.role,
            "state": self.state,
            "consumption": self.consumption.as_dict(),
            "stop_reason": self.stop_reason,
            "waiting_on": self.waiting_on,
            "created_at": self.created_at,
            "exhausted": bool(self.exhausted()),
        }

    def exhausted(self, now: float | None = None) -> str:
        """Which limit has been reached, or "" when none has.

        Checked before work, not after: an objective that notices it is over
        budget only once the spend has happened has not been bounded, it has
        been observed.
        """
        c, u = self.ceiling, self.consumption
        if c.max_turns and u.turns >= c.max_turns:
            return f"turn ceiling reached ({u.turns}/{c.max_turns})"
        if c.max_tokens and u.tokens >= c.max_tokens:
            return f"token ceiling reached ({u.tokens}/{c.max_tokens})"
        if c.max_seconds and u.elapsed(now) >= c.max_seconds:
            return (
                f"time ceiling reached ({u.elapsed(now):.0f}s/{c.max_seconds:.0f}s)"
            )
        return ""

    def runnable(self) -> bool:
        return self.state == State.ACTIVE and not self.waiting_on and not self.exhausted()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def store_path(repo_root: Path | None = None) -> Path:
    raw = os.environ.get(STORE_PATH_VAR, "").strip()
    if raw:
        return Path(raw)
    root = repo_root or Path(__file__).resolve().parent.parent
    return root / DEFAULT_STORE_FILE


def load(repo_root: Path | None = None) -> dict[str, Objective]:
    """Read objectives. Survives a restart; a malformed store yields none."""
    path = store_path(repo_root)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.error("objectives: store unreadable (%s) — none will run", exc)
        return {}

    out: dict[str, Objective] = {}
    for item in raw.get("objectives", []) if isinstance(raw, dict) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        ceiling = Ceiling(**{k: v for k, v in (item.get("ceiling") or {}).items()
                             if k in ("max_turns", "max_tokens", "max_seconds")})
        used = item.get("consumption") or {}
        out[str(item["id"])] = Objective(
            id=str(item["id"]),
            statement=str(item.get("statement", "")),
            ceiling=ceiling,
            owner=str(item.get("owner", "")),
            role=str(item.get("role", "")),
            state=str(item.get("state", State.ACTIVE)),
            consumption=Consumption(
                turns=int(used.get("turns", 0) or 0),
                tokens=int(used.get("tokens", 0) or 0),
                started_at=float(used.get("started_at", 0) or 0),
            ),
            stop_reason=str(item.get("stop_reason", "")),
            waiting_on=str(item.get("waiting_on", "")),
            created_at=float(item.get("created_at", 0) or 0),
        )
    return out


def save(objectives: dict[str, Objective], repo_root: Path | None = None) -> Path:
    path = store_path(repo_root)
    payload = {"objectives": [o.as_dict() for o in objectives.values()]}
    atomic_write_text(
        path, json.dumps(payload, indent=2, sort_keys=True), mode=0o644, newline=""
    )
    return path


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def create(
    statement: str,
    *,
    max_turns: int = 0,
    max_tokens: int = 0,
    max_seconds: float = 0.0,
    owner: str = "",
    role: str = "",
    repo_root: Path | None = None,
) -> Objective:
    """Create an objective. A ceiling is mandatory.

    Raises:
        ObjectiveError: no statement, or no ceiling. "Until met", judged by the
            agent, is the absence of a termination condition.
    """
    if not statement.strip():
        raise ObjectiveError("an objective needs a statement")
    ceiling = Ceiling(max_turns=max_turns, max_tokens=max_tokens, max_seconds=max_seconds)
    if not ceiling.declared():
        raise ObjectiveError(
            "an objective must declare a ceiling: --max-turns, --max-tokens or "
            "--max-seconds. An objective the agent decides when to stop pursuing "
            "is an unbounded spend nobody authorised."
        )

    objective = Objective(
        id=f"obj-{uuid.uuid4().hex[:10]}",
        statement=statement.strip(),
        ceiling=ceiling,
        owner=owner,
        role=role,
        created_at=time.time(),
        consumption=Consumption(started_at=time.time()),
    )
    objectives = load(repo_root)
    objectives[objective.id] = objective
    save(objectives, repo_root)
    logger.info("objectives: created %s (%s)", objective.id, objective.statement[:60])
    return objective


def _mutate(objective_id: str, repo_root: Path | None, fn) -> Objective:
    objectives = load(repo_root)
    objective = objectives.get(objective_id)
    if objective is None:
        raise ObjectiveError(f"no objective {objective_id!r}")
    fn(objective)
    save(objectives, repo_root)
    return objective


def pause(objective_id: str, repo_root: Path | None = None) -> Objective:
    def _apply(o: Objective) -> None:
        if o.state not in (State.ACTIVE, State.PAUSED):
            raise ObjectiveError(f"{o.id} is {o.state}; only an active objective pauses")
        o.state = State.PAUSED

    return _mutate(objective_id, repo_root, _apply)


def resume(objective_id: str, repo_root: Path | None = None) -> Objective:
    def _apply(o: Objective) -> None:
        if o.state != State.PAUSED:
            raise ObjectiveError(f"{o.id} is {o.state}; only a paused objective resumes")
        exhausted = o.exhausted()
        if exhausted:
            raise ObjectiveError(
                f"{o.id} cannot resume: {exhausted}. Raise the ceiling deliberately "
                f"by creating a new objective."
            )
        o.state = State.ACTIVE

    return _mutate(objective_id, repo_root, _apply)


def cancel(objective_id: str, reason: str = "", repo_root: Path | None = None) -> Objective:
    def _apply(o: Objective) -> None:
        o.state = State.STOPPED
        o.stop_reason = reason or "cancelled by the operator"

    return _mutate(objective_id, repo_root, _apply)


def complete(objective_id: str, repo_root: Path | None = None) -> Objective:
    def _apply(o: Objective) -> None:
        o.state = State.DONE
        o.stop_reason = "objective met"

    return _mutate(objective_id, repo_root, _apply)


# ---------------------------------------------------------------------------
# Driving
# ---------------------------------------------------------------------------


def claim_turn(
    objective_id: str, *, repo_root: Path | None = None, now: float | None = None
) -> Objective:
    """Take a turn against an objective, or stop it at its ceiling.

    The ceiling is checked BEFORE the turn is counted. An objective that
    notices it is over budget after spending has not been bounded — it has been
    observed.

    Raises:
        ObjectiveError: the objective is not runnable. The reason says why.
    """
    objectives = load(repo_root)
    objective = objectives.get(objective_id)
    if objective is None:
        raise ObjectiveError(f"no objective {objective_id!r}")

    if objective.state != State.ACTIVE:
        raise ObjectiveError(f"{objective.id} is {objective.state}")
    if objective.waiting_on:
        raise ObjectiveError(
            f"{objective.id} is waiting on oversight {objective.waiting_on}"
        )

    exhausted = objective.exhausted(now)
    if exhausted:
        objective.state = State.STOPPED
        objective.stop_reason = exhausted
        save(objectives, repo_root)
        logger.info("objectives: %s stopped — %s", objective.id, exhausted)
        raise ObjectiveError(f"{objective.id} stopped: {exhausted}")

    objective.consumption.turns += 1
    save(objectives, repo_root)
    return objective


def record_usage(
    objective_id: str, tokens: int, *, repo_root: Path | None = None
) -> Objective:
    """Attribute token spend to an objective, stopping it if that exhausts it."""
    objectives = load(repo_root)
    objective = objectives.get(objective_id)
    if objective is None:
        raise ObjectiveError(f"no objective {objective_id!r}")
    objective.consumption.tokens += max(0, int(tokens))
    exhausted = objective.exhausted()
    if exhausted and objective.state == State.ACTIVE:
        objective.state = State.STOPPED
        objective.stop_reason = exhausted
    save(objectives, repo_root)
    return objective


def block_on_oversight(
    objective_id: str, oversight_id: str, *, repo_root: Path | None = None
) -> Objective:
    """Park an objective behind a governed decision.

    The objective WAITS. It does not escalate, and it does not proceed: an
    objective that could bypass a gate would make "pursue this across turns" a
    way to obtain approval-free execution by phrasing.
    """

    def _apply(o: Objective) -> None:
        o.waiting_on = oversight_id

    objective = _mutate(objective_id, repo_root, _apply)
    logger.info(
        "objectives: %s waiting on oversight %s (gated actions stay gated)",
        objective_id, oversight_id,
    )
    return objective


def unblock(objective_id: str, *, repo_root: Path | None = None) -> Objective:
    """Clear the wait after the decision was made."""

    def _apply(o: Objective) -> None:
        o.waiting_on = ""

    return _mutate(objective_id, repo_root, _apply)


def active(repo_root: Path | None = None) -> list[Objective]:
    return [o for o in load(repo_root).values() if o.state == State.ACTIVE]


def runnable(repo_root: Path | None = None) -> list[Objective]:
    return [o for o in load(repo_root).values() if o.runnable()]
