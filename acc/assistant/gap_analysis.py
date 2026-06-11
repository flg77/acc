"""Request-time role-gap discovery — proposal 019 PR-OP4.

When the assistant's best-matched role is a poor fit for the goal
(confidence below ``role_gap_threshold``), it should NOT force-route to
a flailing role.  Instead it recognises a **role gap** and proposes one
of three remedies, grounded in the feedback loops the operator asked
for (reviewer critiques + compliance_officer findings):

* ``infuse_known``  — a configured catalog already advertises a pack
  whose name matches the missing capability; propose installing it.
* ``extend_role``   — an installed role keeps under-performing on this
  kind of work (reviewer/compliance evidence); propose adding a skill
  / MCP to it.
* ``new_role``      — nothing fits; propose authoring a new role.

This module is **pure**: the caller supplies the catalog view (PR-OP1),
the best-match + confidence (orchestrator / CapabilityIndex), and the
recent feedback notes (read via
:func:`acc.memory_reflection.read_hot_cache` for the reviewer +
compliance_officer roles).  It returns a structured
:class:`RoleGapFinding` (or ``None`` when the match is good enough) and
provides the ``[ROLE_GAP:goal_id:<json>]`` marker emit + parse.  The
finding flows into the existing Compliance pending-items queue
(rendering lands in a follow-up slice); the actual authoring of a new
role from an approved gap is a separate human / compliance_officer
step — this module only *surfaces* the gap.

No NATS, no LLM, no Redis I/O here — all I/O is the caller's job.  The
heuristics are a deterministic first cut; the LLM refines the proposal
prose at runtime when it emits the marker.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Optional

logger = logging.getLogger("acc.assistant.gap_analysis")

DEFAULT_ROLE_GAP_THRESHOLD = 0.6

GapKind = Literal["infuse_known", "extend_role", "new_role"]

# Keywords in a feedback note that signal a role under-performed.  Used
# to mine reviewer / compliance_officer notes for evidence that an
# installed role needs an extension.
_FAILURE_SIGNALS = (
    "fail", "failed", "failure", "lint", "rejected", "reject",
    "missing", "could not", "couldn't", "unable", "violation",
    "hedge", "incomplete", "regression", "below", "did not",
)


@dataclass(frozen=True)
class GapEvidence:
    """One feedback-loop citation backing a gap finding."""

    source: str           # "reviewer" | "compliance_officer" | ...
    note: str             # the summary text
    refs: tuple[str, ...] = ()  # note_ids / audit ids when known

    def to_dict(self) -> dict:
        return {"source": self.source, "note": self.note, "refs": list(self.refs)}


@dataclass(frozen=True)
class RoleGapFinding:
    """A request-time recognition that no role fits the goal well."""

    goal_id: str
    goal_summary: str
    best_match_role: Optional[str]
    best_match_confidence: float
    gap_kind: GapKind
    proposal: dict[str, Any]
    evidence: tuple[GapEvidence, ...] = ()
    fallback_taken: str = "none"

    def to_dict(self) -> dict:
        return {
            "goal_id": self.goal_id,
            "goal_summary": self.goal_summary,
            "best_match": {
                "role": self.best_match_role,
                "confidence": round(self.best_match_confidence, 4),
            },
            "gap_kind": self.gap_kind,
            "proposal": self.proposal,
            "evidence": [e.to_dict() for e in self.evidence],
            "fallback_taken": self.fallback_taken,
        }

    def to_marker(self) -> str:
        """Render the ``[ROLE_GAP:goal_id:<json>]`` marker."""
        body = json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)
        return f"[ROLE_GAP:{self.goal_id}:{body}]"


# ---------------------------------------------------------------------------
# Evidence mining
# ---------------------------------------------------------------------------


def build_evidence(
    feedback_notes: dict[str, Iterable[str]],
    *,
    candidate_role: Optional[str] = None,
    max_per_source: int = 3,
) -> tuple[GapEvidence, ...]:
    """Mine reviewer / compliance_officer notes for failure-signal lines.

    ``feedback_notes`` maps ``role_label -> [summary, ...]`` (as returned
    by :func:`acc.memory_reflection.read_hot_cache`).  A note counts as
    evidence when it contains a failure signal AND, when
    ``candidate_role`` is given, mentions that role (so we attribute the
    weakness to the right role).
    """
    out: list[GapEvidence] = []
    for source, notes in sorted(feedback_notes.items()):
        kept = 0
        for note in notes or []:
            text = str(note)
            low = text.lower()
            if not any(sig in low for sig in _FAILURE_SIGNALS):
                continue
            if candidate_role and candidate_role.lower() not in low:
                continue
            out.append(GapEvidence(source=source, note=text))
            kept += 1
            if kept >= max_per_source:
                break
    return tuple(out)


# ---------------------------------------------------------------------------
# Gap-kind inference
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 2}


def infer_gap_kind(
    *,
    goal_text: str,
    best_match_role: Optional[str],
    available_packages: Iterable[dict[str, Any]],
    evidence: tuple[GapEvidence, ...],
) -> tuple[GapKind, dict[str, Any]]:
    """Decide the remedy class + a first-cut proposal payload.

    Heuristic precedence (deterministic; LLM refines prose at runtime):

    1. ``infuse_known`` — an available (uninstalled) package's name
       shares a meaningful token with the goal → propose installing it.
    2. ``extend_role`` — there's failure evidence pinned to the
       best-match role → propose extending that role.
    3. ``new_role`` — neither; propose a fresh role.
    """
    goal_tokens = _tokenize(goal_text)

    # 1. infuse_known — token overlap between goal and an available pack.
    best_pkg: Optional[dict[str, Any]] = None
    best_overlap = 0
    for pkg in available_packages or []:
        pkg_tokens = _tokenize(pkg.get("package", ""))
        overlap = len(goal_tokens & pkg_tokens)
        if overlap > best_overlap:
            best_overlap, best_pkg = overlap, pkg
    if best_pkg is not None and best_overlap >= 1:
        return "infuse_known", {
            "infuse": {
                "package": best_pkg.get("package"),
                "version": best_pkg.get("version"),
                "tier": best_pkg.get("tier"),
                "rationale": "an available catalog package matches the goal "
                             "domain; install it to gain the missing role(s)",
            }
        }

    # 2. extend_role — failure evidence against the best-match role.
    if best_match_role and evidence:
        return "extend_role", {
            "extend_role": {
                "role": best_match_role,
                "add_skills": [],   # LLM fills the concrete skill at runtime
                "rationale": "the closest installed role repeatedly "
                             "under-performs on this work per the cited "
                             "feedback; extend it rather than force-route",
            }
        }

    # 3. new_role — nothing fits.
    return "new_role", {
        "new_role": {
            "suggested_name": "",  # LLM proposes a name at runtime
            "rationale": "no installed or available role covers this goal; "
                         "a new role is warranted",
        }
    }


# ---------------------------------------------------------------------------
# Top-level analysis
# ---------------------------------------------------------------------------


def analyze_role_gap(
    *,
    goal_id: str,
    goal_text: str,
    best_match_role: Optional[str],
    best_match_confidence: float,
    available_packages: Iterable[dict[str, Any]] = (),
    feedback_notes: Optional[dict[str, Iterable[str]]] = None,
    threshold: float = DEFAULT_ROLE_GAP_THRESHOLD,
    fallback_taken: str = "none",
) -> Optional[RoleGapFinding]:
    """Return a :class:`RoleGapFinding` when the best match is too weak,
    else ``None``.

    The confidence gate is the whole trigger: at or above ``threshold``
    the assistant routes normally; below it, we recognise a gap and
    assemble the structured finding + evidence.
    """
    if best_match_confidence >= threshold:
        return None

    feedback_notes = feedback_notes or {}
    evidence = build_evidence(feedback_notes, candidate_role=best_match_role)
    gap_kind, proposal = infer_gap_kind(
        goal_text=goal_text,
        best_match_role=best_match_role,
        available_packages=available_packages,
        evidence=evidence,
    )
    summary = (goal_text or "").strip().split("\n", 1)[0][:140]
    return RoleGapFinding(
        goal_id=goal_id,
        goal_summary=summary,
        best_match_role=best_match_role,
        best_match_confidence=best_match_confidence,
        gap_kind=gap_kind,
        proposal=proposal,
        evidence=evidence,
        fallback_taken=fallback_taken,
    )


# ---------------------------------------------------------------------------
# Marker parse (the inverse of RoleGapFinding.to_marker)
# ---------------------------------------------------------------------------


# Match the marker prefix only; the JSON body (which contains nested
# braces) is extracted by brace-balancing from the first ``{`` so we
# don't truncate at the first inner ``}``.
_ROLE_GAP_PREFIX_RE = re.compile(r"\[ROLE_GAP:([^\s:\]]+):")


def _extract_balanced_json(text: str, start: int) -> tuple[Optional[str], int]:
    """From ``text[start]`` (expected ``{``), return the balanced-brace
    JSON substring + the index just past its closing ``}``.  Returns
    ``(None, start)`` when no balanced object is found.  String-literal
    aware so a ``}`` inside a quoted value doesn't close the object.
    """
    if start >= len(text) or text[start] != "{":
        return None, start
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1], i + 1
    return None, start


def parse_role_gap_markers(text: str) -> list[RoleGapFinding]:
    """Extract every ``[ROLE_GAP:goal_id:<json>]`` marker from LLM output.

    Malformed JSON is skipped + logged, never raised — a bad marker must
    not break the assistant's main answer flow.
    """
    text = text or ""
    out: list[RoleGapFinding] = []
    for m in _ROLE_GAP_PREFIX_RE.finditer(text):
        goal_id = m.group(1)
        body, end = _extract_balanced_json(text, m.end())
        if body is None:
            logger.warning(
                "gap_analysis: ROLE_GAP marker %r has no balanced JSON body",
                goal_id,
            )
            continue
        # require the marker to actually close with ``]`` right after the
        # JSON (allowing optional whitespace) — guards against prose that
        # merely looks like a marker prefix.
        tail = text[end:end + 2].lstrip()
        if not tail.startswith("]"):
            logger.warning(
                "gap_analysis: ROLE_GAP marker %r not closed with ']'", goal_id
            )
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.warning("gap_analysis: ROLE_GAP marker %r has bad JSON", goal_id)
            continue
        bm = data.get("best_match") or {}
        evidence = tuple(
            GapEvidence(
                source=str(e.get("source", "")),
                note=str(e.get("note", "")),
                refs=tuple(e.get("refs") or ()),
            )
            for e in (data.get("evidence") or [])
        )
        gap_kind = data.get("gap_kind", "new_role")
        if gap_kind not in ("infuse_known", "extend_role", "new_role"):
            gap_kind = "new_role"
        out.append(RoleGapFinding(
            goal_id=str(data.get("goal_id", goal_id)),
            goal_summary=str(data.get("goal_summary", "")),
            best_match_role=bm.get("role"),
            best_match_confidence=float(bm.get("confidence", 0.0) or 0.0),
            gap_kind=gap_kind,  # type: ignore[arg-type]
            proposal=data.get("proposal") or {},
            evidence=evidence,
            fallback_taken=str(data.get("fallback_taken", "none")),
        ))
    return out
