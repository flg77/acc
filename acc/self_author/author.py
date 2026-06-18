"""Gap -> draft role pack — proposal 036 Step 2 / PR-2.

``author_role_from_gap`` is the single entrypoint.  Given an operator-APPROVED
``new_role`` :class:`~acc.assistant.gap_analysis.RoleGapFinding`, it:

1. **secrets scan** — redacts the gap evidence BEFORE drafting (016 precedent),
   recording an audit list of what was redacted;
2. **skeleton** — builds a deterministic :class:`~acc.config.RoleDefinitionConfig`
   from the gap's candidate ``requires_skills`` / ``requires_mcps`` + the
   evidence-derived ``task_types`` + sane defaults (this construction is itself
   the Pydantic *shape* gate — option C in proposal 036 §4b);
3. **LLM fill** — an injectable ``llm_fill`` callable fills ``purpose`` (<=200
   chars), ``persona``, ``seed_context`` and the ``role.md`` narrative, bounded
   to the skeleton and prompted from the (redacted) evidence;
4. **validate** — re-validate the filled config through ``RoleDefinitionConfig``
   (baseline shape gate) PLUS an optional 033 WS-A ``capability_validator`` hook
   that engages automatically when that module is promoted to main;
5. **build** — write ``role.yaml`` + ``role.md`` + ``accpkg.yaml`` into a
   gitignored ``proposed/<scope>/<name>-<version>/`` staging dir and build a
   candidate ``.accpkg`` via :func:`acc.pkg.build.build`.

A draft that fails any gate is **REJECTED** — :func:`author_role_from_gap`
returns an :class:`AuthorResult` with ``ok=False`` + a structured ``finding``;
no partial pack is surfaced.

The dispatch path that hands an approved gap to this function is PR-4 — out of
scope here.  This module is pure-Python + filesystem; no NATS, no Redis, and no
*live* LLM (the default ``llm_fill`` only touches a model if the caller passes a
backend; tests pass a deterministic stub).
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from acc.config import RoleDefinitionConfig
from acc.pkg.build import BuildResult, build
from acc.pkg.manifest import AccPkgManifest

logger = logging.getLogger("acc.self_author")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class SelfAuthorError(ValueError):
    """Raised for caller errors that are not a *draft-quality* rejection.

    Used for wrong-shaped INPUT (e.g. a non-``new_role`` gap, an empty
    candidate skill set the caller should never have routed here).  A
    *draft* that merely fails validation is NOT an exception — it comes back
    as :class:`AuthorResult` ``ok=False`` so the caller can record a finding
    and move on.
    """


# The injectable LLM seam.  Given the deterministic skeleton + the redacted
# gap finding, return the prose fields.  Keys consumed:
#   purpose       -> str (clamped to <=200 chars by the caller)
#   persona       -> one of concise|formal|exploratory|analytical
#   seed_context  -> str (the system-prompt scaffold)
#   role_md       -> str (the human-readable narrative for role.md)
# Any missing key falls back to a deterministic default, so a partial fill is
# safe.  Implementations MUST stay bounded to the skeleton (do not invent
# skills/MCPs the gap evidence did not name).
LLMFill = Callable[[RoleDefinitionConfig, "DraftContext"], dict[str, str]]


@dataclass(frozen=True)
class DraftContext:
    """Read-only context handed to ``llm_fill`` (everything already redacted)."""

    suggested_name: str
    goal_summary: str
    rationale: str
    evidence: tuple[dict[str, Any], ...]
    requires_skills: tuple[str, ...]
    requires_mcps: tuple[str, ...]
    task_types: tuple[str, ...]


@dataclass(frozen=True)
class AuthorResult:
    """Outcome of :func:`author_role_from_gap`.

    On success (``ok=True``): ``role_name`` / ``scope`` / ``version`` identify
    the draft, ``staging_dir`` holds the written ``role.yaml`` + ``role.md`` +
    ``accpkg.yaml``, ``accpkg_path`` is the built candidate, and
    ``redactions`` lists any evidence spans scrubbed before drafting.

    On rejection (``ok=False``): ``finding`` is a structured dict the caller
    surfaces (NOT the pack — a rejected draft is never surfaced).  ``stage``
    names where it failed (``secrets`` / ``skeleton`` / ``validate`` /
    ``build``).
    """

    ok: bool
    scope: str
    role_name: str
    version: str
    staging_dir: Optional[Path] = None
    accpkg_path: Optional[Path] = None
    manifest: Optional[AccPkgManifest] = None
    redactions: tuple[str, ...] = ()
    stage: str = ""
    finding: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Defaults / constants
# ---------------------------------------------------------------------------

_PURPOSE_MAX = 200
_VALID_PERSONAS = {"concise", "formal", "exploratory", "analytical"}
_DEFAULT_PERSONA = "analytical"
_DEFAULT_TASK_TYPES = ("ASSIST",)
_DEFAULT_VERSION = "0.1.0"
_DEFAULT_SCOPE = "self"

# Scoped-name component rule mirrors acc.pkg.manifest.SCOPED_NAME_RE so the
# manifest we build can never fail the package-name shape check downstream.
_ROLE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# ---------------------------------------------------------------------------
# Secrets scan (016 precedent)
# ---------------------------------------------------------------------------
#
# There is no dedicated synchronous secrets scanner in-tree on main — the
# only redactor is the *async* PII guardrail (acc.guardrails.pii_detector,
# coroutine API, compliance-config bound).  Per proposal 036's instruction
# ("if no scanner exists, do a minimal redaction + note it") this is a small
# synchronous credential/PII scrubber reusing the same placeholder style as
# pii_detector so audit output is consistent.  When a first-class
# ``acc.secrets_scan`` lands it can replace ``_scan_and_redact`` wholesale.

_SECRET_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # PEM private keys (collapse the whole block).
    ("PRIVATE_KEY", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    )),
    # AWS access key id.
    ("AWS_ACCESS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # GitHub / GitHub-app tokens.
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    # Slack tokens.
    ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    # Generic "key=<secret>" / "token: <secret>" / "password=<secret>" forms.
    ("CREDENTIAL", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|pwd|bearer)\b"
        r"\s*[:=]\s*['\"]?([A-Za-z0-9_\-./+=]{8,})['\"]?",
    )),
    # Bare email addresses (operator-private contact leakage).
    ("EMAIL_ADDRESS", re.compile(
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
    )),
)


def _scan_and_redact(text: str) -> tuple[str, list[str]]:
    """Redact secrets/PII from ``text``.

    Returns ``(redacted_text, hits)`` where ``hits`` is the list of
    ``entity_type`` labels found (the audit trail; never the secret value).
    For the ``CREDENTIAL`` form only the captured secret group is replaced so
    the surrounding key name stays readable.
    """
    hits: list[str] = []
    out = text or ""
    for label, pat in _SECRET_PATTERNS:
        def _repl(m: "re.Match[str]", _label: str = label) -> str:
            hits.append(_label)
            # When the pattern captured a group (CREDENTIAL), redact only the
            # secret value, preserving the key prefix; else redact the match.
            if m.groups():
                whole = m.group(0)
                secret = m.group(1)
                return whole.replace(secret, f"<{_label}>")
            return f"<{_label}>"

        out = pat.sub(_repl, out)
    return out, hits


def _redact_finding(gap: Any) -> tuple[dict[str, Any], list[str]]:
    """Run the secrets scan over the gap's evidence + free-text proposal.

    Returns ``(redacted_view, hits)``.  ``redacted_view`` is a plain-dict
    snapshot of the gap with evidence notes + proposal rationale scrubbed —
    the LLM fill only ever sees this view, never the raw gap.
    """
    data = gap.to_dict() if hasattr(gap, "to_dict") else dict(gap)
    hits: list[str] = []

    # Evidence notes are the main private-context surface (reviewer /
    # compliance_officer findings 019 G5 cited).
    red_evidence: list[dict[str, Any]] = []
    for ev in data.get("evidence", []) or []:
        note = str(ev.get("note", ""))
        red_note, ev_hits = _scan_and_redact(note)
        hits.extend(ev_hits)
        red_evidence.append({**ev, "note": red_note})
    data["evidence"] = red_evidence

    # The proposal rationale is free text too.
    proposal = data.get("proposal") or {}
    new_role = proposal.get("new_role") or {}
    if "rationale" in new_role:
        red_rat, rat_hits = _scan_and_redact(str(new_role["rationale"]))
        hits.extend(rat_hits)
        new_role = {**new_role, "rationale": red_rat}
        proposal = {**proposal, "new_role": new_role}
        data["proposal"] = proposal

    # goal_summary can echo the operator's prompt.
    gs = str(data.get("goal_summary", ""))
    red_gs, gs_hits = _scan_and_redact(gs)
    hits.extend(gs_hits)
    data["goal_summary"] = red_gs

    return data, hits


# ---------------------------------------------------------------------------
# Skeleton construction
# ---------------------------------------------------------------------------


def _extract_new_role_proposal(gap: Any) -> dict[str, Any]:
    """Pull the ``new_role`` sub-dict from the gap's ``proposal`` payload.

    ``gap_analysis`` emits ``proposal == {"new_role": {...}}`` for a
    ``new_role`` gap.  We tolerate a flat proposal dict too (PR-4 may pass a
    hand-built finding).
    """
    proposal = getattr(gap, "proposal", None) or {}
    if "new_role" in proposal and isinstance(proposal["new_role"], dict):
        return dict(proposal["new_role"])
    return dict(proposal)


def _candidate_skills(new_role: dict[str, Any]) -> list[str]:
    """Read the candidate skill ids the gap evidence implies.

    Accepts ``requires_skills`` (036 §Step-2 wording) or ``add_skills``
    (the gap_analysis ``extend_role`` field name, reused by some emitters).
    """
    for key in ("requires_skills", "add_skills", "skills"):
        val = new_role.get(key)
        if val:
            return [str(s).strip() for s in val if str(s).strip()]
    return []


def _candidate_mcps(new_role: dict[str, Any]) -> list[str]:
    for key in ("requires_mcps", "add_mcps", "mcps"):
        val = new_role.get(key)
        if val:
            return [str(m).strip() for m in val if str(m).strip()]
    return []


def _evidence_task_types(gap: Any, new_role: dict[str, Any]) -> list[str]:
    """Derive UPPER_SNAKE task types from the gap.

    Precedence: an explicit ``task_types`` on the proposal wins; else mine the
    goal summary for capitalised verb-ish tokens; else the safe default.
    """
    explicit = new_role.get("task_types")
    if explicit:
        return [_normalise_task_type(t) for t in explicit if str(t).strip()]

    summary = str(getattr(gap, "goal_summary", "") or "")
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", summary) if len(t) > 3]
    # Keep the first two meaningful tokens, upper-snaked, deduped in order.
    seen: list[str] = []
    for t in tokens[:4]:
        tt = _normalise_task_type(t)
        if tt not in seen:
            seen.append(tt)
        if len(seen) >= 2:
            break
    return seen or list(_DEFAULT_TASK_TYPES)


def _normalise_task_type(raw: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(raw).strip()).strip("_")
    return s.upper() or "ASSIST"


def _suggested_name(gap: Any, new_role: dict[str, Any]) -> str:
    """Pick the role's snake_case id from the proposal / goal summary."""
    name = str(new_role.get("suggested_name", "") or "").strip()
    if not name:
        # Derive from the goal summary's first two words.
        summary = str(getattr(gap, "goal_summary", "") or "")
        words = [w for w in re.split(r"[^A-Za-z0-9]+", summary) if w]
        name = "_".join(words[:2]) if words else "drafted_role"
    name = re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_-")
    return name or "drafted_role"


