"""`.accpkg` manifest schema v1 — Stage 0.

The manifest is the contract that every `.accpkg` carries. It declares:

* Package identity: scoped name (``@scope/name``) + exact semver.
* Dependencies: other packages with semver-range constraints
  (resolved + cycle-checked at install time, not here).
* Contents: roles, skills, MCPs the package ships.
* Tier classification per skill / MCP — refuses ``core_baseline``
  entries (those stay in ACC core; see ``tools/skill_mcp_tiers.yaml``).
* Signed dependency closure: populated by the Stage-1 trust chain;
  Stage 0 leaves this empty and emits a warning.

The schema is intentionally tight (``extra="forbid"``) so misspelt
fields are caught at parse time rather than silently ignored.

JSON Schema export (for non-Python consumers) lives at
``acc/pkg/schema/accpkg-v1.json``. Re-emit with
``python -m acc.pkg.manifest --emit-schema``.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger("acc.pkg.manifest")

# ---------------------------------------------------------------------------
# Identity + version regexes
# ---------------------------------------------------------------------------

# Scoped name: @scope/name. Lowercase, hyphen-friendly; mirrors npm + MCP
# Registry conventions.
SCOPED_NAME_RE = re.compile(r"^@[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9_-]*$")

# Exact semver (X.Y.Z optionally with prerelease).  Build metadata is
# rejected on purpose — packages are content-addressed by sha256, build
# metadata adds ambiguity without value.
EXACT_SEMVER_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$",
)

# Constraint forms accepted in ``Dependency.version``.  Range syntax
# subset; resolution semantics live in ``acc/pkg/install.py``.
#   * exact:    1.2.3
#   * caret:    ^1.2.3, ^1.2, ^1
#   * tilde:    ~1.2.3, ~1.2
#   * gte:      >=1.2.3, >=1.2
#   * lt:       <1.2.3, <1.2
#   * range:    >=1.2.3 <2.0.0    (space-separated, exactly two parts)
_RANGE_PART_RE = re.compile(
    r"^(?:\^|~|>=|<=|<|>)?\d+(?:\.\d+){0,2}(?:-[0-9A-Za-z.-]+)?$"
)


def _is_valid_constraint(constraint: str) -> bool:
    """Cheap structural check on a semver constraint string.

    Full SAT-style resolution happens in the installer; here we only
    catch obvious syntax errors so a malformed manifest fails at parse
    time.
    """
    if not constraint:
        return False
    parts = constraint.split()
    if len(parts) > 2:
        return False
    return all(_RANGE_PART_RE.match(p) for p in parts)


# ---------------------------------------------------------------------------
# Tier policy — which skills/MCPs may NOT travel inside a package
# ---------------------------------------------------------------------------
#
# Per brainstorm Q3a, every skill/MCP is classified into one of three
# tiers:
#
#   * ``core_baseline`` — ships with ACC core; never packaged.
#   * ``bundle_in_role`` — travels inside the role package.
#   * ``own_pack``      — its own ``@scope/skills-foo`` package.
#
# ``tools/skill_mcp_tiers.yaml`` is the long-term source of truth and
# can update without code changes.  For the manifest validator to work
# standalone (without loading that file), we hard-code the v0.3.50
# baseline set here.  When the YAML moves a skill from baseline to
# bundle_in_role, that change MUST also remove it from this set in the
# same release.

CORE_BASELINE_SKILLS: frozenset[str] = frozenset(
    {
        "fs_read",
        "grep_text",
        "pwd",
        "which_cmd",
        "ls_dir",
        "find_files",
        "env_get",
        "git_status",
        "git_log_recent",
        "disk_free",
        "shell_exec",
        "ssh_exec",
        # Proposal 024 P3 — governed RAG document store.  Granted by the
        # built-in ``document_store`` role flag (any pack's role may set
        # it), so the skills ship in the image like fs_read, never in a
        # pack.  acc/docstore.py + skills/doc_{ingest,retrieve}/.
        "doc_ingest",
        "doc_retrieve",
        # Assistant (CONTROL role) operational skills — ship in the image
        # (skills/<name>/), granted by the assistant's role caps, never in a
        # pack: catalog query, python exec, role/skill authoring, release pipe.
        "catalog_query",
        "python_exec",
        "role_author",
        "skill_author",
        "release_pipe",
        # Integration pillars A + C (proposals A/C). The skill manifests +
        # adapters ship in the image; ENABLEMENT is per-feature (messengers /
        # [speech] extras + role caps), but packaging-wise they're core — the
        # control-roles pack must not re-ship them (043 feature-assembly).
        "telegram_send",
        "slack_post",
        "mattermost_post",
        "speech_transcribe",
        "speech_synthesize",
    }
)

CORE_BASELINE_MCPS: frozenset[str] = frozenset(
    {
        "arxiv",
        "wikipedia",
        "semantic_scholar",
        # Integration pillars A + B. The MCP manifests ship in the image
        # (mcps/<name>/); the servers themselves are deploy-time sidecars. Core
        # for packaging — not bundled in the control-roles pack (043).
        "signal",
        "google_workspace",
    }
)


# ---------------------------------------------------------------------------
# Component models
# ---------------------------------------------------------------------------


class Dependency(BaseModel):
    """A semver-pinned reference to another `.accpkg`.

    Cycle detection + version-range resolution happens in
    ``acc/pkg/install.py`` against the catalog index; this model only
    validates the *shape* of the constraint string.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Scoped package name, e.g. @acc/foo")
    version: str = Field(
        ...,
        description=(
            "Semver constraint: exact (1.2.3), caret (^1.2), tilde "
            "(~1.2.3), bound (>=1.2 <2.0), etc."
        ),
    )

    @model_validator(mode="after")
    def _check_shapes(self) -> "Dependency":
        if not SCOPED_NAME_RE.match(self.name):
            raise ValueError(
                f"dependency name must match {SCOPED_NAME_RE.pattern!r}: "
                f"{self.name!r}"
            )
        if not _is_valid_constraint(self.version):
            raise ValueError(
                f"invalid semver constraint: {self.version!r}"
            )
        return self


