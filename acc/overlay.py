"""Personalization overlay resolver (proposal ``agent-personalization-overlay``)
— P0: layer human-authored ``soul.md`` / ``collective.md`` / ``AGENTS.md`` files
onto a role's *signed envelope* at prompt-assembly time.

The model is **role-scoped** (see ``docs/agent-personalization-overlay-DRAFT.md``
**§0**): a role is a self-contained directory and its overlay files live in it::

    roles/<role-name>/
      role.yaml  role.md     # signed, IMMUTABLE — editing breaks the signature
      AGENTS.md  soul.md      # user-editable overlay; picked up at runtime init
      skills/    mcp/         # role-local capability defs (shipped=signed; user-added=local)

* ``role.(yaml,md)`` is the **signed package** — the capability *envelope*
  (``allowed_skills`` / ``allowed_mcps`` / ``max_skill_risk_level``), identity,
  and safety floor.  These are Tier-0 and **never** touched by an overlay.
* ``soul.md`` is the **role's persona** (voice, ``user_profile``), user-tunable
  *per role* — not a user-global voice.  Mechanically it applies last so voice
  wins; its scope is the role.
* ``AGENTS.md`` is **role-scoped** (one per role dir): operational context +
  within-envelope toggles.
* ``collective.md`` stays **agentset-scoped** (with ``collective.yaml``), not in
  the role dir.

The overlay files are **config within the envelope** — they may append Tier-2
*context/voice* and toggle Tier-1 *activation* (enable an ``allowed``-but-default-off
capability, or narrow the default set), but **never** widen ``allowed_*`` or
raise the risk ceiling.  An out-of-envelope ``enable`` is **dropped + recorded**.

**Separately** (§0.5), the per-role ``skills/``/``mcp/`` dir is a governed *local
capability surface*: a **user-added** def present there can be granted **for this
one agent only** via the operator's ``allow_unsigned`` flag — recorded as a
``LocalGrant`` (audit-logged, constrained tier, never silent, never prod-default;
the caller is responsible for only passing ``allow_unsigned=True`` when
operator-approved and non-prod).  This **refines** (does not break) the
"overlays never widen ``allowed_*``" invariant: the overlay *files* still cannot
widen — the role-dir presence + ``allow_unsigned`` is the separate, explicit gate.
Without ``allow_unsigned`` the out-of-envelope enable is dropped, exactly as
before (→ capability-gap candidate for the ``role_lifecycle`` skill / signed infuse).

The resolver is **pure** — it takes the role, the raw overlay text, and the set
of locally-available def ids, and returns an :class:`EffectiveProfile`.  File IO
lives at the edge (:func:`load_overlay_sources` / :func:`discover_local_capabilities`),
so the merge + ceiling logic is unit-testable without a workspace.  Merge order
(each layer overrides the previous, all under the Tier-0 ceiling)::

    role defaults  →  collective.md (team)  →  AGENTS.md (this agent)  →  soul.md (role persona)

Precedence is two axes: operational/capability toggles follow most-specific-wins
(AGENTS.md > collective.md); voice/identity follows ``soul.md`` (the role persona).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("acc.overlay")


# ---------------------------------------------------------------------------
# Layer model
# ---------------------------------------------------------------------------

# Overlay layers, in merge order (later overrides earlier). ``scope`` is the
# precedence axis the layer owns; ``filename`` is its default on-disk name.
LAYER_COLLECTIVE = "collective.md"
LAYER_AGENTS = "AGENTS.md"
LAYER_SOUL = "soul.md"

# Order matters: collective (team) first, then the per-agent file, then the
# role's soul last so its persona/voice wins. Capability toggles still resolve
# most-specific-wins because AGENTS.md is applied after collective.md.
_MERGE_ORDER = (LAYER_COLLECTIVE, LAYER_AGENTS, LAYER_SOUL)

# Front-matter keys the overlay is allowed to set. Anything else is reported as
# an unknown key by :func:`validate_overlay`. Keys that would breach Tier-0 are
# listed in ``_FORBIDDEN_KEYS`` so we can reject them *loudly* rather than
# silently ignore — an operator writing ``allowed_skills:`` in an overlay must
# learn it has no effect.
_TOGGLE_KEYS = frozenset(
    {
        "enable_skills",
        "disable_skills",
        "enable_mcps",
        "disable_mcps",
        "user_profile",
        "verbosity",
        "proactivity",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "allowed_skills",
        "allowed_mcps",
        "allowed_actions",
        "max_skill_risk_level",
        "persona",
        "purpose",
        "policy_enabled",
        "category_b_overrides",
    }
)

_KNOWN_PROFILES = ("novice", "intermediate", "expert", "operator")


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OverlaySource:
    """One overlay file's parsed contents.

    ``front_matter`` is the YAML mapping between the leading ``---`` fences (or
    ``{}`` when absent / malformed); ``body`` is the markdown after it.
    ``layer`` is one of the ``LAYER_*`` constants; ``origin`` is a human label
    (usually the path) for provenance + diagnostics.
    """

    layer: str
    origin: str
    front_matter: dict[str, Any] = field(default_factory=dict)
    body: str = ""


@dataclass(frozen=True)
class Dropped:
    """An overlay request that was refused (out-of-envelope or forbidden)."""

    item: str
    layer: str
    reason: str


@dataclass(frozen=True)
class LocalGrant:
    """A capability granted from the role's own ``skills/``/``mcp/`` dir.

    Out-of-envelope (not in ``allowed_*``) but present as a *local* def in the
    role directory and admitted via the operator's ``allow_unsigned`` flag —
    granted for **this one agent only**, at a constrained/unsigned trust tier,
    audit-logged.  ``kind`` is ``"skill"`` or ``"mcp"``.
    """

    item: str
    layer: str
    kind: str


@dataclass(frozen=True)
class EffectiveProfile:
    """Resolved view of a role after overlays, under the Tier-0 ceiling.

    ``effective_default_skills`` / ``effective_default_mcps`` are the advertised
    sets the prompt should use (always ``⊆`` the role's ``allowed_*``).
    ``provenance`` maps each advertised skill/mcp id to the layer that put it
    there (``"role default"`` / a ``LAYER_*`` name).  ``dropped`` records every
    refused request — surfaced in logs + the effective-profile dump so an
    operator can see what the envelope rejected.  ``block`` is the fenced
    markdown to inject into the system prompt (empty when no overlay applies).
    """

    effective_default_skills: list[str]
    effective_default_mcps: list[str]
    provenance: dict[str, str] = field(default_factory=dict)
    dropped: list[Dropped] = field(default_factory=list)
    local_grants: list[LocalGrant] = field(default_factory=list)
    user_profile: Optional[str] = None
    layers: list[str] = field(default_factory=list)
    block: str = ""

    def local_grant_skill_ids(self) -> list[str]:
        """Ids granted local-unsigned from the role dir's ``skills/``."""
        return [g.item for g in self.local_grants if g.kind == "skill"]

    def local_grant_mcp_ids(self) -> list[str]:
        """Ids granted local-unsigned from the role dir's ``mcp/``."""
        return [g.item for g in self.local_grants if g.kind == "mcp"]

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable dump for the Compliance/Soma effective-profile view."""
        return {
            "effective_default_skills": list(self.effective_default_skills),
            "effective_default_mcps": list(self.effective_default_mcps),
            "provenance": dict(self.provenance),
            "dropped": [
                {"item": d.item, "layer": d.layer, "reason": d.reason}
                for d in self.dropped
            ],
            "local_grants": [
                {"item": g.item, "layer": g.layer, "kind": g.kind}
                for g in self.local_grants
            ],
            "user_profile": self.user_profile,
            "layers": list(self.layers),
        }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_overlay(layer: str, text: str, *, origin: str = "") -> OverlaySource:
    """Split an overlay file into YAML front-matter + markdown body.

    Front-matter is an optional block delimited by a leading ``---`` line and a
    closing ``---`` line.  A malformed / non-mapping front-matter degrades to
    ``{}`` (logged) rather than raising — an overlay must never break the prompt
    path.
    """
    origin = origin or layer
    front: dict[str, Any] = {}
    body = text

    stripped = text.lstrip("﻿")  # tolerate a BOM
    # Only treat a leading '---' as a fence (ignore '---' horizontal rules mid-doc).
    lines = stripped.splitlines()
    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                fm_text = "\n".join(lines[1:idx])
                body = "\n".join(lines[idx + 1 :])
                try:
                    import yaml  # noqa: PLC0415 — optional-at-import-time

                    loaded = yaml.safe_load(fm_text) if fm_text.strip() else {}
                    if isinstance(loaded, dict):
                        front = loaded
                    elif loaded is not None:
                        logger.debug(
                            "overlay %s: front-matter is not a mapping; ignoring",
                            origin,
                        )
                except Exception:
                    logger.debug(
                        "overlay %s: front-matter parse failed; ignoring",
                        origin,
                        exc_info=True,
                    )
                break

    return OverlaySource(
        layer=layer, origin=origin, front_matter=front, body=body.strip()
    )


def _as_str_list(value: Any) -> list[str]:
    """Coerce a front-matter scalar/list into a clean list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for v in value:
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out
    return []


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _resolve_set(
    *,
    base: list[str],
    allowed: list[str],
    sources: list[OverlaySource],
    enable_key: str,
    disable_key: str,
    provenance: dict[str, str],
    dropped: list[Dropped],
    kind: str,
    local: frozenset[str] = frozenset(),
    allow_unsigned: bool = False,
    local_grants: Optional[list[LocalGrant]] = None,
) -> list[str]:
    """Apply enable/disable toggles to ``base`` under the ``allowed`` ceiling.

    Returns the deterministic effective list: original ``base`` order (minus
    anything disabled) followed by newly-enabled ids.  An enable outside
    ``allowed`` is dropped (recorded), never granted — **except** an enable for
    an id present in ``local`` (a def in the role dir's ``skills/``/``mcp/``)
    when ``allow_unsigned`` is set: that is granted for this agent only, at an
    unsigned/constrained tier, and recorded as a :class:`LocalGrant`.
    """
    allowed_set = set(allowed)
    granted_local: set[str] = set()
    result: list[str] = []
    for sid in base:
        if sid in allowed_set:
            result.append(sid)
            provenance.setdefault(sid, "role default")
    result_set = set(result)

    for src in sources:  # merge order: later layers win
        for sid in _as_str_list(src.front_matter.get(disable_key)):
            if sid in result_set:
                result_set.discard(sid)
                result = [s for s in result if s != sid]
                provenance[sid] = f"disabled by {src.layer}"
        for sid in _as_str_list(src.front_matter.get(enable_key)):
            if sid not in allowed_set:
                # §0.5 — a user-added def in the role dir, admitted by the
                # operator's allow_unsigned flag, grants for THIS agent only.
                if allow_unsigned and sid in local:
                    if sid not in result_set:
                        result.append(sid)
                        result_set.add(sid)
                    granted_local.add(sid)
                    provenance[sid] = f"{src.layer} (local-unsigned, role dir)"
                    if local_grants is not None:
                        local_grants.append(
                            LocalGrant(item=sid, layer=src.layer, kind=kind)
                        )
                    logger.warning(
                        "overlay %s: LOCAL-UNSIGNED %s grant '%s' "
                        "(allow_unsigned; this agent only; audit)",
                        src.layer,
                        kind,
                        sid,
                    )
                    continue
                dropped.append(
                    Dropped(
                        item=sid,
                        layer=src.layer,
                        reason=(
                            f"{kind} '{sid}' is outside the role envelope "
                            f"(not in allowed_{kind}s); overlays cannot widen the "
                            f"ceiling — propose a signed infuse, or add it to the "
                            f"role's {kind}/ dir and re-resolve with allow_unsigned "
                            f"(operator-gated, this agent only)"
                        ),
                    )
                )
                logger.info(
                    "overlay %s: dropped out-of-envelope %s enable '%s'",
                    src.layer,
                    kind,
                    sid,
                )
                continue
            if sid not in result_set:
                result.append(sid)
                result_set.add(sid)
            provenance[sid] = src.layer

    # Final safety net: never advertise anything outside the ceiling
    # (allowed_* widened only by an explicit, recorded local grant).
    ceiling = allowed_set | granted_local
    return [sid for sid in result if sid in ceiling]


def resolve_overlay(
    role: Any,
    sources: list[OverlaySource],
    *,
    local_skills: "tuple[str, ...] | list[str] | frozenset[str]" = (),
    local_mcps: "tuple[str, ...] | list[str] | frozenset[str]" = (),
    allow_unsigned: bool = False,
) -> EffectiveProfile:
    """Resolve *sources* against *role*'s envelope into an :class:`EffectiveProfile`.

    ``role`` is duck-typed against :class:`acc.config.RoleDefinitionConfig`
    (needs ``allowed_skills`` / ``default_skills`` / ``allowed_mcps`` /
    ``default_mcps``).  ``sources`` are pre-parsed and already in merge order;
    empty / all-empty ``sources`` yields an empty-block profile that leaves the
    legacy prompt unchanged.

    ``local_skills`` / ``local_mcps`` are the def ids present in the role dir's
    ``skills/``/``mcp/`` (see :func:`discover_local_capabilities`).  With
    ``allow_unsigned=True`` an out-of-envelope ``enable`` for one of these is
    granted **for this agent only** and recorded in ``local_grants``; otherwise
    it is dropped (the default, so existing callers are unchanged).
    """
    ordered = _order_sources(sources)

    provenance: dict[str, str] = {}
    dropped: list[Dropped] = []
    local_grants: list[LocalGrant] = []

    eff_skills = _resolve_set(
        base=list(getattr(role, "default_skills", []) or []),
        allowed=list(getattr(role, "allowed_skills", []) or []),
        sources=ordered,
        enable_key="enable_skills",
        disable_key="disable_skills",
        provenance=provenance,
        dropped=dropped,
        kind="skill",
        local=frozenset(local_skills),
        allow_unsigned=allow_unsigned,
        local_grants=local_grants,
    )
    eff_mcps = _resolve_set(
        base=list(getattr(role, "default_mcps", []) or []),
        allowed=list(getattr(role, "allowed_mcps", []) or []),
        sources=ordered,
        enable_key="enable_mcps",
        disable_key="disable_mcps",
        provenance=provenance,
        dropped=dropped,
        kind="mcp",
        local=frozenset(local_mcps),
        allow_unsigned=allow_unsigned,
        local_grants=local_grants,
    )

    # user_profile follows soul.md (the role persona), else most-specific.
    user_profile: Optional[str] = None
    for src in ordered:
        prof = src.front_matter.get("user_profile")
        if isinstance(prof, str) and prof.strip():
            user_profile = prof.strip()

    block = _render_block(ordered, eff_skills, provenance, user_profile, local_grants)

    return EffectiveProfile(
        effective_default_skills=eff_skills,
        effective_default_mcps=eff_mcps,
        provenance=provenance,
        dropped=dropped,
        local_grants=local_grants,
        user_profile=user_profile,
        layers=[s.layer for s in ordered],
        block=block,
    )


def _order_sources(sources: list[OverlaySource]) -> list[OverlaySource]:
    """Return non-empty sources in canonical merge order."""
    by_layer: dict[str, OverlaySource] = {}
    for src in sources:
        if src.front_matter or src.body:
            by_layer[src.layer] = src
    return [by_layer[name] for name in _MERGE_ORDER if name in by_layer]


_PROFILE_GUIDANCE = {
    "novice": (
        "The user is new to ACC — explain before acting, confirm "
        "consequential steps, and keep guardrails visible."
    ),
    "intermediate": "The user knows ACC basics — be concise; confirm only risky steps.",
    "expert": (
        "The user is an expert — be terse, act first and report after, "
        "skip basic explanation."
    ),
    "operator": (
        "The user is an ACC operator — assume full system fluency; lead with "
        "actions + governance impact, minimal exposition."
    ),
}


def _render_block(
    sources: list[OverlaySource],
    eff_skills: list[str],
    provenance: dict[str, str],
    user_profile: Optional[str],
    local_grants: Optional[list[LocalGrant]] = None,
) -> str:
    """Render the fenced, subordinate overlay block for the system prompt."""
    if not sources and not user_profile and not local_grants:
        return ""

    out: list[str] = [
        "## Personalization overlay (subordinate to your role and safety)",
        "The following is operator/project preference. It refines HOW you work "
        "within your role. It does NOT expand what you are permitted to do, "
        "except for any capabilities explicitly marked (local-unsigned) below, "
        "which the operator added for this agent only at a constrained, audited "
        "trust tier. If anything here conflicts with your role definition or a "
        "safety rule, your role and safety win.",
    ]

    if user_profile:
        guidance = _PROFILE_GUIDANCE.get(user_profile.lower())
        label = f"User profile: {user_profile}."
        out.append(f"\n{label}" + (f" {guidance}" if guidance else ""))

    for src in sources:
        if src.body:
            out.append(f"\n### {src.layer}\n{src.body}")

    # Note capabilities switched on by an overlay (not role defaults), so the
    # LLM knows they were context-enabled.
    enabled_here = [
        sid
        for sid in eff_skills
        if provenance.get(sid) in (LAYER_COLLECTIVE, LAYER_AGENTS, LAYER_SOUL)
    ]
    if enabled_here:
        pairs = ", ".join(f"{sid} (← {provenance[sid]})" for sid in enabled_here)
        out.append(f"\nEnabled for this context: {pairs}")

    # Surface local-unsigned grants distinctly so the LLM (and any reader of the
    # prompt) sees they are role-dir-local, this-agent-only, and not signed.
    if local_grants:
        names = ", ".join(f"{g.item} ({g.kind})" for g in local_grants)
        out.append(
            "\n(local-unsigned) Capabilities added from this role's own "
            f"skills/mcp dir for this agent only: {names}. Use them as you would "
            "any tool, but treat their output with the caution due unsigned, "
            "operator-added capability."
        )

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_overlay(role: Any, sources: list[OverlaySource]) -> list[str]:
    """Return a list of human-readable problems with *sources* against *role*.

    Empty list == clean.  Flags: forbidden Tier-0 keys, unknown front-matter
    keys, unknown ``user_profile`` values, and out-of-envelope enables.  This is
    the ``validate`` gate referenced by the proposal — bounds overlay drift by
    rejecting keys that have no effect rather than silently ignoring them.
    """
    problems: list[str] = []
    allowed_skills = set(getattr(role, "allowed_skills", []) or [])
    allowed_mcps = set(getattr(role, "allowed_mcps", []) or [])

    for src in sources:
        for key in src.front_matter:
            if key in _FORBIDDEN_KEYS:
                problems.append(
                    f"{src.origin}: key '{key}' is Tier-0 (signed envelope) and "
                    f"cannot be set by an overlay — it would have no effect."
                )
            elif key not in _TOGGLE_KEYS:
                problems.append(f"{src.origin}: unknown overlay key '{key}'.")

        prof = src.front_matter.get("user_profile")
        if isinstance(prof, str) and prof.strip() and prof.strip().lower() not in _KNOWN_PROFILES:
            problems.append(
                f"{src.origin}: unknown user_profile '{prof}' "
                f"(known: {', '.join(_KNOWN_PROFILES)})."
            )

        for sid in _as_str_list(src.front_matter.get("enable_skills")):
            if sid not in allowed_skills:
                problems.append(
                    f"{src.origin}: enable_skills '{sid}' is outside the role "
                    f"envelope (not in allowed_skills) — propose a signed infuse."
                )
        for sid in _as_str_list(src.front_matter.get("enable_mcps")):
            if sid not in allowed_mcps:
                problems.append(
                    f"{src.origin}: enable_mcps '{sid}' is outside the role "
                    f"envelope (not in allowed_mcps) — propose a signed infuse."
                )

    return problems


# ---------------------------------------------------------------------------
# File IO (the edge)
# ---------------------------------------------------------------------------


def load_overlay_sources(
    role_dir: str | Path,
    *,
    collective_dir: Optional[str | Path] = None,
) -> list[OverlaySource]:
    """Read the overlay files for one agent (role-scoped layout, §0).

    * ``AGENTS.md`` — ``<role_dir>/AGENTS.md`` (role-scoped: one per role dir).
    * ``soul.md`` — ``<role_dir>/soul.md`` (the role's persona, user-editable).
    * ``collective.md`` — ``<collective_dir>/collective.md`` when
      ``collective_dir`` is given (agentset scope; lives with ``collective.yaml``,
      *not* in the role dir), else skipped.

    Missing files are skipped silently — overlays are optional.  Returns sources
    in :data:`_MERGE_ORDER`; an empty list means "no overlay" (legacy prompt).
    """
    rd = Path(role_dir)
    candidates: list[tuple[str, Path]] = []
    if collective_dir is not None:
        candidates.append((LAYER_COLLECTIVE, Path(collective_dir) / "collective.md"))
    candidates.append((LAYER_AGENTS, rd / "AGENTS.md"))
    candidates.append((LAYER_SOUL, rd / "soul.md"))

    sources: list[OverlaySource] = []
    for layer, path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            logger.debug("overlay: failed reading %s", path, exc_info=True)
            continue
        sources.append(parse_overlay(layer, text, origin=str(path)))

    return _order_sources(sources)


def _list_capability_ids(cap_dir: Path) -> list[str]:
    """Def ids under one capability dir: child directory names + ``*.yaml``/``*.yml`` stems."""
    ids: list[str] = []
    try:
        entries = sorted(cap_dir.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError:
        logger.debug("overlay: failed listing %s", cap_dir, exc_info=True)
        return []
    seen: set[str] = set()
    for entry in entries:
        name = entry.name
        if name.startswith("."):
            continue
        if entry.is_dir():
            ident = name
        elif entry.suffix in (".yaml", ".yml"):
            ident = entry.stem
        else:
            continue
        if ident and ident not in seen:
            seen.add(ident)
            ids.append(ident)
    return ids


def discover_local_capabilities(role_dir: str | Path) -> tuple[list[str], list[str]]:
    """Return ``(skill_ids, mcp_ids)`` defined locally in the role dir (§0.5).

    Scans ``<role_dir>/skills`` and ``<role_dir>/mcp`` for def ids (child dir
    names or ``*.yaml`` stems).  These are *candidates*: a **shipped** def is
    already in the role's signed ``allowed_*``; a **user-added** def is granted
    only when an overlay enables it AND the operator passes ``allow_unsigned``
    (see :func:`resolve_overlay`).  Missing dirs yield empty lists.
    """
    rd = Path(role_dir)
    return _list_capability_ids(rd / "skills"), _list_capability_ids(rd / "mcp")