def _max_risk_for(skills: list[str]) -> str:
    """Conservative skill-risk ceiling for the skeleton.

    Self-authored drafts default to MEDIUM (the schema default) so the draft
    never silently grants HIGH/CRITICAL.  Known HIGH-risk skills raise it only
    as far as needed; the operator review (PR-3) is the real gate.
    """
    high = {"shell_exec", "ssh_exec", "python_exec", "fs_write", "test_execution"}
    return "HIGH" if any(s in high for s in skills) else "MEDIUM"


def _build_skeleton(gap: Any, new_role: dict[str, Any]) -> RoleDefinitionConfig:
    """Construct the deterministic RoleDefinitionConfig skeleton.

    Constructing the Pydantic model IS the baseline shape gate — a malformed
    skeleton raises ``pydantic.ValidationError`` here, which the caller turns
    into a structured rejection.
    """
    skills = _candidate_skills(new_role)
    mcps = _candidate_mcps(new_role)
    task_types = _evidence_task_types(gap, new_role)

    return RoleDefinitionConfig(
        # purpose/persona/seed_context are LLM-filled below; start with safe
        # non-empty-on-fill defaults.  purpose stays "" here so a fill that
        # omits it is caught by the post-fill validation as an empty draft.
        purpose="",
        persona=_DEFAULT_PERSONA,
        task_types=task_types,
        seed_context="",
        version=_DEFAULT_VERSION,
        allowed_skills=skills,
        default_skills=list(skills),
        allowed_mcps=mcps,
        default_mcps=list(mcps),
        max_skill_risk_level=_max_risk_for(skills),  # type: ignore[arg-type]
        # Self-authored drafts inhabit the gap's domain when named, else
        # uncategorised; reasoning_trace on so the operator sees the draft
        # reason during review.
        domain_id=str(new_role.get("domain_id", "") or ""),
        reasoning_trace=True,
    )


