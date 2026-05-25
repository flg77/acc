"""acc-webgui — governance / compliance / diagnostics / models endpoints.

Web parity for the latest TUI work:
  * Compliance pane — governance layers (Cat-A/B/C), framework catalogs,
    gap analysis, rule proposals (PR-Z1/Z2/Z3).
  * Diagnostics pane — golden prompts (PR-N/Y).
  * Multimodel — the central model registry (PR-MM1).

Reads are **host-local** (``regulatory_layer/``, ``models.yaml``, the
writable stores mounted into acc-webgui) — no NATS.  Best-effort: a
missing dir yields an empty list, never a 500.  Reads need the viewer
role; the gap-scan + proposal-decision actions need operator.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from acc.webgui.auth import Principal, require_operator, require_viewer

router = APIRouter(prefix="/api", tags=["governance"])


# ---------------------------------------------------------------------------
# Reads (viewer)
# ---------------------------------------------------------------------------


@router.get("/governance/layers", dependencies=[Depends(require_viewer)])
def governance_layers() -> dict:
    """Loaded Cat-A/B/C governance layers + their rules."""
    from acc.governance_inventory import load_all_layers  # noqa: PLC0415
    try:
        layers = load_all_layers()
    except Exception:
        layers = []
    return {"layers": [
        {
            "category": l.category, "title": l.title, "version": l.version,
            "immutable": l.immutable, "rule_count": l.rule_count,
            "file_paths": l.file_paths,
            "rules": [asdict(r) for r in l.rules],
        }
        for l in layers
    ]}


@router.get("/governance/frameworks", dependencies=[Depends(require_viewer)])
def frameworks() -> dict:
    """Built-in + imported compliance framework catalogs (summary)."""
    from acc.frameworks import load_all_frameworks  # noqa: PLC0415
    try:
        fws = load_all_frameworks()
    except Exception:
        fws = []
    return {"frameworks": [
        {
            "framework_id": f.framework_id, "name": f.name,
            "version": f.version, "source": f.source,
            "control_count": f.control_count,
        }
        for f in fws
    ]}


@router.get("/governance/proposals", dependencies=[Depends(require_viewer)])
def proposals() -> dict:
    """Pending/decided Cat-B/C rule proposals (gap / violation / self-challenge)."""
    from acc.rule_proposals import list_proposals  # noqa: PLC0415
    try:
        ps = list_proposals()
    except Exception:
        ps = []
    return {"proposals": [p.model_dump() for p in ps]}


@router.get("/diagnostics/golden", dependencies=[Depends(require_viewer)])
def golden_prompts() -> dict:
    """Golden-prompt suite (shipped + writable store + attached dirs)."""
    from acc.golden_prompts import load_merged  # noqa: PLC0415
    try:
        gs = load_merged()
    except Exception:
        gs = []
    return {"prompts": [
        {
            "name": g.name, "target_role": g.target_role,
            "operating_mode": g.operating_mode, "description": g.description,
        }
        for g in gs
    ]}


@router.get("/models", dependencies=[Depends(require_viewer)])
def models() -> dict:
    """Central model registry (per-agent model assignment, PR-MM1)."""
    from acc.models import load_models  # noqa: PLC0415
    try:
        ms = load_models()
    except Exception:
        ms = []
    return {"models": [m.model_dump() for m in ms]}


# ---------------------------------------------------------------------------
# Actions (operator)
# ---------------------------------------------------------------------------


class GapScanRequest(BaseModel):
    framework_id: str


@router.post("/governance/gap-scan")
def gap_scan(
    req: GapScanRequest,
    principal: Principal = Depends(require_operator),
) -> dict:
    """Run the deterministic gap analysis for a framework, write the audit
    doc, and emit Cat-B/C rule proposals — the Compliance pane's Run-scan."""
    from acc.frameworks import load_all_frameworks  # noqa: PLC0415
    from acc.gap_analysis import analyze_gaps, dump_gap_report  # noqa: PLC0415
    from acc.governance_inventory import load_all_layers  # noqa: PLC0415
    from acc.rule_proposals import (  # noqa: PLC0415
        promotion_mode, proposals_from_gap_report,
    )

    fw = next(
        (f for f in load_all_frameworks() if f.framework_id == req.framework_id),
        None,
    )
    if fw is None:
        raise HTTPException(404, f"framework {req.framework_id!r} not found")
    report = analyze_gaps(load_all_layers(), fw)
    dump_gap_report(report)
    created = proposals_from_gap_report(report)
    return {
        "framework_id": fw.framework_id,
        "coverage_pct": round(report.coverage_pct, 1),
        "gaps": report.gap_count,
        "proposals": len(created),
        "mode": promotion_mode(),
    }


class ProposalDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(approve|reject)$")


@router.post("/governance/proposals/{proposal_id}/decision")
def proposal_decision(
    proposal_id: str,
    req: ProposalDecisionRequest,
    principal: Principal = Depends(require_operator),
) -> dict:
    """Approve (→ signed-bundle overlay) or reject a rule proposal."""
    from acc.rule_proposals import (  # noqa: PLC0415
        approve_proposal, get_proposal, reject_proposal,
    )
    if get_proposal(proposal_id) is None:
        raise HTTPException(404, f"proposal {proposal_id!r} not found")
    by = f"webgui:{principal.user}"
    if req.decision == "approve":
        approve_proposal(proposal_id, by=by)
    else:
        reject_proposal(proposal_id, by=by)
    return {"status": "ok", "decision": req.decision, "proposal_id": proposal_id}
