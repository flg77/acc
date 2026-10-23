"""Mission grants -- "ask once, then get things done" (vault proposal 058, Phase 1).

Under ``ASK_ONCE`` the first gated call of a thread asks ONE question, the
mission card.  Allowed, it becomes a :class:`Mission`: every later call in the
same thread that the mission covers runs without asking, recorded as an
``AUTO_APPROVED`` row whose approver is ``policy:mission:<id>``.  The thread is
the task's session and its task id, so a hand-off, an infuse continuation and
the operator's follow-up prompts are covered too (058 Q3: "includes
follow-ups").

What a mission does NOT change:

* the category ceiling (D-014) and the role's own grants -- both are checked
  before the mission is consulted, so a mission can only spare a question,
  never widen what a role may do;
* CRITICAL calls and calls that act in the operator's name (send, post,
  publish) -- still asked, every time;
* destructive calls -- still asked, except a workspace ``rm`` through
  ``shell_exec``: that one is **backed up first** into the mission's
  quarantine and then runs (058 Q1: "an rm is not executed without a
  backup").  Closing the mission reports every quarantined item; the
  operator's purge or restore is the final allowance.

A mission ends when it is closed or revoked, when its call budget is used up,
or when it expires (``ACC_MISSION_MAX_CALLS``, ``ACC_MISSION_TTL_S``).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable, Optional

from acc.question import Question, QuestionOption
from acc.redis_compat import call_redis as _call_redis

logger = logging.getLogger("acc.mission")

STATUS_ACTIVE = "ACTIVE"
STATUS_CLOSED = "CLOSED"
STATUS_REVOKED = "REVOKED"
STATUS_EXHAUSTED = "EXHAUSTED"
STATUS_EXPIRED = "EXPIRED"

#: The gate categories a mission answers.  ``acts_on_behalf`` (send, post,
#: publish) stays a question per call: it speaks in the operator's name.
COVERED_CATEGORIES: frozenset[str] = frozenset({"system_access"})

#: Answer keys of the mission card.
ANSWER_MISSION = "1"
ANSWER_ONCE = "2"
ANSWER_REFUSE = "3"
#: 060 -- "always allow" on the mission card: a standing grant for this role
#: and target instead of a mission (the operator: granted once by the user).
ANSWER_ALWAYS = "4"

QUARANTINE_DIR = ".acc-quarantine"

_DEFAULT_MAX_CALLS = 200
_DEFAULT_TTL_S = 8 * 3600


def _max_calls() -> int:
    try:
        return max(1, int(os.environ.get("ACC_MISSION_MAX_CALLS", "") or _DEFAULT_MAX_CALLS))
    except ValueError:
        return _DEFAULT_MAX_CALLS


def _ttl_s() -> int:
    try:
        return max(60, int(os.environ.get("ACC_MISSION_TTL_S", "") or _DEFAULT_TTL_S))
    except ValueError:
        return _DEFAULT_TTL_S


def thread_keys(task_payload: dict | None = None, *, session_id: str = "", task_id: str = "") -> list[str]:
    """The keys that name a thread: its session and its task id, deduplicated.
    A continuation or a hand-off keeps the task id; a follow-up prompt keeps
    the session."""
    data = task_payload or {}
    keys = [str(session_id or data.get("session_id") or ""), str(task_id or data.get("task_id") or "")]
    out: list[str] = []
    for k in keys:
        if k and k not in out:
            out.append(k)
    return out


@dataclass
class Mission:
    """One approved "ask once" grant."""

    mission_id: str
    threads: list[str]
    principal: str
    approver: str
    workspace: str
    created_ms: int
    expires_ms: int
    max_calls: int
    used_calls: int = 0
    status: str = STATUS_ACTIVE
    roles_used: list[str] = field(default_factory=list)
    #: Calls the mission answered: ``{ts_ms, role, kind, target, oversight_id}``.
    calls: list[dict] = field(default_factory=list)
    #: Backed-up deletions: ``{ts_ms, path, kept_at, role}`` (relative paths).
    quarantine: list[dict] = field(default_factory=list)
    closed_ms: int = 0
    closed_by: str = ""
    #: The approved plan (``acc.plan_card.Plan.to_dict()``); empty for a
    #: mission raised by the first gated call (058).  Vault 059.
    plan: dict = field(default_factory=dict)

    def live_status(self, now_ms: int | None = None) -> str:
        """The status with expiry and budget applied."""
        if self.status != STATUS_ACTIVE:
            return self.status
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        if now_ms >= self.expires_ms:
            return STATUS_EXPIRED
        if self.used_calls >= self.max_calls:
            return STATUS_EXHAUSTED
        return STATUS_ACTIVE

    @property
    def active(self) -> bool:
        return self.live_status() == STATUS_ACTIVE

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Mission":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class MissionStore:
    """Missions beside the oversight queue: in Redis when the queue has it,
    in process otherwise.  Mission keys carry no TTL; a mission ends by its
    own status, and a closed one stays as history (its quarantine report)."""

    _KEY = "acc:{cid}:mission:{mid}"
    _KEY_THREAD = "acc:{cid}:mission_thread:{thread}"
    _KEY_ALL = "acc:{cid}:missions"

    def __init__(self, redis_client: Any = None, collective_id: str = "sol-01") -> None:
        self._redis = redis_client
        self._cid = collective_id
        self._mem: dict[str, Mission] = {}
        self._mem_threads: dict[str, str] = {}

    @classmethod
    def for_queue(cls, queue: Any) -> "MissionStore":
        """The store sharing *queue*'s backend (one per queue object)."""
        store = getattr(queue, "_mission_store", None)
        if store is None:
            store = cls(getattr(queue, "_redis", None), getattr(queue, "_cid", "sol-01"))
            try:
                queue._mission_store = store
            except Exception:  # noqa: BLE001 -- a queue that refuses attributes
                pass
        return store

    async def save(self, m: Mission) -> None:
        if self._redis is not None:
            try:
                await _call_redis(self._redis.set, self._KEY.format(cid=self._cid, mid=m.mission_id),
                                  json.dumps(m.to_dict()))
                await _call_redis(self._redis.sadd, self._KEY_ALL.format(cid=self._cid), m.mission_id)
                for t in m.threads:
                    await _call_redis(self._redis.set, self._KEY_THREAD.format(cid=self._cid, thread=t),
                                      m.mission_id)
                return
            except Exception as exc:  # noqa: BLE001
                logger.error("mission: Redis save failed: %s", exc)
        self._mem[m.mission_id] = m
        for t in m.threads:
            self._mem_threads[t] = m.mission_id

    async def get(self, mission_id: str) -> Optional[Mission]:
        if self._redis is not None:
            try:
                raw = await _call_redis(self._redis.get, self._KEY.format(cid=self._cid, mid=mission_id))
                return Mission.from_dict(json.loads(raw)) if raw else None
            except Exception as exc:  # noqa: BLE001
                logger.error("mission: Redis load failed: %s", exc)
        return self._mem.get(mission_id)

    async def for_threads(self, threads: Iterable[str]) -> Optional[Mission]:
        """The ACTIVE mission of any of *threads*, or None."""
        for t in threads:
            if not t:
                continue
            mid = None
            if self._redis is not None:
                try:
                    mid = await _call_redis(self._redis.get, self._KEY_THREAD.format(cid=self._cid, thread=t))
                    mid = mid.decode() if isinstance(mid, bytes) else mid
                except Exception as exc:  # noqa: BLE001
                    logger.error("mission: Redis thread lookup failed: %s", exc)
            else:
                mid = self._mem_threads.get(t)
            if mid:
                m = await self.get(str(mid))
                if m is not None and m.active:
                    return m
        return None

    async def all(self) -> list[Mission]:
        ids: list[str] = []
        if self._redis is not None:
            try:
                raw = await _call_redis(self._redis.smembers, self._KEY_ALL.format(cid=self._cid))
                ids = [i.decode() if isinstance(i, bytes) else str(i) for i in raw or ()]
            except Exception as exc:  # noqa: BLE001
                logger.error("mission: Redis list failed: %s", exc)
        else:
            ids = list(self._mem)
        out = [m for m in [await self.get(i) for i in ids] if m is not None]
        out.sort(key=lambda m: m.created_ms, reverse=True)
        return out

    async def create(
        self, threads: list[str], *, principal: str, approver: str, workspace: str,
        plan: dict | None = None,
    ) -> Mission:
        now = int(time.time() * 1000)
        m = Mission(
            mission_id=f"m-{uuid.uuid4().hex[:12]}",
            threads=[t for t in threads if t],
            principal=principal or "",
            approver=approver or "",
            workspace=workspace,
            created_ms=now,
            expires_ms=now + _ttl_s() * 1000,
            max_calls=_max_calls(),
            plan=dict(plan or {}),
        )
        await self.save(m)
        logger.warning("mission: %s allowed by %s for threads %s", m.mission_id, approver, m.threads)
        return m

    async def end(self, mission_id: str, *, status: str, by: str) -> Optional[Mission]:
        """Close or revoke a mission.  Its record stays (the report)."""
        m = await self.get(mission_id)
        if m is None:
            return None
        if m.status == STATUS_ACTIVE:
            m.status = status
            m.closed_ms = int(time.time() * 1000)
            m.closed_by = by
            await self.save(m)
            if m.plan:
                # 059: what was planned next to what ran, in the durable record.
                from acc import plan_card  # noqa: PLC0415
                plan = plan_card.Plan.from_dict(m.plan)
                plan_card.record(
                    m.mission_id, plan_card.EVENT_CLOSED, plan=plan, by=by, status=status,
                    planned_vs_actual=plan_card.planned_vs_actual(plan, m.calls, m.roles_used),
                    calls=len(m.calls), quarantined=len(m.quarantine),
                )
        return m


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def covers(
    m: Mission, *, kind: str, target: str, args: dict, categories: frozenset[str], risk_level: str,
) -> str:
    """``""`` when *m* covers the call, else the reason it does not."""
    status = m.live_status()
    if status != STATUS_ACTIVE:
        return f"mission {m.mission_id} is {status.lower()}"
    if str(risk_level).upper() == "CRITICAL":
        return "CRITICAL calls are asked every time"
    outside = set(categories) - COVERED_CATEGORIES
    if outside:
        return f"{', '.join(sorted(outside))} is asked every time"
    root = Path(m.workspace).resolve() if m.workspace else None
    for key in ("cwd", "path"):
        value = (args or {}).get(key)
        if not value:
            continue
        if root is None:
            return f"{key} given and the mission has no workspace"
        p = Path(str(value))
        resolved = (p if p.is_absolute() else root / p).resolve()
        if not _within(resolved, root):
            return f"{key} {value!r} is outside the mission's workspace"
    return ""