# ---------------------------------------------------------------------------
# LLM fill (default seam) + application
# ---------------------------------------------------------------------------


def default_llm_fill(backend: Any = None) -> LLMFill:
    """Return the default ``llm_fill`` callable.

    The repo's LLM seam is :class:`acc.backends.LLMBackend` — an *async*
    ``complete(system, user, response_schema=None) -> dict``.  Wrapping an
    async backend in a sync callable here would need an event loop and a live
    model, which we deliberately keep out of this pure module (and out of
    tests).  So:

    * ``backend is None`` (the default) -> a **deterministic, no-LLM** fill
      that derives readable prose from the skeleton + gap.  This keeps
      ``author_role_from_gap`` usable with zero LLM wiring, and is exactly
      what the tests exercise.
    * a non-None ``backend`` -> PR-4 supplies a thin sync adapter (e.g. via
      ``asyncio.run``) when it wires the real model in; this module does not
      assume a loop is free to spin.

    Keeping the seam injectable is the whole point: callers pass their own
    ``llm_fill`` (reviewer's model per 036 §8 Q2) without this module taking a
    hard dependency on any backend.
    """

    def _fill(skeleton: RoleDefinitionConfig, ctx: DraftContext) -> dict[str, str]:
        if backend is not None:  # pragma: no cover - PR-4 wires the real model
            raise NotImplementedError(
                "default_llm_fill(backend=...) is a PR-4 hook; pass an explicit "
                "llm_fill callable to author_role_from_gap until then"
            )
        name_h = ctx.suggested_name.replace("_", " ")
        skills_h = ", ".join(ctx.requires_skills) or "no extra skills"
        mcps_h = ", ".join(ctx.requires_mcps) or "no MCP servers"
        tasks_h = ", ".join(ctx.task_types)
        purpose = (
            f"Handle {name_h} work the existing roster cannot cover "
            f"({ctx.goal_summary or 'an uncovered goal'})."
        )
        seed = (
            f"You are the ACC {name_h} agent. {ctx.rationale or 'No role covered '
            'this goal, so this role was drafted to fill the gap.'} "
            f"Accept task types: {tasks_h}. You may use: {skills_h}; "
            f"MCP servers: {mcps_h}. Output JSON with a 'confidence' field; "
            f"externalize your reasoning before the answer."
        )
        role_md = (
            f"# Role: {ctx.suggested_name}\n\n"
            f"## Purpose\n{purpose}\n\n"
            f"## Why this role exists\n"
            f"{ctx.rationale or 'Drafted from an approved ROLE_GAP finding.'}\n\n"
            f"## Task Types\n"
            + "".join(f"- {t}\n" for t in ctx.task_types)
            + "\n## Capabilities\n"
            f"- Allowed skills: {skills_h}\n"
            f"- Allowed MCPs: {mcps_h}\n\n"
            f"## Evidence\n"
            + (
                "".join(
                    f"- ({e.get('source', '?')}) {e.get('note', '')}\n"
                    for e in ctx.evidence
                )
                or "- (none cited)\n"
            )
        )
        return {
            "purpose": purpose,
            "persona": skeleton.persona,
            "seed_context": seed,
            "role_md": role_md,
        }

    return _fill


