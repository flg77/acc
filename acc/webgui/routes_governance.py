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
from acc.webgui.deps import get_hub
from acc.webgui.observers import ObserverHub

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


# ---------------------------------------------------------------------------
# Diagnostics — golden-prompt eval-history (WebGUI parity for proposal G)
#
# Mirrors the TUI Diagnostics pane on the RHOAI-facing surface: run a golden
# prompt, see the per-prompt run history enriched (tokens / compliance /
# verdict) with an MLflow trace deep-link, and promote a prompt into a role's
# behavioral eval pack.  Reuses the shipped runtime functions verbatim —
# ``run_one`` (which itself carries the P2 reply enrichment), ``read_run_history``,
# ``from_golden_prompt``, ``mlflow_trace_url`` — so this is API surface, not new
# engine code.
# ---------------------------------------------------------------------------


def _golden_def_of_good(g) -> list[str]:
    """The deterministic ``expects`` criteria a golden prompt is judged by
    (proposal G 'definition of good', layer 1)."""
    ex = getattr(g, "expects", None)
    crit: list[str] = []
    if ex is not None:
        if getattr(ex, "reply_non_empty", False):
            crit.append("reply non-empty")
        if getattr(ex, "blocked", False):
            crit.append("expected blocked")
        lat = getattr(ex, "latency_max_ms", None)
        if lat:
            crit.append(f"latency ≤ {lat}ms")
        oc = list(getattr(ex, "output_contains", None) or [])
        if oc:
            crit.append("contains " + ", ".join(map(str, oc[:3])))
        rx = getattr(ex, "output_matches_regex", None)
        if rx:
            crit.append(f"matches /{rx}/")
        inv = list(getattr(ex, "invocations_kind_contains", None) or [])
        inv += list(getattr(ex, "invocations_target_contains", None) or [])
        if inv:
            crit.append("invokes " + ", ".join(map(str, inv[:3])))
    return crit or ["reply arrived"]


def _find_golden(name: str):
    from acc.golden_prompts import load_merged  # noqa: PLC0415
    g = next((p for p in load_merged() if p.name == name), None)
    if g is None:
        raise HTTPException(404, f"golden prompt {name!r} not found")
    return g


@router.get("/diagnostics/golden/{name}", dependencies=[Depends(require_viewer)])
def golden_detail(name: str) -> dict:
    """One golden prompt's full definition + its 'definition of good'."""
    g = _find_golden(name)
    return {"prompt": g.model_dump(), "definition_of_good": _golden_def_of_good(g)}


@router.get(
    "/diagnostics/golden/{name}/history", dependencies=[Depends(require_viewer)],
)
def golden_history(name: str, limit: int = 20) -> dict:
    """Per-prompt run history (newest-first) — each run enriched (tokens /
    compliance / verdict) with an MLflow trace deep-link — plus saved versions."""
    from acc.backends.mlflow_runs import mlflow_trace_url  # noqa: PLC0415
    from acc.golden_prompts import (  # noqa: PLC0415
        list_versions, read_run_history,
    )
    rows = read_run_history(name, limit=limit)
    for r in rows:
        r["mlflow_trace_url"] = mlflow_trace_url(r.get("task_id", "") or "")
    return {"name": name, "runs": rows, "versions": list_versions(name)}


class RunGoldenRequest(BaseModel):
    collective_id: str
    target_agent_id: str | None = None
    timeout_s: float = 180.0


@router.post("/diagnostics/golden/{name}/run")
async def run_golden(
    name: str,
    req: RunGoldenRequest,
    hub: ObserverHub = Depends(get_hub),
    principal: Principal = Depends(require_operator),
) -> dict:
    """Run a golden prompt against the live collective and return the enriched
    result + the MLflow trace link — the WebGUI's eval-execution path."""
    from acc.backends.mlflow_runs import mlflow_trace_url  # noqa: PLC0415
    from acc.golden_prompts import append_run_record, run_one  # noqa: PLC0415

    obs = hub.observer(req.collective_id)
    if obs is None:
        raise HTTPException(404, f"collective {req.collective_id!r} not observed")
    g = _find_golden(name)
    updates: dict = {"timeout_s": req.timeout_s}
    if req.target_agent_id:
        updates["target_agent_id"] = req.target_agent_id
    g = g.model_copy(update=updates)
    result = await run_one(g, observer=obs, collective_id=req.collective_id)
    append_run_record(result, collective_id=req.collective_id)
    out = result.model_dump()
    out["mlflow_trace_url"] = mlflow_trace_url(result.task_id)
    return out


@router.post("/diagnostics/golden/{name}/promote")
def promote_golden(
    name: str, principal: Principal = Depends(require_operator),
) -> dict:
    """Promote a golden prompt into its role's behavioral eval pack
    (proposal G P3 — the role-testing on-ramp)."""
    from acc.golden_prompts import writable_root  # noqa: PLC0415
    from acc.pkg.evals import (  # noqa: PLC0415
        dump_behavior_eval, from_golden_prompt,
    )
    g = _find_golden(name)
    be = from_golden_prompt(g)
    role = g.target_role or "_norole"
    dest = writable_root() / "promoted-evals" / role / "evals" / "behavior"
    try:
        out = dump_behavior_eval(be, dest)
    except OSError as exc:
        raise HTTPException(500, f"promote failed: {exc}")
    return {
        "status": "promoted", "role": role,
        "eval_name": be.name, "path": str(out),
    }