def mission_question(role: str, workspace: str, *, kind: str, target: str) -> Question:
    """The mission card: the one question of an "ask once" thread."""
    where = workspace or "(no workspace)"
    return Question(
        text=(
            f"Ask once: let {role or 'the agent'} get this done? This call is "
            f"{kind} {target}; allowing the mission also covers this thread's "
            f"later system-access calls inside {where}."
        ),
        options=(
            QuestionOption(
                key=ANSWER_MISSION, label="allow the mission", proceeds=True,
                detail=(
                    f"system access inside {where} runs without asking, for this thread and its follow-ups",
                    "any role ACC brings into the thread, each only within its own grants",
                    f"up to {_max_calls()} calls or {_ttl_s() // 3600} h; close or revoke it any time (/mission)",
                    "a workspace rm is backed up first and reported when the mission closes",
                    "still asked: CRITICAL calls, sending or posting for you, other deletions",
                ),
            ),
            QuestionOption(
                key=ANSWER_ONCE, label="just this call", proceeds=True,
                detail=("runs once; the next call asks again",),
            ),
            QuestionOption(
                key=ANSWER_REFUSE, label="don't run it", proceeds=False,
                detail=("nothing runs", "-> the agent is told you refused it"),
            ),
            QuestionOption(
                key=ANSWER_ALWAYS, label=f"always allow {kind} {target} for {role or 'the agent'}",
                proceeds=True,
                detail=(f"{role or 'the agent'} may run {kind} {target} without asking, in every thread",
                        "a standing grant in your name; revoke with acc-cli grants revoke <id>",
                        "no mission: other calls in this thread still ask"),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Soft delete (058 Q1)
# ---------------------------------------------------------------------------


def soft_delete_targets(kind: str, target: str, args: dict, workspace: str) -> Optional[list[Path]]:
    """The workspace paths a ``shell_exec`` ``rm`` would delete, or None when
    the call is not one that can be backed up first (then it is asked).

    ``shell_exec`` runs an argv with no shell, so the targets are exactly the
    non-flag arguments: no globs, no pipes, no substitutions to guess at."""
    if kind != "skill" or target != "shell_exec" or not workspace:
        return None
    try:
        from acc.skills.skill_runtime import resolve_argv  # noqa: PLC0415
        argv = resolve_argv(args, skill="shell_exec")
    except Exception:  # noqa: BLE001
        return None
    if not argv or Path(argv[0]).name != "rm":
        return None
    root = Path(workspace).resolve()
    cwd = Path(str(args.get("cwd") or root))
    cwd = (cwd if cwd.is_absolute() else root / cwd).resolve()
    if not _within(cwd, root):
        return None
    paths: list[Path] = []
    options_ended = False
    for a in argv[1:]:
        if a == "--" and not options_ended:
            options_ended = True
            continue
        if a.startswith("-") and not options_ended:
            continue
        p = Path(a)
        p = (p if p.is_absolute() else cwd / p).resolve()
        if p == root or not _within(p, root) or not p.exists():
            return None
        if _within(p, root / QUARANTINE_DIR) or (root / QUARANTINE_DIR) in p.parents or p == root / QUARANTINE_DIR:
            return None
        paths.append(p)
    return paths or None


def back_up(m: Mission, paths: list[Path], *, role: str = "") -> list[dict]:
    """Copy *paths* into the mission's quarantine before they are deleted.
    Returns the entries added to ``m.quarantine`` (the caller saves *m*)."""
    root = Path(m.workspace).resolve()
    stamp = int(time.time() * 1000)
    base = root / QUARANTINE_DIR / m.mission_id / str(stamp)
    entries: list[dict] = []
    for p in paths:
        rel = p.relative_to(root)
        dest = base / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if p.is_dir():
            shutil.copytree(p, dest, symlinks=True)
        else:
            shutil.copy2(p, dest, follow_symlinks=False)
        entries.append({
            "ts_ms": stamp, "path": rel.as_posix(),
            "kept_at": dest.relative_to(root).as_posix(), "role": role,
        })
    m.quarantine.extend(entries)
    return entries


def purge(m: Mission) -> int:
    """The operator's final allowance: delete the quarantine.  Returns how
    many items were removed."""
    q = Path(m.workspace).resolve() / QUARANTINE_DIR / m.mission_id
    n = len(m.quarantine)
    if q.exists():
        shutil.rmtree(q)
    return n


def restore(m: Mission) -> list[str]:
    """Put every quarantined item back where it was (never over an existing
    path).  Returns the paths restored."""
    root = Path(m.workspace).resolve()
    done: list[str] = []
    for e in m.quarantine:
        src = root / e["kept_at"]
        dest = root / e["path"]
        if not src.exists() or dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest, symlinks=True)
        else:
            shutil.copy2(src, dest, follow_symlinks=False)
        done.append(e["path"])
    return done


def report(m: Mission) -> list[str]:
    """The closing report, as lines: what the mission ran and what it kept."""
    lines = [
        f"mission {m.mission_id} — {m.live_status()} · {m.used_calls}/{m.max_calls} calls · "
        f"allowed by {m.approver or '?'} for {m.principal or 'the operator'}",
        f"roles: {', '.join(m.roles_used) or '(none yet)'} · workspace: {m.workspace or '(none)'}",
    ]
    if m.plan:
        # 059: the approved plan, and what ran against it.
        from acc import plan_card  # noqa: PLC0415
        plan = plan_card.Plan.from_dict(m.plan)
        pva = plan_card.planned_vs_actual(plan, m.calls, m.roles_used)
        lines.append(plan_card.summary_line(plan) + (f" · goal: {plan.goal}" if plan.goal else ""))
        lines.append(f"planned vs actual: ran {', '.join(pva['ran']) or '-'} · "
                     f"skipped {', '.join(pva['skipped']) or '-'} · "
                     f"unplanned {', '.join(pva['unplanned']) or '-'}")
    if m.quarantine:
        lines.append(f"{len(m.quarantine)} deletion(s) backed up — still recoverable until you purge:")
        lines += [f"  {e['path']}  ->  {e['kept_at']}" for e in m.quarantine]
        lines.append("final allowance: /mission purge <id> deletes them; /mission restore <id> puts them back")
    else:
        lines.append("no deletions")
    return lines
