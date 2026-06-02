"""Flip ``os_basics: true`` on every shipped role.yaml + add the
universal research MCP triad (arxiv, wikipedia, web_fetch) to every
role's ``allowed_mcps``.  Also opts the engineering family into
``shell_exec`` + the ``execute_shell`` action.

Idempotent: re-running on an already-flipped role.yaml is a no-op
(comments preserved; line-shaped insert near the existing field).

OpenSpec `20260603-capability-pool` Phase 1.4 + 1.5.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROLES = REPO / "roles"

# Skip these — substrate, not shipped agents.
SKIP = frozenset({"_base", "TEMPLATE"})

# Universal MCP triad — every role gets these.
UNIVERSAL_MCPS = ("arxiv", "wikipedia", "web_fetch")

# Engineering family: gets shell_exec + execute_shell action + raised
# max_skill_risk_level.
ENG_FAMILY = frozenset({
    "coding_agent",
    "coding_agent_architect", "coding_agent_dependency",
    "coding_agent_implementer", "coding_agent_reviewer",
    "coding_agent_tester",
    "devops_engineer", "data_engineer", "ml_engineer",
})


def _flip_one(path: Path) -> tuple[bool, list[str]]:
    """Return (changed, notes).  Pure text edit so per-file comments
    are preserved."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    notes: list[str] = []
    changed = False
    role_name = path.parent.name

    # ---- 1. os_basics: true ----
    if not any(line.lstrip().startswith("os_basics:") for line in lines):
        # Insert after the ``role_definition:`` block's persona line if
        # we can find it, else just at the bottom of role_definition.
        for i, line in enumerate(lines):
            if line.startswith("  persona:") or line.startswith("  task_types:"):
                # Find the end of this top-level role_definition block.
                insert_idx = i
                while insert_idx + 1 < len(lines) and lines[insert_idx + 1].startswith("  "):
                    insert_idx += 1
                lines.insert(
                    insert_idx + 1,
                    "  # OpenSpec `20260603-capability-pool` Phase 1.4.\n",
                )
                lines.insert(insert_idx + 2, "  os_basics: true\n")
                changed = True
                notes.append("added os_basics")
                break

    # ---- 2. allowed_mcps universal triad ----
    # Find an existing allowed_mcps: block or insert one.
    has_block = any(line.lstrip().startswith("allowed_mcps:") for line in lines)
    if has_block:
        # Add any missing universal MCPs into the existing block.
        for mcp in UNIVERSAL_MCPS:
            already = any(
                line.strip() in (f"- {mcp}", f"- \"{mcp}\"", f"- '{mcp}'")
                for line in lines
            )
            if already:
                continue
            for i, line in enumerate(lines):
                if line.lstrip().startswith("allowed_mcps:"):
                    indent = "  " if line.startswith("  ") else ""
                    lines.insert(i + 1, f"{indent}  - {mcp}\n")
                    changed = True
                    notes.append(f"added {mcp} to allowed_mcps")
                    break
    else:
        # Find the end of the role_definition: block.
        in_role_def = False
        for i, line in enumerate(lines):
            if line.startswith("role_definition:"):
                in_role_def = True
                continue
            if in_role_def and line and not line.startswith(" ") and not line.startswith("#"):
                # Out of the role_definition block — insert just above.
                insert_lines = ["  # OpenSpec `20260603-capability-pool` Phase 1.5.\n",
                                "  allowed_mcps:\n"]
                for m in UNIVERSAL_MCPS:
                    insert_lines.append(f"    - {m}\n")
                for off, l in enumerate(insert_lines):
                    lines.insert(i + off, l)
                changed = True
                notes.append("created allowed_mcps")
                break
        else:
            if in_role_def:
                # File ended inside the block.
                insert_lines = ["  # OpenSpec `20260603-capability-pool` Phase 1.5.\n",
                                "  allowed_mcps:\n"]
                for m in UNIVERSAL_MCPS:
                    insert_lines.append(f"    - {m}\n")
                lines.extend(insert_lines)
                changed = True
                notes.append("appended allowed_mcps")

    # ---- 3. Engineering family — shell_exec + execute_shell + risk ----
    if role_name in ENG_FAMILY:
        # shell_exec into allowed_skills.
        has_shell = any(
            line.strip() in ("- shell_exec",) for line in lines
        )
        if not has_shell:
            for i, line in enumerate(lines):
                if line.lstrip().startswith("allowed_skills:"):
                    indent = "  " if line.startswith("  ") else ""
                    lines.insert(i + 1, f"{indent}  - shell_exec\n")
                    changed = True
                    notes.append("added shell_exec")
                    break
            else:
                # No allowed_skills block — create one.
                in_role_def = False
                for i, line in enumerate(lines):
                    if line.startswith("role_definition:"):
                        in_role_def = True
                        continue
                    if in_role_def and line and not line.startswith(" ") and not line.startswith("#"):
                        lines.insert(i, "  allowed_skills:\n    - shell_exec\n")
                        changed = True
                        notes.append("created allowed_skills with shell_exec")
                        break
        # execute_shell action.
        has_action = any(
            line.strip() == "- execute_shell" for line in lines
        )
        if not has_action:
            for i, line in enumerate(lines):
                if line.lstrip().startswith("allowed_actions:"):
                    indent = "  " if line.startswith("  ") else ""
                    lines.insert(i + 1, f"{indent}  - execute_shell\n")
                    changed = True
                    notes.append("added execute_shell action")
                    break
        # max_skill_risk_level: HIGH.
        risk_set = False
        for i, line in enumerate(lines):
            if line.lstrip().startswith("max_skill_risk_level:"):
                if "HIGH" not in line and "CRITICAL" not in line:
                    indent = line[: len(line) - len(line.lstrip())]
                    lines[i] = f"{indent}max_skill_risk_level: HIGH\n"
                    changed = True
                    notes.append("raised max_skill_risk_level to HIGH")
                risk_set = True
                break
        if not risk_set:
            for i, line in enumerate(lines):
                if line.lstrip().startswith("allowed_skills:"):
                    indent = "  " if line.startswith("  ") else ""
                    lines.insert(i, f"{indent}max_skill_risk_level: HIGH\n")
                    changed = True
                    notes.append("set max_skill_risk_level: HIGH")
                    break

    if changed:
        path.write_text("".join(lines), encoding="utf-8")
    return changed, notes


def main() -> int:
    if not ROLES.is_dir():
        print(f"roles/ not found at {ROLES}", file=sys.stderr)
        return 1
    n_flipped = 0
    for child in sorted(ROLES.iterdir()):
        if not child.is_dir() or child.name in SKIP:
            continue
        ry = child / "role.yaml"
        if not ry.exists():
            continue
        changed, notes = _flip_one(ry)
        status = "FLIPPED" if changed else "skip   "
        print(f"  {status}  {child.name:34s} {', '.join(notes) if notes else ''}")
        if changed:
            n_flipped += 1
    print(f"\nflipped {n_flipped} role.yaml files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
