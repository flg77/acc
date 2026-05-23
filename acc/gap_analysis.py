"""Compliance gap analysis (PR-Z2b).

Compares the governance ACC actually loads (Cat-A/B/C rules, from
:mod:`acc.governance_inventory`) against a target framework catalog
(from :mod:`acc.frameworks`) and reports which framework controls are
**covered** by an existing rule vs. which are **gaps**.

The mapping is deterministic + explainable: each control is matched to
governance rules by shared domain terminology (token overlap, with
generic stopwords removed).  Every control records *why* it was judged
covered/uncovered (the matched rule ids + the shared terms) so the
report doubles as an **audit reasoning trail** a human can follow.

Gaps carry a severity (driven by the control's category) and a
``proposed_rule_text`` stub the operator/agent can refine into an
enforceable Cat-B/C rule.  :func:`build_gap_prompt` produces an
LLM prompt for the deeper, agent-driven analysis (Phase 3); the
deterministic report stands alone for on-demand TUI use.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acc.frameworks import Framework
    from acc.governance_inventory import GovernanceLayer

# Generic words that carry no discriminating compliance meaning — drop
# them before computing overlap so "the system manages data" doesn't
# spuriously match every rule.
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "is",
    "are", "be", "by", "with", "that", "this", "these", "those", "as",
    "at", "from", "into", "its", "it", "must", "shall", "should", "all",
    "any", "not", "no", "only", "when", "which", "who", "via", "per",
    "ai", "system", "systems", "data", "use", "used", "using", "ensure",
    "ensures", "process", "processes", "management", "managed", "manage",
    "documented", "document", "documentation", "organization",
    "organizational", "appropriate", "related", "relevant", "across",
    "within", "their", "they", "can", "support", "achieve", "results",
    "include", "including", "etc", "e", "g",
})

# Uncovered controls in these (case-insensitive) categories are the
# higher-stakes ones — flag them HIGH so they surface first.
_HIGH_SEVERITY_CATEGORIES = frozenset({
    "human_oversight", "robustness", "logging", "risk_management",
    "security", "data_governance", "data",
})

# A control is COVERED when some rule shares at least this many
# meaningful terms; rules sharing >= _MAP_MIN are listed as related.
_COVER_MIN = 2
_MAP_MIN = 1


@dataclass
class ControlGap:
    control_id: str
    title: str
    category: str
    covered: bool
    mapped_rule_ids: list[str] = field(default_factory=list)
    shared_terms: list[str] = field(default_factory=list)
    severity: str = ""           # gaps: HIGH/MEDIUM; covered: ""
    rationale: str = ""
    proposed_rule_text: str = ""


@dataclass
class GapReport:
    framework_id: str
    framework_name: str
    generated_at_ms: int
    controls: list[ControlGap] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.controls)

    @property
    def covered_count(self) -> int:
        return sum(1 for c in self.controls if c.covered)

    @property
    def gap_count(self) -> int:
        return self.total - self.covered_count

    @property
    def coverage_pct(self) -> float:
        return (self.covered_count / self.total * 100.0) if self.total else 0.0


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.split(r"[^a-z0-9]+", (text or "").lower())
        if len(t) >= 3 and t not in _STOPWORDS
    }


def _severity_for(category: str) -> str:
    return "HIGH" if category.strip().lower() in _HIGH_SEVERITY_CATEGORIES else "MEDIUM"


def _proposed_rule(control, framework_id: str) -> str:
    return (
        f"# Proposed rule to cover {framework_id}:{control.control_id} "
        f"— {control.title}\n"
        f"# Intent: {control.description.strip() or control.title}\n"
        f"# Category: Cat-B (conditional) or Cat-C (learned) — Cat-A is "
        f"immutable.\n"
        f"# TODO: encode as an OPA policy / setpoint and route through the "
        f"signed RULE_UPDATE path."
    )


def analyze_gaps(
    layers: "list[GovernanceLayer]", framework: "Framework",
) -> GapReport:
    """Deterministically map *framework* controls onto loaded *layers*."""
    # Pre-tokenise every governance rule once.
    rule_tokens: list[tuple[str, set[str]]] = []
    for layer in layers:
        for rule in layer.rules:
            rule_tokens.append((rule.rule_id, _tokens(rule.summary)))

    report = GapReport(
        framework_id=framework.framework_id,
        framework_name=framework.name,
        generated_at_ms=int(time.time() * 1000),
    )
    for control in framework.controls:
        ctrl_tokens = _tokens(f"{control.title} {control.description}")
        mapped: list[tuple[str, set[str]]] = []
        best_overlap = 0
        for rid, rtoks in rule_tokens:
            shared = ctrl_tokens & rtoks
            if len(shared) >= _MAP_MIN:
                mapped.append((rid, shared))
                best_overlap = max(best_overlap, len(shared))
        covered = best_overlap >= _COVER_MIN
        mapped.sort(key=lambda m: len(m[1]), reverse=True)
        mapped_ids = [rid for rid, _ in mapped]
        shared_terms = sorted(
            {t for _, sh in mapped for t in sh}
        )
        if covered:
            rationale = (
                f"covered: rule(s) {', '.join(mapped_ids[:5])} share terms "
                f"{', '.join(shared_terms[:8])}"
            )
            gap = ControlGap(
                control_id=control.control_id,
                title=control.title,
                category=control.category,
                covered=True,
                mapped_rule_ids=mapped_ids,
                shared_terms=shared_terms,
                rationale=rationale,
            )
        else:
            severity = _severity_for(control.category)
            weak = (
                f" (weak partial match: {', '.join(mapped_ids[:3])})"
                if mapped_ids else ""
            )
            rationale = (
                f"GAP: no loaded rule shares >= {_COVER_MIN} domain terms "
                f"with this control{weak}"
            )
            gap = ControlGap(
                control_id=control.control_id,
                title=control.title,
                category=control.category,
                covered=False,
                mapped_rule_ids=mapped_ids,
                shared_terms=shared_terms,
                severity=severity,
                rationale=rationale,
                proposed_rule_text=_proposed_rule(control, framework.framework_id),
            )
        report.controls.append(gap)
    return report


def build_gap_prompt(
    layers: "list[GovernanceLayer]", framework: "Framework",
) -> str:
    """Build an LLM prompt for the deeper, agent-driven gap analysis
    (Phase 3).  Embeds the loaded rules + the framework controls and
    asks for a structured mapping + proposed enforceable rules."""
    rule_lines = [
        f"- [{layer.category}] {rule.rule_id}: {rule.summary}"
        for layer in layers for rule in layer.rules
    ]
    ctrl_lines = [
        f"- {c.control_id} ({c.category}): {c.title} — {c.description.strip()}"
        for c in framework.controls
    ]
    return (
        f"You are a compliance auditor.  Map the loaded governance rules "
        f"onto the controls of framework '{framework.name}'.\n\n"
        f"LOADED GOVERNANCE RULES (Cat-A immutable, Cat-B/C updatable):\n"
        + "\n".join(rule_lines)
        + "\n\nFRAMEWORK CONTROLS:\n"
        + "\n".join(ctrl_lines)
        + "\n\nFor EACH control return JSON {control_id, covered (bool), "
        "mapped_rule_ids[], gap_description, severity (HIGH|MEDIUM|LOW), "
        "proposed_rule_text}.  For gaps, propose an enforceable Cat-B or "
        "Cat-C rule (never Cat-A).  Include your full reasoning."
    )


def reports_root() -> Path:
    """Writable dir for generated gap-analysis reports / audit docs."""
    raw = os.environ.get("ACC_COMPLIANCE_REPORTS_ROOT", "").strip()
    return Path(raw) if raw else Path("/app/.acc-compliance")


def render_markdown(report: GapReport) -> str:
    """Human-readable audit doc with the full per-control reasoning."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(report.generated_at_ms / 1000))
    lines = [
        f"# Compliance gap analysis — {report.framework_name}",
        "",
        f"- framework_id: `{report.framework_id}`",
        f"- generated: {ts}",
        f"- coverage: **{report.covered_count}/{report.total}** "
        f"({report.coverage_pct:.0f}%) — {report.gap_count} gap(s)",
        "",
        "> Method: deterministic **lexical** first-pass (shared domain "
        "terminology between rule summaries and control text). ACC's "
        "constitutional rules use biological-metaphor language, so lexical "
        "coverage is conservative — run the agent-driven (LLM) gap analysis "
        "for semantic mapping and refined proposed rules.",
        "",
        "## Controls",
        "",
    ]
    for c in report.controls:
        status = "✅ covered" if c.covered else f"❌ GAP ({c.severity})"
        lines.append(f"### {c.control_id} — {c.title}  [{status}]")
        lines.append(f"- rationale: {c.rationale}")
        if c.mapped_rule_ids:
            lines.append(f"- mapped rules: {', '.join(c.mapped_rule_ids)}")
        if not c.covered and c.proposed_rule_text:
            lines.append("- proposed rule:")
            lines.append("```")
            lines.append(c.proposed_rule_text)
            lines.append("```")
        lines.append("")
    return "\n".join(lines)


def dump_gap_report(report: GapReport, root: Path | None = None) -> Path:
    """Atomically write the report as JSON + markdown into the reports
    store.  Returns the JSON path.  The pair forms the audit artifact:
    the JSON is machine-greppable, the markdown human-readable."""
    root = root or reports_root()
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(report.generated_at_ms / 1000))
    base = f"gap-{report.framework_id}-{stamp}"
    payload = {
        "framework_id": report.framework_id,
        "framework_name": report.framework_name,
        "generated_at_ms": report.generated_at_ms,
        "coverage_pct": report.coverage_pct,
        "covered_count": report.covered_count,
        "gap_count": report.gap_count,
        "controls": [asdict(c) for c in report.controls],
    }
    json_path = root / f"{base}.json"
    md_path = root / f"{base}.md"
    for path, text in (
        (json_path, json.dumps(payload, indent=2, ensure_ascii=False)),
        (md_path, render_markdown(report)),
    ):
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    return json_path
