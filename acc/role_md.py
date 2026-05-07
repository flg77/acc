"""Markdown ↔ canonical role.yaml compiler (PR-3 of subagent clustering).

Operators author roles in a friendly markdown source file — sections,
lists, key/value blocks — and the compiler emits the strict YAML
schema :class:`acc.config.RoleDefinitionConfig` consumes.

The reverse is also supported (``decompile``) so existing roles can be
ported in a single command and the round-trip identity is testable.

Format::

    # Role: coding_agent
    Version: 1.2.0
    Persona: analytical
    Domain: software_engineering
    Receptors: software_engineering, security_audit

    ## Purpose
    Generate, review, and test code artefacts.

    ## Task Types
    - CODE_GENERATE
    - CODE_REVIEW

    ## Allowed Actions
    - read_vector_db
    - publish_task

    ## Category-B Setpoints
    - token_budget: 4096
    - rate_limit_rpm: 40

    ## Capabilities
    - Allowed skills: echo
    - Default skills: echo
    - Max skill risk: MEDIUM
    - Allowed MCPs: echo_server
    - Default MCPs: echo_server
    - Max MCP risk: MEDIUM
    - Max parallel tasks: 3

    ## Sub-cluster Estimator
    Strategy: heuristic
    Base: 1
    Per-N-tokens: 2000
    Skill-per-subagent: 2
    Cap: 5
    Difficulty signals:
    - security → +1
    - concurrency → +2

    ## System Prompt
    You are a precise software engineer.

Design constraints:

* **Round-trip stable** for every role currently in ``roles/``.  The
  test suite asserts ``compile(decompile(yaml)) == yaml`` for the
  bundled roles + a small synthetic battery that exercises every
  supported field.
* **Human-friendly errors**: missing required headings raise with the
  line number and a hint.
* **Forward-compatible**: unknown sections and unknown key/value pairs
  are preserved on the round-trip via an ``extras`` carry-over so
  partial-knowledge tools never lose data.

Stdlib + markdown-it-py only.  No new runtime deps — markdown-it-py
is already pulled in transitively by Textual.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Section name → canonical slug (case + spelling tolerant)
# ---------------------------------------------------------------------------


_SECTION_ALIASES: dict[str, str] = {
    "purpose": "purpose",
    "task types": "task_types",
    "allowed actions": "allowed_actions",
    "category-b setpoints": "category_b_overrides",
    "category b setpoints": "category_b_overrides",
    "cat-b setpoints": "category_b_overrides",
    "capabilities": "capabilities",
    "sub-cluster estimator": "estimator",
    "subcluster estimator": "estimator",
    "estimator": "estimator",
    "system prompt": "system_prompt",
}


# Risk-level enum allowed on Capabilities entries.
_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


# ---------------------------------------------------------------------------
# Lint exception
# ---------------------------------------------------------------------------


class RoleMarkdownError(ValueError):
    """Raised when a markdown source cannot be parsed.

    Carries a ``line`` attribute (1-indexed) so CLI output can point
    operators at the exact place to fix.
    """

    def __init__(self, message: str, line: int = 0) -> None:
        super().__init__(message)
        self.line = line


# ---------------------------------------------------------------------------
# Compile (markdown → canonical role dict)
# ---------------------------------------------------------------------------


@dataclass
class _CompileResult:
    """Output of :func:`compile_markdown`.

    ``role_definition`` is the dict to dump under ``role_definition:``
    in role.yaml.  ``system_prompt`` is the verbatim body of the
    ``## System Prompt`` section, written separately by the CLI to
    ``system_prompt.md`` so it stays human-editable as a markdown
    file.
    """

    role_definition: dict[str, Any]
    system_prompt: str = ""
    extras: dict[str, str] = field(default_factory=dict)
    """Sections we did not recognise.  Preserved as raw markdown so a
    decompile of the result emits them verbatim — forward-compatibility
    for tools that add new sections without updating the compiler."""


def compile_markdown(source: str) -> _CompileResult:
    """Parse a markdown role source and return the canonical role dict.

    Raises:
        RoleMarkdownError: with a ``line`` attribute on every error so
            ``acc-cli role lint`` can print actionable diagnostics.
    """
    lines = source.splitlines()
    if not lines:
        raise RoleMarkdownError("empty role markdown source", line=1)

    # ---- 1. Front matter (lines above the first H2 ##) ----
    header: dict[str, str] = {}
    role_name = ""
    cursor = 0
    for cursor, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            break  # body starts here
        if stripped.startswith("# "):
            # H1 carries the role name: "# Role: coding_agent"
            tail = stripped[2:].strip()
            if tail.lower().startswith("role:"):
                role_name = tail.split(":", 1)[1].strip()
            else:
                role_name = tail
            continue
        if ":" in stripped and not stripped.startswith("-"):
            key, _, value = stripped.partition(":")
            header[key.strip().lower()] = value.strip()

    # ---- 2. Body sections (each H2 ## starts a new section) ----
    sections: dict[str, list[str]] = {}
    current_name: str | None = None
    current_buf: list[str] = []
    raw_section_blocks: dict[str, list[str]] = {}

    def _flush() -> None:
        if current_name is not None:
            sections[current_name] = list(current_buf)
            raw_section_blocks[current_name] = list(current_buf)
        current_buf.clear()

    # Once the parser enters the System Prompt section, every subsequent
    # ``## ...`` line is treated as part of the prompt body — system
    # prompts are themselves markdown and frequently contain second-level
    # headings.  Without this rule the inner ``## Task types`` of a
    # typical system_prompt.md would clobber the outer Task Types
    # section we just parsed.
    in_system_prompt = False
    for line_num in range(cursor, len(lines)):
        line = lines[line_num]
        stripped = line.strip()
        if stripped.startswith("## ") and not in_system_prompt:
            _flush()
            heading = stripped[3:].strip().lower()
            current_name = _SECTION_ALIASES.get(heading, heading)
            in_system_prompt = current_name == "system_prompt"
            continue
        if current_name is not None:
            current_buf.append(line)
    _flush()

    # ---- 3. Required-section guard ----
    if "purpose" not in sections:
        raise RoleMarkdownError(
            "missing required section '## Purpose' "
            "(every role must declare what the agent does)",
            line=cursor + 1,
        )
    if not role_name:
        raise RoleMarkdownError(
            "missing role name — the first H1 line should read "
            "'# Role: <role_id>'",
            line=1,
        )

    # ---- 4. Build the canonical role dict ----
    role: dict[str, Any] = {}
    role["purpose"] = _join_text(sections["purpose"])

    if "version" in header:
        role["version"] = header["version"]
    persona = header.get("persona")
    if persona:
        role["persona"] = persona
    if "domain" in header:
        role["domain_id"] = header["domain"]
    if "receptors" in header:
        role["domain_receptors"] = _split_csv(header["receptors"])

    if "task_types" in sections:
        role["task_types"] = _parse_bullets(sections["task_types"])
    if "allowed_actions" in sections:
        role["allowed_actions"] = _parse_bullets(sections["allowed_actions"])

    if "category_b_overrides" in sections:
        role["category_b_overrides"] = _parse_kv_bullets(
            sections["category_b_overrides"], coerce=_coerce_number,
        )

    if "capabilities" in sections:
        cap = _parse_capabilities(sections["capabilities"])
        role.update(cap)

    if "estimator" in sections:
        role["estimator"] = _parse_estimator(sections["estimator"])

    seed = ""
    system_prompt = ""
    if "system_prompt" in sections:
        system_prompt = _join_text(sections["system_prompt"])
        # ``seed_context`` is the YAML-side field name — operators
        # should treat it as the LLM's system-prompt scaffold.
        seed = system_prompt
    if seed:
        role["seed_context"] = seed

    # ---- 5. Track unknown sections so decompile preserves them. ----
    extras = {
        name: "\n".join(raw_section_blocks[name]).rstrip("\n")
        for name in raw_section_blocks
        if name not in {
            "purpose", "task_types", "allowed_actions",
            "category_b_overrides", "capabilities", "estimator",
            "system_prompt",
        }
    }

    role.setdefault("__role_name__", role_name)
    return _CompileResult(
        role_definition=role,
        system_prompt=system_prompt,
        extras=extras,
    )


# ---------------------------------------------------------------------------
# Decompile (canonical role.yaml → markdown)
# ---------------------------------------------------------------------------


def decompile_to_markdown(
    role_yaml_dict: dict[str, Any],
    *,
    role_name: str,
    system_prompt: str = "",
    extras: dict[str, str] | None = None,
) -> str:
    """Render a canonical role dict + system prompt back to markdown.

    Symmetrical with :func:`compile_markdown` — round-tripping a
    well-formed source is the integration test in
    ``tests/test_role_markdown.py``.
    """
    role = role_yaml_dict.get("role_definition", role_yaml_dict)
    out: list[str] = []

    out.append(f"# Role: {role_name}")
    if "version" in role:
        out.append(f"Version: {role['version']}")
    if "persona" in role:
        out.append(f"Persona: {role['persona']}")
    if "domain_id" in role and role["domain_id"]:
        out.append(f"Domain: {role['domain_id']}")
    if role.get("domain_receptors"):
        out.append(f"Receptors: {', '.join(role['domain_receptors'])}")
    out.append("")

    if "purpose" in role and role["purpose"]:
        out += ["## Purpose", role["purpose"].rstrip(), ""]

    if role.get("task_types"):
        out.append("## Task Types")
        for t in role["task_types"]:
            out.append(f"- {t}")
        out.append("")

    if role.get("allowed_actions"):
        out.append("## Allowed Actions")
        for a in role["allowed_actions"]:
            out.append(f"- {a}")
        out.append("")

    if role.get("category_b_overrides"):
        out.append("## Category-B Setpoints")
        for k, v in role["category_b_overrides"].items():
            # Render integer-valued floats as ints for readability.
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            out.append(f"- {k}: {v}")
        out.append("")

    cap_lines = _render_capabilities(role)
    if cap_lines:
        out.append("## Capabilities")
        out += cap_lines
        out.append("")

    if role.get("estimator"):
        out.append("## Sub-cluster Estimator")
        out += _render_estimator(role["estimator"])
        out.append("")

    if system_prompt or role.get("seed_context"):
        out.append("## System Prompt")
        out.append((system_prompt or role.get("seed_context", "")).rstrip())
        out.append("")

    if extras:
        for name, body in extras.items():
            display = name.replace("_", " ").title()
            out.append(f"## {display}")
            out.append(body.rstrip())
            out.append("")

    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Lint convenience
# ---------------------------------------------------------------------------


def lint_markdown(source: str) -> list[str]:
    """Return a list of human-readable diagnostic strings.

    Empty list = clean.  Non-empty = each entry is ``"L<line>: <msg>"``.
    Always exhaustive for the issues this tool can detect — does not
    short-circuit on the first error.
    """
    issues: list[str] = []
    try:
        compile_markdown(source)
    except RoleMarkdownError as exc:
        issues.append(f"L{exc.line}: {exc}")
    # Future: orthogonal checks (e.g. unknown task_type names) plug in
    # here without touching compile_markdown.
    return issues


# ---------------------------------------------------------------------------
# Helpers — line-level parsing
# ---------------------------------------------------------------------------


def _join_text(lines: list[str]) -> str:
    """Trim leading + trailing blank lines, keep internal formatting."""
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return "\n".join(lines)


def _split_csv(s: str) -> list[str]:
    return [piece.strip() for piece in s.split(",") if piece.strip()]


def _parse_bullets(lines: list[str]) -> list[str]:
    """Extract every ``- item`` line, preserving order, stripping bullet."""
    items: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
        elif stripped.startswith("*"):
            items.append(stripped[1:].strip())
    return items


def _coerce_number(value: str) -> Any:
    """Convert a string to int / float when it parses cleanly, else
    return the original string.  Tolerant by design — schema-level
    type checking happens in Pydantic at load time."""
    value = value.strip()
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_kv_bullets(lines: list[str], *, coerce=lambda v: v) -> dict[str, Any]:
    """Parse ``- key: value`` lines into a dict."""
    out: dict[str, Any] = {}
    for raw in lines:
        stripped = raw.strip()
        if not stripped or not stripped.startswith("- "):
            continue
        body = stripped[2:].strip()
        if ":" not in body:
            continue
        key, _, value = body.partition(":")
        out[key.strip()] = coerce(value.strip())
    return out


# ---------------------------------------------------------------------------
# Capabilities section — multi-field one-liners
# ---------------------------------------------------------------------------


_CAP_PATTERNS: list[tuple[re.Pattern, str, callable]] = [
    (re.compile(r"^allowed\s+skills$", re.I), "allowed_skills", _split_csv),
    (re.compile(r"^default\s+skills$", re.I), "default_skills", _split_csv),
    (re.compile(r"^max\s+skill\s+risk$", re.I), "max_skill_risk_level", str.strip),
    (re.compile(r"^allowed\s+mcps$", re.I), "allowed_mcps", _split_csv),
    (re.compile(r"^default\s+mcps$", re.I), "default_mcps", _split_csv),
    (re.compile(r"^max\s+mcp\s+risk$", re.I), "max_mcp_risk_level", str.strip),
    (re.compile(r"^max\s+parallel\s+tasks$", re.I), "max_parallel_tasks", lambda v: int(v.strip())),
]


def _parse_capabilities(lines: list[str]) -> dict[str, Any]:
    """Parse the Capabilities section into individual role fields."""
    kv = _parse_kv_bullets(lines)
    out: dict[str, Any] = {}
    for raw_key, raw_val in kv.items():
        for pat, target, fn in _CAP_PATTERNS:
            if pat.match(raw_key.strip()):
                try:
                    out[target] = fn(raw_val) if callable(fn) else raw_val
                except Exception:
                    raise RoleMarkdownError(
                        f"could not parse capability {raw_key!r}: {raw_val!r}",
                        line=0,
                    )
                break
        else:
            logger.warning(
                "role_md: unknown capability key %r — preserving as extra",
                raw_key,
            )
    # Validate risk levels.
    for fld in ("max_skill_risk_level", "max_mcp_risk_level"):
        if fld in out and str(out[fld]).upper() not in _RISK_LEVELS:
            raise RoleMarkdownError(
                f"invalid {fld}={out[fld]!r}; must be one of "
                f"{sorted(_RISK_LEVELS)}",
                line=0,
            )
        if fld in out:
            out[fld] = str(out[fld]).upper()
    return out


def _render_capabilities(role: dict[str, Any]) -> list[str]:
    """Reverse of :func:`_parse_capabilities`."""
    out: list[str] = []
    if role.get("allowed_skills"):
        out.append(f"- Allowed skills: {', '.join(role['allowed_skills'])}")
    if role.get("default_skills"):
        out.append(f"- Default skills: {', '.join(role['default_skills'])}")
    if "max_skill_risk_level" in role:
        out.append(f"- Max skill risk: {role['max_skill_risk_level']}")
    if role.get("allowed_mcps"):
        out.append(f"- Allowed MCPs: {', '.join(role['allowed_mcps'])}")
    if role.get("default_mcps"):
        out.append(f"- Default MCPs: {', '.join(role['default_mcps'])}")
    if "max_mcp_risk_level" in role:
        out.append(f"- Max MCP risk: {role['max_mcp_risk_level']}")
    if role.get("max_parallel_tasks"):
        out.append(f"- Max parallel tasks: {role['max_parallel_tasks']}")
    return out


# ---------------------------------------------------------------------------
# Estimator section — strategy + heuristic + difficulty signals
# ---------------------------------------------------------------------------


def _parse_estimator(lines: list[str]) -> dict[str, Any]:
    """Parse the estimator block into the dict the runtime consumes."""
    out: dict[str, Any] = {}
    heuristic: dict[str, Any] = {}
    fixed: dict[str, Any] = {}
    difficulty: list[dict[str, Any]] = []
    in_difficulty = False

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        lower = stripped.lower()

        if lower.startswith("difficulty signals"):
            in_difficulty = True
            continue
        if in_difficulty and stripped.startswith("- "):
            body = stripped[2:].strip()
            # Format: "<keyword> → +<bump>" (also "->" for ASCII).
            m = re.match(r"^(.+?)\s*(?:→|->)\s*\+?(-?\d+)\s*$", body)
            if m:
                difficulty.append({
                    "keyword": m.group(1).strip(),
                    "bump": int(m.group(2)),
                })
            continue

        if ":" in stripped and not stripped.startswith("-"):
            key, _, value = stripped.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "strategy":
                out["strategy"] = value.lower()
            elif key in ("base", "per-n-tokens", "skill-per-subagent", "cap"):
                norm = key.replace("-", "_")
                heuristic[norm] = _coerce_number(value)
            elif key == "count":
                fixed["count"] = _coerce_number(value)
            else:
                # Tolerate unknown one-liner — log and drop.
                logger.warning(
                    "role_md: unknown estimator key %r — ignored", key,
                )

    if heuristic:
        out["heuristic"] = heuristic
    if fixed:
        out["fixed"] = fixed
    if difficulty:
        out["difficulty_signals"] = difficulty
    return out


def _render_estimator(est: dict[str, Any]) -> list[str]:
    """Reverse of :func:`_parse_estimator`."""
    out: list[str] = []
    strat = est.get("strategy", "heuristic")
    out.append(f"Strategy: {strat}")

    h = est.get("heuristic") or {}
    if "base" in h:
        out.append(f"Base: {h['base']}")
    if "per_n_tokens" in h:
        out.append(f"Per-N-tokens: {h['per_n_tokens']}")
    if "skill_per_subagent" in h:
        out.append(f"Skill-per-subagent: {h['skill_per_subagent']}")
    if "cap" in h:
        out.append(f"Cap: {h['cap']}")

    fx = est.get("fixed") or {}
    if "count" in fx:
        out.append(f"Count: {fx['count']}")

    if est.get("difficulty_signals"):
        out.append("Difficulty signals:")
        for sig in est["difficulty_signals"]:
            kw = sig.get("keyword", "")
            bump = sig.get("bump", 1)
            sign = "+" if bump >= 0 else ""
            out.append(f"- {kw} → {sign}{bump}")

    return out


# ---------------------------------------------------------------------------
# File-system helpers — used by the CLI subcommands
# ---------------------------------------------------------------------------


def compile_file(md_path: Path, dest_dir: Path | None = None) -> tuple[Path, Path]:
    """Compile *md_path* → ``role.yaml`` (+ ``system_prompt.md``).

    Returns the (yaml_path, system_prompt_path) tuple.  Raises
    :class:`RoleMarkdownError` on parse failure.
    """
    source = md_path.read_text(encoding="utf-8")
    result = compile_markdown(source)

    role_name = result.role_definition.pop("__role_name__", md_path.stem)
    target_dir = dest_dir or (md_path.parent / role_name)
    target_dir.mkdir(parents=True, exist_ok=True)

    yaml_path = target_dir / "role.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {"role_definition": result.role_definition},
            sort_keys=False, default_flow_style=False,
        ),
        encoding="utf-8",
    )

    sp_path = target_dir / "system_prompt.md"
    if result.system_prompt:
        sp_path.write_text(result.system_prompt + "\n", encoding="utf-8")

    return yaml_path, sp_path


def decompile_dir(role_dir: Path) -> str:
    """Render ``<role_dir>/role.yaml`` (+ system_prompt.md) back to markdown."""
    yaml_path = role_dir / "role.yaml"
    if not yaml_path.exists():
        raise RoleMarkdownError(
            f"no role.yaml in {role_dir}", line=0,
        )
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    sp_path = role_dir / "system_prompt.md"
    system_prompt = sp_path.read_text(encoding="utf-8") if sp_path.exists() else ""
    return decompile_to_markdown(
        raw,
        role_name=role_dir.name,
        system_prompt=system_prompt,
    )