def _apply_fill(
    skeleton: RoleDefinitionConfig, fill: dict[str, str]
) -> tuple[RoleDefinitionConfig, str]:
    """Merge the LLM fill into the skeleton + return ``(role_def, role_md)``.

    Bounded: only ``purpose`` / ``persona`` / ``seed_context`` are accepted
    into the config (the LLM cannot widen skills/MCPs/risk beyond the
    skeleton).  ``purpose`` is clamped to <=200 chars; an out-of-enum persona
    falls back to the skeleton's.
    """
    purpose = str(fill.get("purpose", "")).strip()[:_PURPOSE_MAX]
    persona = str(fill.get("persona", "")).strip().lower()
    if persona not in _VALID_PERSONAS:
        persona = skeleton.persona
    seed = str(fill.get("seed_context", "")).strip()
    role_md = str(fill.get("role_md", "")).strip()

    role_def = skeleton.model_copy(update={
        "purpose": purpose,
        "persona": persona,
        "seed_context": seed,
    })
    return role_def, role_md


# ---------------------------------------------------------------------------
# Validation — Pydantic shape gate + optional 033 WS-A hook
# ---------------------------------------------------------------------------


def _validate_draft(role_def: RoleDefinitionConfig, role_md: str) -> list[str]:
    """Return a list of validation errors ([] == the draft passes).

    Gate 1 (baseline, always on): re-validate through ``RoleDefinitionConfig``
    + assert the LLM actually filled the human-facing fields (an empty draft is
    rejected, not surfaced).

    Gate 2 (optional, auto-engaging): the 033 WS-A capability validator.  It is
    ABSENT on main (it lives on a spearhead feature branch), so we import it in
    a try/except — when it is promoted, the deeper check engages with zero code
    change here.
    """
    errors: list[str] = []

    # Gate 1a — Pydantic shape (re-validate the *merged* config).
    try:
        RoleDefinitionConfig.model_validate(role_def.model_dump())
    except Exception as exc:  # noqa: BLE001 - turn any validation error into a finding
        errors.append(f"role_definition shape invalid: {exc}")
        return errors  # nothing else is meaningful if the shape is broken

    # Gate 1b — non-empty required prose + at least one capability.
    if not role_def.purpose.strip():
        errors.append("purpose is empty (LLM fill did not produce a purpose)")
    if not role_def.task_types:
        errors.append("task_types is empty (no routable task type)")
    if not role_def.allowed_skills and not role_def.allowed_mcps:
        errors.append(
            "draft grants no skills and no MCPs — a new_role gap must carry at "
            "least one candidate capability from the evidence"
        )
    if not role_md.strip():
        errors.append("role.md narrative is empty")

    # Gate 2 — 033 WS-A deeper capability validation (optional).
    # TODO(036): WS-A capability_validator — deeper check when promoted to main.
    try:
        from acc.capability_validator import validate_role_capabilities  # type: ignore
    except Exception:  # noqa: BLE001 - module absent on main; degrade cleanly
        logger.debug(
            "self_author: 033 WS-A capability_validator not present; "
            "using RoleDefinitionConfig shape gate only"
        )
    else:  # pragma: no cover - exercised only once WS-A is promoted
        try:
            ws_a_errors = validate_role_capabilities(role_def) or []
        except Exception as exc:  # noqa: BLE001
            ws_a_errors = [f"capability_validator raised: {exc}"]
        errors.extend(str(e) for e in ws_a_errors)

    return errors


