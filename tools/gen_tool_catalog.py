#!/usr/bin/env python3
"""Generate ``docs/tool-catalog.md`` — every model-facing capability ACC ships.

ACC can already state what an agent is *composed of* (the signed AgentBOM) and
what is *installable* (the built-in catalog).  Neither answers the question an
auditor actually asks: **what was this agent's model offered?**  A capability the
model can invoke that appears in no inventory is an ungoverned capability — it
gets no category assigned, never appears in a gap scan, and is absent from the
BOM the deployment attests to (threat ACC-TM-24).

Three properties make this more than documentation, and all three are the point:

1. **It BOOTS the registries, it does not parse source.**  ``SkillRegistry`` and
   ``MCPRegistry`` do the real load — importing adapter modules, deep-merging
   ``_base`` defaults, validating against the Pydantic manifests.  What lands in
   the catalog is what the runtime would actually offer, not what the YAML
   appears to say.

2. **A completeness guard turns a silent drop into a failure.**  ``load_from()``
   deliberately logs-and-drops a skill whose adapter will not import, so one bad
   skill cannot knock out the healthy ones.  That is correct at runtime and
   dangerous at inventory time: the capability quietly leaves the surface.  This
   generator globs ``skills/*/`` and ``mcps/*/`` and FAILS if anything on disk
   did not make it into the registry.

3. **It records the deployment truth.**  Whether an MCP's ``allowed_tools`` is
   bounded or delegates the whole tool surface to an upstream (ACC-TM-11), and
   the fact that ACC advertises ``id: purpose`` to the model while enforcing a
   schema it never shows.

Usage::

    python -m tools.gen_tool_catalog             # write docs/tool-catalog.md
    python -m tools.gen_tool_catalog --check     # CI guard: fail if stale

ACC Roadmap: DS-04.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_OUT = _REPO / "docs" / "tool-catalog.md"

# Mirrors acc.mcp.registry._EXCLUDED_DIR_NAMES / the skills loader's skips.
_EXCLUDED_DIRS = {"_base", "TEMPLATE", "__pycache__"}


class CompletenessError(RuntimeError):
    """Something on disk never reached the registry — a silent capability drop."""


# ---------------------------------------------------------------------------
# Discovery — what is on disk
# ---------------------------------------------------------------------------


def _dirs_on_disk(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and p.name not in _EXCLUDED_DIRS and not p.name.startswith(".")
    )


# ---------------------------------------------------------------------------
# Boot — what the runtime actually loads
# ---------------------------------------------------------------------------


def _boot_skills(skills_root: Path) -> dict[str, Any]:
    """Really load every skill: import adapters, validate manifests."""
    from acc.skills.registry import SkillRegistry

    reg = SkillRegistry()
    # Explicit base_dir keeps installed-package roots out of the catalog —
    # this documents what the REPO ships, deterministically.
    reg.load_from(skills_root)
    return reg.manifests()


def _boot_mcps(mcps_root: Path) -> dict[str, Any]:
    from acc.mcp.registry import MCPRegistry

    reg = MCPRegistry()
    reg.load_from(mcps_root)
    return reg.manifests()


def build(repo: Path = _REPO) -> dict[str, Any]:
    """Boot the registries and assemble the catalog model.

    Raises:
        CompletenessError: a skill or MCP directory exists but did not load.
    """
    skills_root = repo / "skills"
    mcps_root = repo / "mcps"

    skill_manifests = _boot_skills(skills_root)
    mcp_manifests = _boot_mcps(mcps_root)

    skill_dirs = _dirs_on_disk(skills_root)
    mcp_dirs = _dirs_on_disk(mcps_root)

    missing_skills = [d for d in skill_dirs if d not in skill_manifests]
    missing_mcps = [d for d in mcp_dirs if d not in mcp_manifests]
    if missing_skills or missing_mcps:
        raise CompletenessError(
            "capabilities on disk that the registry did not load "
            "(the registry logs-and-drops these, so they leave the "
            "model-facing surface silently):\n"
            + "".join(f"  skills/{s}\n" for s in missing_skills)
            + "".join(f"  mcps/{m}\n" for m in missing_mcps)
        )

    skills = []
    for sid in sorted(skill_manifests):
        m = skill_manifests[sid]
        skills.append({
            "id": sid,
            "purpose": m.purpose,
            "version": m.version,
            "risk_level": m.risk_level,
            "domain_id": m.domain_id or "",
            "requires_actions": list(m.requires_actions),
            "adapter": f"{m.adapter_module}.{m.adapter_class}",
            "input_schema": m.input_schema or {},
            "provider": getattr(m, "provider", "builtin"),
            "tags": list(getattr(m, "tags", []) or []),
        })

    mcps = []
    for sid in sorted(mcp_manifests):
        m = mcp_manifests[sid]
        allowed = list(m.allowed_tools)
        mcps.append({
            "id": sid,
            "purpose": m.purpose,
            "version": m.version,
            "risk_level": m.risk_level,
            "domain_id": m.domain_id or "",
            "requires_actions": list(m.requires_actions),
            "transport": m.transport,
            "allowed_tools": allowed,
            "denied_tools": list(m.denied_tools),
            # The ACC-TM-11 finding, computed rather than asserted.
            "tool_surface_bounded": bool(allowed),
        })

    return {"skills": skills, "mcps": mcps}


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _cell(text: str) -> str:
    """Make free-form manifest prose safe inside a markdown table cell.

    Manifest ``purpose`` fields are YAML block scalars, so several carry
    embedded newlines — which silently break the row they land in.
    """
    return " ".join(str(text).split()).replace("|", "\\|")


def _fmt_list(items: list[str]) -> str:
    return ", ".join(f"`{i}`" for i in items) if items else "—"


def _schema_summary(schema: dict) -> str:
    """One-line shape of an enforced input schema."""
    if not schema:
        return "_(accepts anything — no schema)_"
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    if not props:
        return f"`{schema.get('type', 'object')}`"
    parts = []
    for name, spec in props.items():
        typ = spec.get("type", "any") if isinstance(spec, dict) else "any"
        parts.append(f"{name}: {typ}" + ("" if name in required else "?"))
    closed = schema.get("additionalProperties") is False
    return "`{" + ", ".join(parts) + "}`" + ("" if closed else " _(open)_")


def render(cat: dict[str, Any]) -> str:
    skills, mcps = cat["skills"], cat["mcps"]
    unbounded = [m for m in mcps if not m["tool_surface_bounded"]]

    out: list[str] = []
    add = out.append

    add("<!-- GENERATED by tools/gen_tool_catalog.py — do not edit by hand.")
    add("     Regenerate:  python -m tools.gen_tool_catalog")
    add("     CI guard:    python -m tools.gen_tool_catalog --check -->")
    add("")
    add("# Model-facing capability catalog")
    add("")
    add("Every capability a model running in this repo's configuration can be "
        "offered. Generated by **booting** the skill and MCP registries — "
        "importing adapters, deep-merging `_base` defaults and validating the "
        "Pydantic manifests — not by parsing source, because what a registry "
        "loads and what a YAML file appears to say are different things.")
    add("")
    add("A capability the model can invoke that appears in no inventory is an "
        "ungoverned capability: no category assigned, absent from gap scans, "
        "missing from the signed AgentBOM. See "
        "[`THREAT-MODEL.md`](THREAT-MODEL.md) — **ACC-TM-24**.")
    add("")
    add(f"| Skills | MCP servers | MCP servers with an unbounded tool surface |")
    add(f"|---|---|---|")
    add(f"| {len(skills)} | {len(mcps)} | **{len(unbounded)}** |")
    add("")

    add("## What the model is actually told")
    add("")
    add("ACC advertises capabilities in the system prompt as `- <id>: <purpose>` "
        "plus the marker grammar (`[SKILL: <id> {json}]`, "
        "`[MCP: <server>.<tool> {json}]`), gated by each role's "
        "`default_skills` / `default_mcps`.")
    add("")
    add("**The input schema is enforced at invoke but never advertised.** The "
        "model is told a capability exists and what it is for, then has to infer "
        "the argument shape. The `Enforced input` column below is what the "
        "registry validates against — it is not what the model sees.")
    add("")

    if unbounded:
        add("## ⚠ Unbounded tool surfaces")
        add("")
        add("An MCP manifest with an empty `allowed_tools` list means *allow "
            "every tool the server advertises*. The advertised set is owned by "
            "the upstream: it can change when the upstream updates, with no ACC "
            "code change, no review, and no diff — the agent's capability "
            "surface grows silently between two runs. Raw upstream JSON Schema "
            "also enters the model-visible corpus unreviewed.")
        add("")
        add("This is threat **ACC-TM-11**, and these are the servers it applies to:")
        add("")
        for m in unbounded:
            add(f"- **`{m['id']}`** — {_cell(m['purpose'])} "
                f"(risk `{m['risk_level']}`, transport `{m['transport']}`"
                + (f", denied: {_fmt_list(m['denied_tools'])}"
                   if m["denied_tools"] else "")
                + ")")
        add("")

    add("## Skills")
    add("")
    add("| Skill | Purpose (model-visible) | Risk | Requires actions | Enforced input | Adapter |")
    add("|---|---|---|---|---|---|")
    for s in skills:
        add(
            f"| `{s['id']}` | {_cell(s['purpose'])} | `{s['risk_level']}` | "
            f"{_fmt_list(s['requires_actions'])} | "
            f"{_schema_summary(s['input_schema'])} | `{s['adapter']}` |"
        )
    add("")

    add("## MCP servers")
    add("")
    add("| Server | Purpose (model-visible) | Risk | Transport | Tool surface | Requires actions |")
    add("|---|---|---|---|---|---|")
    for m in mcps:
        surface = (
            _fmt_list(m["allowed_tools"]) if m["tool_surface_bounded"]
            else "**unbounded** _(whatever the server advertises)_"
        )
        if m["denied_tools"]:
            surface += f"<br>denied: {_fmt_list(m['denied_tools'])}"
        add(
            f"| `{m['id']}` | {_cell(m['purpose'])} | `{m['risk_level']}` | "
            f"`{m['transport']}` | {surface} | "
            f"{_fmt_list(m['requires_actions'])} |"
        )
    add("")

    add("## Completeness")
    add("")
    add("The generator globs `skills/*/` and `mcps/*/` and fails if anything on "
        "disk did not reach the registry. This matters because `load_from()` "
        "deliberately logs-and-drops a capability whose adapter will not import "
        "— correct at runtime (one bad skill must not knock out the healthy "
        "ones), dangerous at inventory time, because the capability leaves the "
        "model-facing surface without saying so.")
    add("")
    add("Enforced by `tests/test_tool_catalog.py`. A new skill or MCP cannot be "
        "silently undocumented.")
    add("")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=_OUT,
                    help="output path (default: docs/tool-catalog.md)")
    ap.add_argument("--check", action="store_true",
                    help="fail if the committed file is stale (CI guard)")
    ap.add_argument("--json", action="store_true",
                    help="emit the catalog model as JSON instead of markdown")
    args = ap.parse_args(argv)

    try:
        cat = build()
    except CompletenessError as exc:
        print(f"INCOMPLETE: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(cat, indent=2, sort_keys=True))
        return 0

    text = render(cat)
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"STALE: {args.out} differs from generated — "
                  "re-run `python -m tools.gen_tool_catalog`", file=sys.stderr)
            return 1
        print(f"OK: {args.out} is up to date")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Explicit LF: this artifact is committed, so a regenerate on Windows
    # must not produce a whole-file diff against a regenerate on Linux.
    args.out.write_text(text, encoding="utf-8", newline="\n")
    unbounded = sum(1 for m in cat["mcps"] if not m["tool_surface_bounded"])
    print(f"wrote {args.out} — {len(cat['skills'])} skills, "
          f"{len(cat['mcps'])} mcps ({unbounded} with an unbounded tool surface)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
