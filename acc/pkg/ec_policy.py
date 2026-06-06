"""Enterprise Contract policy — Stage 1.2 declarative checker.

Per brainstorm Q4 + the Stage 1 proposal's open decision Q1, the
canonical EC policy language is **Rego** (matching the Red Hat
Trusted Application Pipeline + the Enterprise Contract project's
own choice).  Stage 1.2 ships a **declarative YAML** form of the
same checks so the runtime doesn't have to depend on an OPA binary
at install time; the YAML policy is fully sufficient for the
attestation-presence set the brainstorm calls out (build provenance,
eval pass, Cat-A/B/C smoke).

A Stage 2 follow-up adds a Rego backend that shells out to ``opa
eval``; this module's :class:`EnterpriseContractPolicy` becomes a
narrow front-end that delegates to whichever backend the operator
points ``--ec-policy`` at (``.yaml`` ⇒ this checker, ``.rego`` ⇒
the OPA shell-out).

Policy schema (``policy/enterprise-contract.yaml``)
---------------------------------------------------

::

    schema_version: 1
    required_attestations:
      - kind: build_provenance
        # Optional: pin to a specific predicateType / format string
        predicate_type: "https://slsa.dev/provenance/v0.2"
      - kind: eval_pass
        # Stage 1.1's evals JSONL — checker requires presence + at
        # least one ``pass`` verdict per declared model
      - kind: cat_abc_smoke
    allow_empty_attestation_bundle: false   # Default; flip in dev
    minimum_signers: 1                      # Future: dual-signer

Attestation bundle (what the catalog serves alongside the .accpkg)
------------------------------------------------------------------

::

    [
      {"kind": "build_provenance", "sha256": "<64-hex>",
       "predicate_type": "https://slsa.dev/provenance/v0.2",
       "data": {...arbitrary attestation body...}},
      {"kind": "eval_pass", "sha256": "<64-hex>",
       "data": {"jsonl_sha256": "...", "verdicts": {"claude-sonnet": "pass", ...}}},
      ...
    ]

Stage 1.3 (publish) populates this bundle from Tekton Chains + the
Stage 1.1 evals runner.  Stage 1.2 just verifies presence + per-kind
requirements.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger("acc.pkg.ec_policy")

# Default policy ships installed at ``/etc/acc/policy/enterprise-contract.yaml``
# (operator may override via ``--ec-policy`` CLI flag).  The constant
# lives here so the CLI + verify share the same fallback.
DEFAULT_POLICY_PATH = Path("/etc/acc/policy/enterprise-contract.yaml")


# ---------------------------------------------------------------------------
# Policy schema
# ---------------------------------------------------------------------------


class RequiredAttestation(BaseModel):
    """One required-attestation row in the policy."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(..., min_length=1)
    predicate_type: str = Field(
        "",
        description="If set, the attestation's predicate_type must match.",
    )
    require_verdict_pass: bool = Field(
        False,
        description=(
            "For ``eval_pass`` rows: every model in the verdicts dict "
            "must report ``pass`` (any other value fails)."
        ),
    )


class EnterpriseContractPolicy(BaseModel):
    """Top-level EC policy model."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(1, ge=1)
    required_attestations: list[RequiredAttestation] = Field(default_factory=list)
    allow_empty_attestation_bundle: bool = False
    minimum_signers: int = Field(1, ge=1, le=8)

    @field_validator("required_attestations")
    @classmethod
    def _no_duplicate_kinds(cls, v: list[RequiredAttestation]) -> list[RequiredAttestation]:
        seen: set[str] = set()
        for entry in v:
            if entry.kind in seen:
                raise ValueError(f"required_attestations contains duplicate kind {entry.kind!r}")
            seen.add(entry.kind)
        return v


# ---------------------------------------------------------------------------
# Attestation envelope (what publishers + verify pass around)
# ---------------------------------------------------------------------------


class Attestation(BaseModel):
    """One in-toto-shaped attestation row."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(..., min_length=1)
    sha256: str = Field(..., min_length=64, max_length=64)
    predicate_type: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_policy(path: Path | None = None) -> EnterpriseContractPolicy:
    """Load a YAML policy file (defaults to :data:`DEFAULT_POLICY_PATH`).

    A missing policy file is treated as the **empty policy** — no
    required attestations, signing floor only.  This makes the
    transition gentle: Stage 1.2 ships without forcing every existing
    package to grow an attestation bundle overnight; operators that
    want stricter enforcement drop the bundled
    ``policy/enterprise-contract.yaml`` into place.
    """
    target = path or DEFAULT_POLICY_PATH
    if not target.is_file():
        logger.debug(
            "ec_policy: no policy at %s — using empty policy", target,
        )
        return EnterpriseContractPolicy()
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"malformed YAML in {target}: {exc}") from exc
    return EnterpriseContractPolicy.model_validate(raw)


def load_attestations(path: Path | None) -> list[Attestation]:
    """Load an attestation bundle from a YAML or JSON file.

    The file is a top-level list of attestation rows.  Missing file
    returns an empty list; bundle-empty enforcement is done by the
    checker (so the operator can override via
    ``allow_empty_attestation_bundle: true``).
    """
    if path is None or not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"{path}: attestation bundle top-level must be a list"
        )
    return [Attestation.model_validate(row) for row in raw]


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyCheckResult:
    ok: bool
    violations: tuple[str, ...]
    matched_kinds: tuple[str, ...]   # which required attestations were present


def check_policy(
    policy: EnterpriseContractPolicy,
    attestations: Iterable[Attestation],
) -> PolicyCheckResult:
    """Run every policy rule against the attestation bundle.

    Returns a :class:`PolicyCheckResult` with the full list of
    violations (so the caller can render them all rather than only
    surfacing the first failure).  Pure / deterministic.
    """
    bundle = list(attestations)
    violations: list[str] = []
    matched: list[str] = []

    if not bundle and not policy.allow_empty_attestation_bundle:
        if policy.required_attestations:
            violations.append("attestation bundle is empty")

    by_kind: dict[str, Attestation] = {a.kind: a for a in bundle}

    for required in policy.required_attestations:
        found = by_kind.get(required.kind)
        if found is None:
            violations.append(f"missing required attestation: {required.kind}")
            continue
        if required.predicate_type and found.predicate_type != required.predicate_type:
            violations.append(
                f"{required.kind}: predicate_type mismatch "
                f"(expected {required.predicate_type!r}, got {found.predicate_type!r})"
            )
            continue
        if required.require_verdict_pass:
            verdicts = found.data.get("verdicts", {})
            if not isinstance(verdicts, dict) or not verdicts:
                violations.append(
                    f"{required.kind}: no verdicts recorded in attestation data"
                )
                continue
            bad = [m for m, v in verdicts.items() if v != "pass"]
            if bad:
                violations.append(
                    f"{required.kind}: non-pass verdicts on models: {sorted(bad)}"
                )
                continue
        matched.append(required.kind)

    return PolicyCheckResult(
        ok=not violations,
        violations=tuple(violations),
        matched_kinds=tuple(matched),
    )


__all__ = [
    "DEFAULT_POLICY_PATH",
    "Attestation",
    "EnterpriseContractPolicy",
    "PolicyCheckResult",
    "RequiredAttestation",
    "check_policy",
    "load_attestations",
    "load_policy",
]