# ---------------------------------------------------------------------------
# Build the candidate pack
# ---------------------------------------------------------------------------


def _packages_root(packages_root: Optional[Path]) -> Path:
    """Resolve the ``proposed/`` staging root.

    Precedence: explicit ``packages_root`` arg > repo-root ``proposed/``.
    The directory is gitignored (see the ``proposed/`` rule in ``.gitignore``).
    """
    if packages_root is not None:
        return Path(packages_root).resolve()
    # Repo root is three levels up from this file: acc/self_author/author.py.
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "proposed"


def _write_and_build(
    role_def: RoleDefinitionConfig,
    role_md: str,
    *,
    scope: str,
    role_name: str,
    version: str,
    rationale: str,
    proposed_root: Path,
) -> tuple[Path, BuildResult]:
    """Materialise role.yaml + role.md + accpkg.yaml and build the .accpkg.

    Layout::

        proposed/<scope>/<name>-<version>/
            accpkg.yaml
            roles/<name>/role.yaml
            roles/<name>/role.md
        proposed/<scope>/<name>-<version>.accpkg
    """
    scope_dir = proposed_root / scope
    staging = scope_dir / f"{role_name}-{version}"
    # A fresh draft replaces any stale staging dir for the same name+version so
    # re-runs are idempotent (the build refuses a pre-stamped manifest).
    if staging.exists():
        shutil.rmtree(staging)
    role_dir = staging / "roles" / role_name
    role_dir.mkdir(parents=True, exist_ok=True)

    (role_dir / "role.yaml").write_text(
        yaml.safe_dump(
            {"role_definition": role_def.model_dump(mode="json")},
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    (role_dir / "role.md").write_text(role_md.rstrip() + "\n", encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "name": f"@{scope}/{role_name}",
        "version": version,
        "description": (rationale or f"Self-authored draft for {role_name}.")[:200],
        "depends_on": [],
        "skills": [],
        "mcps": [],
        "roles": [{"name": role_name, "path": f"roles/{role_name}/role.yaml"}],
    }
    (staging / "accpkg.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )

    out_path = scope_dir / f"{role_name}-{version}.accpkg"
    result = build(staging, out_path)
    return staging, result


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def author_role_from_gap(
    gap: Any,
    *,
    llm_fill: Optional[LLMFill] = None,
    packages_root: Optional[Path] = None,
    scope: str = _DEFAULT_SCOPE,
    backend: Any = None,
) -> AuthorResult:
    """Turn an APPROVED ``new_role`` gap finding into a draft ``.accpkg``.

    Parameters
    ----------
    gap:
        An operator-APPROVED :class:`~acc.assistant.gap_analysis.RoleGapFinding`
        (or a dict-like with the same ``to_dict`` shape) whose ``gap_kind`` is
        ``"new_role"``.  ``extend_role`` / ``infuse_known`` route to PR-1's
        paths, not here.
    llm_fill:
        Injectable prose filler (see :data:`LLMFill`).  Defaults to
        :func:`default_llm_fill` (deterministic, no live model).  Tests pass a
        stub; PR-4 passes a backend-backed adapter.
    packages_root:
        Override for the gitignored ``proposed/`` staging root (tests point it
        at ``tmp_path``).  Defaults to ``<repo>/proposed``.
    scope:
        Package scope for the draft (``@<scope>/<name>``).  Default ``self``.
    backend:
        Forwarded to :func:`default_llm_fill` only when ``llm_fill`` is None;
        ignored otherwise.

    Returns
    -------
    AuthorResult
        ``ok=True`` with the staged pack on success; ``ok=False`` with a
        structured ``finding`` (and no surfaced pack) on rejection.

    Raises
    ------
    SelfAuthorError
        Only for wrong-shaped INPUT (non-``new_role`` gap, malformed scope).
        Draft-quality failures come back as ``ok=False``, never as exceptions.
    """
    gap_kind = getattr(gap, "gap_kind", None)
    if gap_kind is None and isinstance(gap, dict):
        gap_kind = gap.get("gap_kind")
    if gap_kind != "new_role":
        raise SelfAuthorError(
            f"author_role_from_gap only handles new_role gaps; got {gap_kind!r} "
            "(extend_role/infuse_known route to the PR-1 install paths)"
        )

    scope = scope.lstrip("@").strip()
    if not _SCOPE_RE.match(scope):
        raise SelfAuthorError(f"invalid package scope: {scope!r}")

    new_role = _extract_new_role_proposal(gap)
    role_name = _suggested_name(gap, new_role)
    version = str(new_role.get("version", "") or _DEFAULT_VERSION).strip()
    proposed_root = _packages_root(packages_root)

    # --- Stage 1: secrets scan (BEFORE any drafting) ---
    redacted_view, redaction_hits = _redact_finding(gap)
    redactions = tuple(sorted(set(redaction_hits)))
    if redactions:
        logger.info(
            "self_author: redacted %s from gap evidence before drafting %r",
            list(redactions), role_name,
        )

    def _reject(stage: str, errors: list[str]) -> AuthorResult:
        finding = {
            "kind": "self_author_rejected",
            "stage": stage,
            "scope": scope,
            "role_name": role_name,
            "version": version,
            "errors": errors,
            "redactions": list(redactions),
            "source": "self_author",
        }
        logger.warning(
            "self_author: REJECTED draft %r at %s: %s", role_name, stage, errors
        )
        return AuthorResult(
            ok=False, scope=scope, role_name=role_name, version=version,
            redactions=redactions, stage=stage, finding=finding,
        )

    if not _ROLE_NAME_RE.match(role_name):
        return _reject("skeleton", [f"derived role name not usable: {role_name!r}"])

    # --- Stage 2: deterministic skeleton (Pydantic shape gate) ---
    try:
        skeleton = _build_skeleton(gap, new_role)
    except Exception as exc:  # noqa: BLE001 - ValidationError -> finding
        return _reject("skeleton", [f"skeleton construction failed: {exc}"])

    # --- Stage 3: LLM fill (bounded; injectable) ---
    fill_fn = llm_fill or default_llm_fill(backend=backend)
    ctx = DraftContext(
        suggested_name=role_name,
        goal_summary=str(redacted_view.get("goal_summary", "")),
        rationale=str(new_role.get("rationale", "") or ""),
        evidence=tuple(redacted_view.get("evidence", []) or ()),
        requires_skills=tuple(skeleton.allowed_skills),
        requires_mcps=tuple(skeleton.allowed_mcps),
        task_types=tuple(skeleton.task_types),
    )
    try:
        fill = fill_fn(skeleton, ctx) or {}
    except Exception as exc:  # noqa: BLE001 - a bad fill is a rejection, not a crash
        return _reject("validate", [f"llm_fill raised: {exc}"])
    role_def, role_md = _apply_fill(skeleton, fill)

    # --- Stage 4: validate (shape gate + optional WS-A hook) ---
    errors = _validate_draft(role_def, role_md)
    if errors:
        return _reject("validate", errors)

    # --- Stage 5: build the candidate .accpkg into proposed/ ---
    try:
        staging, result = _write_and_build(
            role_def, role_md,
            scope=scope, role_name=role_name, version=version,
            rationale=str(new_role.get("rationale", "") or ""),
            proposed_root=proposed_root,
        )
    except Exception as exc:  # noqa: BLE001 - build failure -> finding
        return _reject("build", [f"pack build failed: {exc}"])

    logger.info(
        "self_author: drafted candidate pack @%s/%s@%s at %s",
        scope, role_name, version, result.output_path,
    )
    return AuthorResult(
        ok=True,
        scope=scope,
        role_name=role_name,
        version=version,
        staging_dir=staging,
        accpkg_path=result.output_path,
        manifest=result.manifest,
        redactions=redactions,
        stage="built",
    )