class RoleRef(BaseModel):
    """Pointer to a ``role.yaml`` inside the package tree."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    path: str = Field(
        ...,
        description="Path inside the package, e.g. roles/coding_agent/role.yaml",
    )


# Tier label permitted on package-contained skills/MCPs.  The third
# tier (``core_baseline``) is intentionally excluded — it would mean
# "ships with core" and so cannot appear inside a package.
PackagedTier = Literal["bundle_in_role", "own_pack"]


class SkillRef(BaseModel):
    """A skill bundled inside the package tree."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    tier: PackagedTier
    path: str = Field(..., description="Path inside the package, e.g. skills/<name>/")


class McpRef(BaseModel):
    """An MCP bundled inside the package tree."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    tier: PackagedTier
    path: str = Field(..., description="Path inside the package, e.g. mcps/<name>/")


class SignedDepEntry(BaseModel):
    """One row of the signed dependency closure (Stage 1).

    Stage 0 leaves the parent list empty.  Stage 1's publish pipeline
    populates it with ``{name, version, sha256}`` for every transitive
    dep, and ``acc-pkg verify`` then rejects installs whose resolved
    graph diverges from this closure.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    sha256: str = Field(..., min_length=64, max_length=64)


# ---------------------------------------------------------------------------
# Top-level manifest
# ---------------------------------------------------------------------------


class AccPkgManifest(BaseModel):
    """The top-level ``accpkg.yaml`` model — v1.

    A package MAY ship zero of any one component type (a memory-seed-
    only pack has no roles + no skills + no mcps), but the manifest
    must declare ``name`` + ``version`` + ``schema_version`` always.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    name: str = Field(..., description="Scoped package name, @scope/name")
    version: str = Field(..., description="Exact semver of this build")
    description: str = ""
    depends_on: list[Dependency] = Field(default_factory=list)
    roles: list[RoleRef] = Field(default_factory=list)
    skills: list[SkillRef] = Field(default_factory=list)
    mcps: list[McpRef] = Field(default_factory=list)
    signed_dep_closure: list[SignedDepEntry] = Field(default_factory=list)
    content_sha256: str = Field(
        "",
        description=(
            "sha256 over the deterministic tarball content; populated by "
            "`acc-pkg build`, empty in a source manifest."
        ),
    )

    @model_validator(mode="after")
    def _check_top_level(self) -> "AccPkgManifest":
        # name + version shape
        if not SCOPED_NAME_RE.match(self.name):
            raise ValueError(
                f"package name must match {SCOPED_NAME_RE.pattern!r}: "
                f"{self.name!r}"
            )
        if not EXACT_SEMVER_RE.match(self.version):
            raise ValueError(
                f"package version must be exact semver (X.Y.Z[-pre]): "
                f"{self.version!r}"
            )

        # Refuse core-baseline leakage — a package may NOT ship a skill
        # or MCP that already lives in ACC core.
        for s in self.skills:
            if s.name in CORE_BASELINE_SKILLS:
                raise ValueError(
                    f"skill {s.name!r} is core_baseline; it ships with "
                    "ACC core and must not appear in a package"
                )
        for m in self.mcps:
            if m.name in CORE_BASELINE_MCPS:
                raise ValueError(
                    f"mcp {m.name!r} is core_baseline; it ships with "
                    "ACC core and must not appear in a package"
                )

        # Duplicates within a component list are a manifest authoring
        # error — surface them up front rather than letting the
        # installer crash with a confused path collision.
        _check_unique([r.name for r in self.roles], "role")
        _check_unique([s.name for s in self.skills], "skill")
        _check_unique([m.name for m in self.mcps], "mcp")
        _check_unique([d.name for d in self.depends_on], "dependency")

        # content_sha256, when present, must look like a sha256 hex.
        if self.content_sha256 and not re.fullmatch(
            r"[0-9a-f]{64}", self.content_sha256
        ):
            raise ValueError(
                "content_sha256 must be 64 hex chars (or empty in source)"
            )

        return self


def _check_unique(names: list[str], kind: str) -> None:
    seen: set[str] = set()
    for n in names:
        if n in seen:
            raise ValueError(f"duplicate {kind} name: {n!r}")
        seen.add(n)


# ---------------------------------------------------------------------------
# JSON Schema export
# ---------------------------------------------------------------------------

_SCHEMA_PATH = Path(__file__).parent / "schema" / "accpkg-v1.json"


def emit_json_schema() -> dict:
    """Return the Pydantic-generated JSON Schema for ``AccPkgManifest``."""
    return AccPkgManifest.model_json_schema()


def _main(argv: list[str]) -> int:
    if argv == ["--emit-schema"]:
        json.dump(emit_json_schema(), sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    sys.stderr.write(
        "usage: python -m acc.pkg.manifest --emit-schema\n"
    )
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main(sys.argv[1:]))
