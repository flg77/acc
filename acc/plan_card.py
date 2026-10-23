"""The plan card -- the assistant's plan is the mission (vault proposal 059,
OpenSpec ``20261018-plan-card``).

Under ``ASK_ONCE`` the operator answers ONE question per piece of work.  058
asked it at the first gated call ("this call is shell_exec ...; allowing the
mission also covers ..."), which approves a scope without showing a plan.  Here
the question is the plan itself: which roles the work uses -- reused, infused
or authored -- which packs it installs and which hosts it needs.  Approving it
creates the :class:`acc.mission.Mission` and dispatches exactly the proposals
the plan holds; nothing the model says afterwards is approved by implication.

A reply may carry the plan explicitly::

    [PROPOSE_PLAN:{"goal": "...", "steps": [{"role": "devops_engineer",
      "staffing": "reuse", "does": "audit the lab servers"}], "hosts": [...]}]

When it does not (small models rarely do), the plan is built from the reply's
own proposals, one step per proposal, so the card never depends on the model
remembering a marker.

What a plan never does: widen a role's grants or the requester's ceiling (both
are checked before a proposal reaches here), cover CRITICAL calls, calls made
in the operator's name, or non-file destructive calls (``acc.mission``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Iterable

from acc.question import Question, QuestionOption

logger = logging.getLogger("acc.plan_card")

STAFFING = ("reuse", "infuse", "author")

#: The proposal kinds a plan holds.  Everything else keeps its own path.
STAFFING_KINDS: frozenset[str] = frozenset({
    "spawn", "route", "infuse",
    "lifecycle_scale", "lifecycle_pause", "lifecycle_resume", "lifecycle_stop",
})

#: Answer keys of the plan card.
ANSWER_PLAN = "1"     # allow the plan: a mission covers its steps and follow-ups
ANSWER_STEPS = "2"    # run these steps; later system calls ask again
ANSWER_REFUSE = "3"

_MAX_STEPS = 12
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?(?::\d{1,5})?$")
_MARKER = "[PROPOSE_PLAN:"


@dataclass
class PlanStep:
    role: str
    staffing: str = "reuse"
    does: str = ""
    pack: str = ""
    #: Installed roles that are not running -- ACC's own fact, never the model's.
    installed_idle: list[str] = field(default_factory=list)


@dataclass
class Plan:
    goal: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    #: True when ACC built the plan from the reply's proposals.
    derived: bool = False

    @property
    def roles(self) -> set[str]:
        return {s.role for s in self.steps if s.role}

    @property
    def packs(self) -> set[str]:
        return {s.pack for s in self.steps if s.pack}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Plan":
        data = dict(data or {})
        known = {f.name for f in fields(PlanStep)}
        steps = [PlanStep(**{k: v for k, v in dict(s).items() if k in known})
                 for s in data.get("steps") or [] if isinstance(s, dict)]
        return cls(goal=str(data.get("goal") or ""), steps=steps,
                   hosts=[str(h) for h in data.get("hosts") or []],
                   derived=bool(data.get("derived")))

    def digest(self) -> str:
        """A stable hash of what was approved (the ledger's plan hash)."""
        body = json.dumps({"goal": self.goal, "steps": [asdict(s) for s in self.steps],
                           "hosts": sorted(self.hosts)}, sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Parse / derive / validate
# ---------------------------------------------------------------------------


def _json_object_at(text: str, start: int) -> str | None:
    """The balanced ``{...}`` beginning at *start*, string-aware."""
    if start >= len(text) or text[start] != "{":
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_plan(text: str) -> Plan | None:
    """The first ``[PROPOSE_PLAN:{json}]`` in *text*, validated, or None."""
    pos = (text or "").find(_MARKER)
    if pos < 0:
        return None
    blob = _json_object_at(text, pos + len(_MARKER))
    if blob is None:
        logger.warning("plan_card: PROPOSE_PLAN without a complete JSON object")
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        logger.warning("plan_card: PROPOSE_PLAN JSON did not parse")
        return None
    if not isinstance(data, dict):
        return None
    return validate(Plan.from_dict(data))


def validate(plan: Plan) -> Plan:
    """Normalise *plan*: known staffing values, well-formed hosts, a step cap.
    Never raises; a bad field is dropped, not guessed."""
    steps: list[PlanStep] = []
    for s in plan.steps[:_MAX_STEPS]:
        role = str(s.role or "").strip()
        if not role:
            continue
        staffing = s.staffing if s.staffing in STAFFING else ("infuse" if s.pack else "reuse")
        steps.append(PlanStep(role=role, staffing=staffing, does=str(s.does or "").strip()[:200],
                              pack=str(s.pack or "").strip(), installed_idle=list(s.installed_idle)))
    hosts = sorted({h.strip() for h in plan.hosts if _HOST_RE.match(h.strip())})
    return Plan(goal=str(plan.goal or "").strip()[:300], steps=steps, hosts=hosts,
                derived=plan.derived)


def _role_of(p: Any) -> str:
    params = getattr(p, "params", None) or {}
    return str(params.get("target_role") or params.get("role") or "")


def derive(proposals: Iterable[Any], goal: str = "") -> Plan:
    """A plan built from the reply's proposals, one step per staffing proposal."""
    steps: list[PlanStep] = []
    for p in proposals:
        kind = getattr(p, "kind", "")
        params = getattr(p, "params", None) or {}
        if kind == "infuse":
            steps.append(PlanStep(role="", staffing="infuse", pack=str(params.get("name") or ""),
                                  does=str(getattr(p, "rationale", "") or "")[:200]))
        elif kind in STAFFING_KINDS and _role_of(p):
            steps.append(PlanStep(role=_role_of(p), staffing="reuse",
                                  does=str(getattr(p, "rationale", "") or "")[:200]))
    # An infuse step has no role yet; keep it, the pack names the step.
    plan = Plan(goal=goal, steps=[s for s in steps if s.role or s.pack], derived=True)
    plan.steps = plan.steps[:_MAX_STEPS]
    return plan


def annotate_installed_idle(plan: Plan, snapshot: Any) -> Plan:
    """Name, on every infuse step, the installed roles that are not running.

    The staffing model puts reuse before infuse and models skip it (the
    chief-of-staff A/B, prompt P2).  This is ACC's fact, not the model's:
    the snapshot's available roles minus the running roster minus the control
    roles.  Whether one of them covers the step is the operator's call."""
    if snapshot is None:
        return plan
    from acc.pkg.role_resolution import CONTROL_ROLES  # noqa: PLC0415
    running = set((getattr(snapshot, "roster", None) or {}).keys())
    idle = sorted(
        str(r.get("name")) for r in getattr(snapshot, "available_roles", None) or []
        if r.get("name") and r.get("name") not in running and r.get("name") not in CONTROL_ROLES
    )[:8]
    for s in plan.steps:
        if s.staffing == "infuse" or s.pack:
            s.installed_idle = idle
    return plan


# ---------------------------------------------------------------------------
# Coverage and amendments
# ---------------------------------------------------------------------------


def covers(plan: Plan, proposal: Any) -> str:
    """``""`` when *plan* covers *proposal*, else what is new about it."""
    kind = getattr(proposal, "kind", "")
    params = getattr(proposal, "params", None) or {}
    if kind == "infuse":
        name = str(params.get("name") or "")
        return "" if name in plan.packs else f"pack {name} is not in the plan"
    if kind in STAFFING_KINDS:
        role = _role_of(proposal)
        if role in plan.roles:
            return ""
        # A role an infused pack provides is in the plan although its name was
        # not known when the plan was approved.
        if plan.packs and _pack_of_role(role) in plan.packs:
            return ""
        return f"role {role} is not in the plan"
    return ""


def _pack_of_role(role: str) -> str:
    """The installed package that provides *role* ("" for in-tree or unknown)."""
    try:
        from acc.pkg.role_resolution import list_installed_roles  # noqa: PLC0415
        src = list_installed_roles().get(role)
        return str(src.package.name) if src is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def merge(plan: Plan, extra: Plan) -> Plan:
    """*plan* extended by an approved amendment."""
    steps = list(plan.steps) + [s for s in extra.steps
                                if (s.role, s.pack) not in {(t.role, t.pack) for t in plan.steps}]
    return Plan(goal=plan.goal or extra.goal, steps=steps[:_MAX_STEPS],
                hosts=sorted(set(plan.hosts) | set(extra.hosts)), derived=plan.derived)


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------


def _step_line(i: int, s: PlanStep) -> str:
    who = s.role or (s.pack and f"(from {s.pack})") or "?"
    how = {"reuse": "reuse", "infuse": f"install {s.pack}" if s.pack else "install", "author": "author"}
    line = f"{i}. {who}: {how.get(s.staffing, s.staffing)}"
    if s.does:
        line += f" -- {s.does}"
    if s.installed_idle:
        line += f" [installed, idle: {', '.join(s.installed_idle)}]"
    return line


def plan_question(plan: Plan, *, amendment: bool = False, workspace: str = "") -> Question:
    """The plan card: one question for the whole plan (or for what an
    amendment adds)."""
    head = "Amend the plan? These steps are new:" if amendment else "Plan -- confirm once, then I get it done:"
    lines = [head]
    if plan.goal:
        lines.append(f"Goal: {plan.goal}")
    lines += [_step_line(i, s) for i, s in enumerate(plan.steps, 1)]
    if plan.hosts:
        lines.append(f"Hosts: {', '.join(plan.hosts)}")
    where = workspace or "the workspace"
    return Question(
        text="\n".join(lines),
        options=(
            QuestionOption(
                key=ANSWER_PLAN, label="allow the plan" if not amendment else "add to the plan",
                proceeds=True,
                detail=(
                    "the steps run without asking again, follow-ups included",
                    f"system access inside {where} runs without asking (the mission)",
                    "still asked: CRITICAL calls, sending or posting for you, non-file deletions",
                    "a workspace rm is backed up first and reported when the mission closes",
                ),
            ),
            QuestionOption(
                key=ANSWER_STEPS, label="just these steps", proceeds=True,
                detail=("the steps run once", "later system calls ask again"),
            ),
            QuestionOption(
                key=ANSWER_REFUSE, label="don't do it", proceeds=False,
                detail=("nothing runs", "-> the assistant is told you refused the plan"),
            ),
        ),
    )


def summary_line(plan: Plan, *, amendment: bool = False) -> str:
    roles = ", ".join(sorted(plan.roles)) or "-"
    packs = ", ".join(sorted(plan.packs))
    head = "Plan amendment" if amendment else "Plan"
    out = f"{head}: {len(plan.steps)} step(s), roles {roles}"
    if packs:
        out += f", installs {packs}"
    if plan.hosts:
        out += f", hosts {', '.join(plan.hosts)}"
    return out


# ---------------------------------------------------------------------------
# Durable record and learning
# ---------------------------------------------------------------------------

EVENT_APPROVED = "PLAN_APPROVED"
EVENT_AMENDED = "PLAN_AMENDED"
EVENT_CLOSED = "PLAN_CLOSED"


def planned_vs_actual(plan: Plan, calls: Iterable[dict], roles_used: Iterable[str]) -> dict:
    """Which planned roles ran, which never ran, and which ran unplanned."""
    used = set(roles_used) | {str(c.get("role") or "") for c in calls}
    used.discard("")
    planned = plan.roles
    return {
        "planned": sorted(planned),
        "ran": sorted(planned & used),
        "skipped": sorted(planned - used),
        "unplanned": sorted(used - planned - {"assistant"}),
    }


def record(mission_id: str, event: str, *, plan: Plan, by: str = "", **fields_: Any) -> None:
    """One plan event in the mission's own tracelog session (``mission-<id>``).
    Best-effort, like every tracelog write."""
    try:
        from acc import tracelog  # noqa: PLC0415
        tracelog.emit(f"mission-{mission_id}", tracelog.KIND_PLAN, mission_id=mission_id,
                      event=event, by=by, plan=plan.to_dict(), plan_hash=plan.digest(), **fields_)
    except Exception:  # noqa: BLE001
        logger.debug("plan_card: tracelog record failed", exc_info=True)


def lesson_candidates(closed: Iterable[dict], *, min_missions: int = 3, ratio: float = 0.6) -> list[dict]:
    """Recurring planned-vs-actual differences, as lesson *candidates*.

    *closed* are ``PLAN_CLOSED`` records.  A role planned in at least
    ``min_missions`` missions and skipped in at least ``ratio`` of them is a
    candidate ("planned but not needed"); so is a role that ran unplanned that
    often ("needed but not planned").  Candidates are proposals for a person;
    nothing here applies one."""
    planned: dict[str, int] = {}
    skipped: dict[str, int] = {}
    unplanned: dict[str, int] = {}
    refs: dict[str, list[str]] = {}
    total = 0
    seen: set[str] = set()
    for rec in closed:
        mid = str(rec.get("mission_id") or "")
        # Every agent hears a close and may record it; count a mission once.
        if mid and mid in seen:
            continue
        seen.add(mid)
        pva = rec.get("planned_vs_actual") or {}
        total += 1
        for r in pva.get("planned", []):
            planned[r] = planned.get(r, 0) + 1
        for r in pva.get("skipped", []):
            skipped[r] = skipped.get(r, 0) + 1
            refs.setdefault(f"skipped:{r}", []).append(mid)
        for r in pva.get("unplanned", []):
            unplanned[r] = unplanned.get(r, 0) + 1
            refs.setdefault(f"unplanned:{r}", []).append(mid)
    out: list[dict] = []
    for r, n in sorted(skipped.items()):
        if planned.get(r, 0) >= min_missions and n / planned[r] >= ratio:
            out.append({"kind": "planned_not_needed", "role": r, "missions": n,
                        "of": planned[r], "evidence": refs[f"skipped:{r}"]})
    for r, n in sorted(unplanned.items()):
        if total >= min_missions and n >= min_missions and n / total >= ratio:
            out.append({"kind": "needed_not_planned", "role": r, "missions": n,
                        "of": total, "evidence": refs[f"unplanned:{r}"]})
    return out
