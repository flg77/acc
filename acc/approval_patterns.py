"""Repeated approvals become a *proposal*, never a policy.

An operator who has approved the same thing twenty times is telling you
something. The temptation is to have the system act on it. That temptation is
the hole: **an allowlist that grows itself is not governance.**

So this module observes and proposes. It contains no path that narrows policy,
and a test asserts that by inspecting its surface — because the function added
later "to close the loop" is exactly how an advisory system becomes an
automatic one, and by then nobody remembers it was deliberate that it wasn't.

Three rules make the proposals worth reading rather than noise:

* **Evidence, not suggestion.** A proposal cites the decisions, the approvers
  and the window, so a reviewer is judging a claim they can check.
* **Never generalise.** A pattern proposes *exactly* what was repeatedly
  approved — never a broader rule inferred from it. Widening is the reviewer's
  call to make, with the narrow version in front of them.
* **A rejection sticks.** The same evidence does not raise the same proposal
  again; re-asking until someone says yes is how consent gets manufactured.

CRITICAL decisions are excluded outright. The whole point of that tier is that
a human looks every time, and a pattern that could relax it would remove the
only control that tier has.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from acc._atomic_write import atomic_write_text

logger = logging.getLogger("acc.approval_patterns")

STATE_PATH_VAR = "ACC_APPROVAL_PATTERNS_PATH"
DEFAULT_STATE_FILE = "approval-patterns.json"

#: How many consistent approvals before a pattern is worth proposing.
#: Conservative on purpose: a proposal an operator dismisses is worse than no
#: proposal, because it teaches them to dismiss the next one unread.
DEFAULT_THRESHOLD = 10

#: And over at least this long, so twenty approvals in one busy afternoon do
#: not look like a settled habit.
DEFAULT_MIN_WINDOW_DAYS = 7.0

#: Never proposed for. A human looks at these every time; that IS the control.
EXCLUDED_RISK = frozenset({"CRITICAL"})


class PatternError(Exception):
    """A pattern operation was refused. Operator-facing."""


@dataclass
class Decision:
    """One recorded oversight decision."""

    oversight_id: str
    kind: str
    subject: str            # what was being decided about
    approved: bool
    approver: str
    risk_level: str = ""
    at: float = 0.0

    def signature(self) -> str:
        """What makes two decisions 'the same thing'."""
        return f"{self.kind}:{self.subject}"


@dataclass
class Pattern:
    """A repeated, consistent approval — and the evidence for it."""

    kind: str
    subject: str
    count: int
    approvers: tuple[str, ...]
    first_at: float
    last_at: float
    decision_ids: tuple[str, ...]
    risk_level: str = ""

    @property
    def window_days(self) -> float:
        return max(0.0, (self.last_at - self.first_at) / 86400)

    def evidence_hash(self) -> str:
        """Identifies THIS evidence, so a rejection can stick to it."""
        payload = json.dumps(
            {
                "kind": self.kind,
                "subject": self.subject,
                "decisions": sorted(self.decision_ids),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def as_proposal(self) -> dict[str, Any]:
        """The proposal a human reviews. Never applied by anything here."""
        return {
            "kind": "policy_narrowing",
            "risk_level": "HIGH",
            "summary": (
                f"pre-approve {self.kind} for {self.subject!r} — approved "
                f"{self.count} times over {self.window_days:.0f} days"
            ),
            "rationale": (
                "This is a PROPOSAL, not a change. Nothing narrows policy until "
                "a human approves it. The narrowing proposed is exactly what was "
                "repeatedly approved — no broader rule has been inferred."
            ),
            "evidence": {
                "kind": self.kind,
                "subject": self.subject,
                "approvals": self.count,
                "approvers": sorted(set(self.approvers)),
                "window_days": round(self.window_days, 1),
                "first_at": self.first_at,
                "last_at": self.last_at,
                "decision_ids": sorted(self.decision_ids),
            },
            "evidence_hash": self.evidence_hash(),
        }


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect(
    decisions: Iterable[Decision],
    *,
    threshold: int = DEFAULT_THRESHOLD,
    min_window_days: float = DEFAULT_MIN_WINDOW_DAYS,
) -> list[Pattern]:
    """Find repeated, *consistent* approvals worth proposing.

    Consistency is the point: a subject approved nineteen times and refused
    once is not a settled habit, it is a judgement someone still exercises.
    One rejection disqualifies the pattern entirely.
    """
    grouped: dict[str, list[Decision]] = {}
    for decision in decisions:
        grouped.setdefault(decision.signature(), []).append(decision)

    patterns: list[Pattern] = []
    for signature, group in sorted(grouped.items()):
        if any(not d.approved for d in group):
            continue  # a single refusal means this is still being judged
        if any(d.risk_level.upper() in EXCLUDED_RISK for d in group):
            continue  # CRITICAL is looked at every time; that is the control
        if len(group) < threshold:
            continue

        stamps = sorted(d.at for d in group if d.at)
        first, last = (stamps[0], stamps[-1]) if stamps else (0.0, 0.0)
        window = (last - first) / 86400 if stamps else 0.0
        if window < min_window_days:
            # Twenty approvals in one afternoon is a busy day, not a habit.
            continue

        kind, _, subject = signature.partition(":")
        patterns.append(
            Pattern(
                kind=kind,
                subject=subject,
                count=len(group),
                approvers=tuple(d.approver for d in group),
                first_at=first,
                last_at=last,
                decision_ids=tuple(d.oversight_id for d in group),
                risk_level=next((d.risk_level for d in group if d.risk_level), ""),
            )
        )
    return patterns


# ---------------------------------------------------------------------------
# State — what has already been raised or rejected
# ---------------------------------------------------------------------------


def state_path(repo_root: Path | None = None) -> Path:
    raw = os.environ.get(STATE_PATH_VAR, "").strip()
    if raw:
        return Path(raw)
    root = repo_root or Path(__file__).resolve().parent.parent
    return root / DEFAULT_STATE_FILE


def _load_state(repo_root: Path | None = None) -> dict[str, Any]:
    path = state_path(repo_root)
    if not path.is_file():
        return {"raised": {}, "rejected": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"raised": {}, "rejected": {}}
    return {
        "raised": raw.get("raised") or {},
        "rejected": raw.get("rejected") or {},
    }


def _save_state(state: dict[str, Any], repo_root: Path | None = None) -> None:
    atomic_write_text(
        state_path(repo_root),
        json.dumps(state, indent=2, sort_keys=True),
        mode=0o644,
        newline="",
    )


def record_raised(pattern: Pattern, *, repo_root: Path | None = None) -> None:
    state = _load_state(repo_root)
    state["raised"][pattern.evidence_hash()] = {
        "kind": pattern.kind,
        "subject": pattern.subject,
        "at": time.time(),
    }
    _save_state(state, repo_root)


def record_rejected(
    evidence_hash: str, *, reason: str = "", repo_root: Path | None = None
) -> None:
    """Remember a rejection so the same evidence is not raised again.

    Re-asking until someone says yes is how consent gets manufactured.
    """
    state = _load_state(repo_root)
    state["rejected"][evidence_hash] = {"at": time.time(), "reason": reason}
    _save_state(state, repo_root)


def already_handled(pattern: Pattern, *, repo_root: Path | None = None) -> str:
    """"raised" / "rejected" / "" for this exact evidence."""
    state = _load_state(repo_root)
    digest = pattern.evidence_hash()
    if digest in state["rejected"]:
        return "rejected"
    if digest in state["raised"]:
        return "raised"
    return ""


def proposals(
    decisions: Iterable[Decision],
    *,
    threshold: int = DEFAULT_THRESHOLD,
    min_window_days: float = DEFAULT_MIN_WINDOW_DAYS,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Proposals worth raising: detected, not yet raised, not rejected.

    Returns documents for a human to review. **Nothing here applies one** —
    there is no code path in this module that narrows policy, and that is the
    property the whole design rests on.
    """
    out: list[dict[str, Any]] = []
    for pattern in detect(
        decisions, threshold=threshold, min_window_days=min_window_days
    ):
        if already_handled(pattern, repo_root=repo_root):
            continue
        out.append(pattern.as_proposal())
    return out
