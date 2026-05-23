"""Rule proposals — the human-in-the-loop bridge from findings to
enforceable governance (PR-Z3b).

Gap analysis (Z2b), violation learning (Z3c), and self-challenge (Z3e)
all surface *candidate* rules.  None of them ever write enforced policy
directly — that would bypass the arbiter-signed OPA bundle pipeline and
could touch the immutable Cat-A constitution.  Instead they create
**RuleProposals** (Cat-B or Cat-C only) that:

* in ``propose`` mode (default) sit ``PROPOSED`` until an operator
  approves them in the Compliance pane, then
* (or immediately, in ``auto`` mode) get appended to a **pending-
  proposals overlay** (``proposed_rules.jsonl`` in the writable store)
  that the arbiter's ICL consolidation pipeline consumes, signs, and
  serves as a Cat-C bundle fragment.

The mode is the ``learned_rule_promotion`` Cat-B setpoint
(``regulatory_layer/category_b/data_rhoai.json``).  This module is the
pure store + lifecycle; the arbiter consumption is existing/out-of-scope.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_VALID_CATEGORIES = {"B", "C"}
_VALID_STATUS = {"PROPOSED", "APPROVED", "REJECTED"}
_OVERLAY_NAME = "proposed_rules.jsonl"


class RuleProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    source: str = "manual"           # gap | violation | self_challenge | manual
    category: str = "C"              # B | C — NEVER A (immutable)
    rule_text: str = ""
    rationale: str = ""
    severity: str = "MEDIUM"
    confidence: float = 0.0
    refs: list[str] = Field(default_factory=list)
    status: str = "PROPOSED"
    created_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    decided_at_ms: Optional[int] = None
    decided_by: str = ""

    @field_validator("category")
    @classmethod
    def _cat_not_a(cls, v: str) -> str:
        v = (v or "").upper()
        if v == "A":
            raise ValueError(
                "Cat-A is immutable — proposals may only target Cat-B or Cat-C"
            )
        if v not in _VALID_CATEGORIES:
            raise ValueError(f"category must be B or C, got {v!r}")
        return v


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def proposals_root() -> Path:
    raw = os.environ.get("ACC_RULE_PROPOSALS_ROOT", "").strip()
    if raw:
        return Path(raw)
    # Default: a subdir of the compliance reports store.
    base = os.environ.get("ACC_COMPLIANCE_REPORTS_ROOT", "").strip()
    return Path(base or "/app/.acc-compliance") / "proposals"


def overlay_path() -> Path:
    """The pending-proposals overlay the arbiter consumes."""
    return proposals_root() / _OVERLAY_NAME


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_proposal(p: RuleProposal, root: Optional[Path] = None) -> Path:
    root = root or proposals_root()
    path = root / f"{p.proposal_id}.json"
    _atomic_write(path, p.model_dump_json(indent=2))
    return path


def create_proposal(
    *,
    source: str,
    category: str,
    rule_text: str,
    rationale: str,
    severity: str = "MEDIUM",
    confidence: float = 0.0,
    refs: Optional[list[str]] = None,
    root: Optional[Path] = None,
    auto_approve: bool = False,
) -> RuleProposal:
    """Create + persist a proposal.  When *auto_approve* (auto mode),
    it lands APPROVED and is appended to the overlay immediately."""
    p = RuleProposal(
        source=source, category=category, rule_text=rule_text,
        rationale=rationale, severity=severity, confidence=confidence,
        refs=list(refs or []),
    )
    save_proposal(p, root)
    if auto_approve:
        approve_proposal(p.proposal_id, by="auto", root=root)
        # re-read the decided proposal
        return get_proposal(p.proposal_id, root) or p
    return p


def list_proposals(
    status: Optional[str] = None, root: Optional[Path] = None,
) -> list[RuleProposal]:
    root = root or proposals_root()
    if not root.is_dir():
        return []
    out: list[RuleProposal] = []
    for f in sorted(root.glob("*.json")):
        try:
            out.append(RuleProposal.model_validate_json(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    if status:
        out = [p for p in out if p.status == status]
    out.sort(key=lambda p: p.created_at_ms, reverse=True)
    return out


def get_proposal(pid: str, root: Optional[Path] = None) -> Optional[RuleProposal]:
    root = root or proposals_root()
    path = root / f"{pid}.json"
    try:
        return RuleProposal.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _append_overlay(p: RuleProposal, root: Optional[Path] = None) -> None:
    """Append an approved proposal to the pending-proposals overlay the
    arbiter ICL pipeline consumes (signs + serves as Cat-C)."""
    root = root or proposals_root()
    root.mkdir(parents=True, exist_ok=True)
    line = json.dumps({
        "proposal_id": p.proposal_id,
        "category": p.category,
        "rule_text": p.rule_text,
        "rationale": p.rationale,
        "source": p.source,
        "refs": p.refs,
        "approved_at_ms": p.decided_at_ms,
        "approved_by": p.decided_by,
    }, ensure_ascii=False)
    with (root / _OVERLAY_NAME).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def approve_proposal(
    pid: str, *, by: str = "operator", root: Optional[Path] = None,
) -> Optional[RuleProposal]:
    """Mark a proposal APPROVED + append it to the overlay."""
    p = get_proposal(pid, root)
    if p is None or p.status == "APPROVED":
        return p
    p.status = "APPROVED"
    p.decided_at_ms = int(time.time() * 1000)
    p.decided_by = by
    save_proposal(p, root)
    _append_overlay(p, root)
    return p


def reject_proposal(
    pid: str, *, by: str = "operator", root: Optional[Path] = None,
) -> Optional[RuleProposal]:
    p = get_proposal(pid, root)
    if p is None:
        return None
    p.status = "REJECTED"
    p.decided_at_ms = int(time.time() * 1000)
    p.decided_by = by
    save_proposal(p, root)
    return p


# ---------------------------------------------------------------------------
# Promotion mode (Cat-B setpoint)
# ---------------------------------------------------------------------------


def promotion_mode() -> str:
    """Read ``learned_rule_promotion`` from the Cat-B setpoints.

    Returns ``"propose"`` (default — human approval required) or
    ``"auto"`` (auto-approve into the overlay).  Falls back to
    ``"propose"`` on any read/parse error (fail-safe to human review).
    """
    from acc.governance_inventory import regulatory_root  # noqa: PLC0415

    env = os.environ.get("ACC_LEARNED_RULE_PROMOTION", "").strip().lower()
    if env in {"propose", "auto"}:
        return env
    try:
        data = json.loads(
            (regulatory_root() / "category_b" / "data_rhoai.json").read_text(
                encoding="utf-8",
            )
        )
        mode = str(data.get("setpoints", {}).get("learned_rule_promotion", "propose"))
        return mode if mode in {"propose", "auto"} else "propose"
    except Exception:
        return "propose"


# ---------------------------------------------------------------------------
# Bridges from findings → proposals
# ---------------------------------------------------------------------------


def proposals_from_gap_report(report, root: Optional[Path] = None) -> list[RuleProposal]:
    """Create a Cat-B/C proposal for each GAP in a gap-analysis report.

    Honours :func:`promotion_mode` — ``auto`` auto-approves into the
    overlay; ``propose`` leaves them PENDING for operator review."""
    auto = promotion_mode() == "auto"
    created: list[RuleProposal] = []
    for c in report.controls:
        if c.covered:
            continue
        created.append(create_proposal(
            source="gap",
            category="C",
            rule_text=c.proposed_rule_text,
            rationale=(
                f"Gap vs {report.framework_id}:{c.control_id} "
                f"({c.title}). {c.rationale}"
            ),
            severity=c.severity or "MEDIUM",
            refs=[f"{report.framework_id}:{c.control_id}"],
            root=root,
            auto_approve=auto,
        ))
    return created
