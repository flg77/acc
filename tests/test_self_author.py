"""Proposal 036 PR-2 — ``acc/self_author/`` gap -> draft pack.

Covers:
  (a) a synthetic APPROVED new_role gap (deterministic stub llm_fill) ->
      validated role.yaml + role.md + a built .accpkg in proposed/;
  (b) an invalid draft (empty candidate skills/mcps + empty fill) -> rejected
      with a structured finding, nothing surfaced;
  (c) the secrets-scan redaction path (a secret in the evidence is scrubbed
      before drafting + audited, and never lands in role.md);
  (d) the 033 WS-A capability_validator hook degrades cleanly when the import
      fails (baseline shape gate still passes the draft).

No live LLM, no NATS, no Redis — pure filesystem + the injectable seam.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest
import yaml

from acc.assistant.gap_analysis import GapEvidence, RoleGapFinding
from acc.config import RoleDefinitionConfig
from acc.pkg.manifest import AccPkgManifest
from acc.self_author import author_role_from_gap
from acc.self_author.author import DraftContext, SelfAuthorError


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _new_role_gap(
    *,
    suggested_name: str = "tax_specialist",
    requires_skills=("python_exec",),
    requires_mcps=("web_fetch",),
    rationale: str = "no installed or available role covers US tax filing work",
    evidence_note: str = "reviewer: coding_agent failed the 1040 schedule task",
    goal_summary: str = "draft a US tax filing helper",
) -> RoleGapFinding:
    """A synthetic operator-APPROVED new_role gap, shaped like 019 G4 emits."""
    return RoleGapFinding(
        goal_id="goal-123",
        goal_summary=goal_summary,
        best_match_role="coding_agent",
        best_match_confidence=0.31,
        gap_kind="new_role",
        proposal={
            "new_role": {
                "suggested_name": suggested_name,
                "requires_skills": list(requires_skills),
                "requires_mcps": list(requires_mcps),
                "rationale": rationale,
            }
        },
        evidence=(GapEvidence(source="reviewer", note=evidence_note),),
        fallback_taken="none",
    )


def _stub_fill(_skeleton, ctx: DraftContext) -> dict:
    """Deterministic llm_fill stub — bounded to the skeleton/context."""
    return {
        "purpose": f"Cover {ctx.suggested_name} work the roster lacks.",
        "persona": "analytical",
        "seed_context": (
            f"You are the ACC {ctx.suggested_name} agent. Accept "
            f"{', '.join(ctx.task_types)}. Output JSON with confidence."
        ),
        "role_md": (
            f"# Role: {ctx.suggested_name}\n\n## Purpose\nDrafted from a gap.\n"
        ),
    }


def _accpkg_names(accpkg_path: Path) -> list[str]:
    """Return the member names inside a built .accpkg (gzip tar)."""
    with tarfile.open(accpkg_path, mode="r:gz") as tar:
        return tar.getnames()


# ---------------------------------------------------------------------------
# (a) happy path
# ---------------------------------------------------------------------------


def test_approved_new_role_gap_drafts_validated_pack(tmp_path):
    gap = _new_role_gap()
    res = author_role_from_gap(gap, llm_fill=_stub_fill, packages_root=tmp_path)

    assert res.ok is True
    assert res.scope == "self"
    assert res.role_name == "tax_specialist"
    assert res.finding is None

    # Staging dir holds role.yaml + role.md + accpkg.yaml in the documented layout.
    staging = res.staging_dir
    assert staging is not None
    role_dir = staging / "roles" / "tax_specialist"
    assert (role_dir / "role.yaml").is_file()
    assert (role_dir / "role.md").is_file()
    assert (staging / "accpkg.yaml").is_file()
    assert staging == tmp_path / "self" / "tax_specialist-0.1.0"

    # role.yaml round-trips through RoleDefinitionConfig and carries the
    # gap's candidate skills/mcps + a non-empty LLM-filled purpose.
    raw = yaml.safe_load((role_dir / "role.yaml").read_text(encoding="utf-8"))
    role_def = RoleDefinitionConfig.model_validate(raw["role_definition"])
    assert role_def.purpose and len(role_def.purpose) <= 200
    assert "python_exec" in role_def.allowed_skills
    assert "web_fetch" in role_def.allowed_mcps
    assert role_def.task_types  # evidence-derived, non-empty

    # A real .accpkg was built and contains the role files + a valid manifest.
    assert res.accpkg_path is not None and res.accpkg_path.is_file()
    names = _accpkg_names(res.accpkg_path)
    assert "accpkg.yaml" in names
    assert "roles/tax_specialist/role.yaml" in names
    assert "roles/tax_specialist/role.md" in names

    assert isinstance(res.manifest, AccPkgManifest)
    assert res.manifest.name == "@self/tax_specialist"
    assert res.manifest.version == "0.1.0"
    assert res.manifest.content_sha256  # stamped by build()


def test_default_fill_used_when_none_passed(tmp_path):
    """Omitting llm_fill uses the deterministic default seam (no live model)."""
    gap = _new_role_gap(suggested_name="data_steward")
    res = author_role_from_gap(gap, packages_root=tmp_path)
    assert res.ok is True
    role_dir = res.staging_dir / "roles" / "data_steward"
    md = (role_dir / "role.md").read_text(encoding="utf-8")
    assert "data steward" in md.lower()


# ---------------------------------------------------------------------------
# (b) invalid draft -> rejected, nothing surfaced
# ---------------------------------------------------------------------------


def test_invalid_draft_rejected_with_finding(tmp_path):
    """A gap with no candidate skills/mcps + an empty fill is rejected."""
    gap = _new_role_gap(requires_skills=(), requires_mcps=())

    def _empty_fill(_skeleton, _ctx):
        return {}  # no purpose, no role_md -> draft is unusable

    res = author_role_from_gap(gap, llm_fill=_empty_fill, packages_root=tmp_path)

    assert res.ok is False
    assert res.stage == "validate"
    assert res.finding is not None
    assert res.finding["kind"] == "self_author_rejected"
    assert res.finding["source"] == "self_author"
    assert res.finding["errors"]  # at least one structured error
    # Nothing surfaced: no pack path, no staging dir, and proposed/ stays empty.
    assert res.accpkg_path is None
    assert res.staging_dir is None
    assert not any(tmp_path.iterdir())


def test_missing_capability_rejected(tmp_path):
    """Even with good prose, zero skills AND zero mcps is rejected."""
    gap = _new_role_gap(requires_skills=(), requires_mcps=())
    res = author_role_from_gap(gap, llm_fill=_stub_fill, packages_root=tmp_path)
    assert res.ok is False
    assert any("no skills and no MCPs" in e for e in res.finding["errors"])
    assert not any(tmp_path.iterdir())


def test_non_new_role_gap_raises(tmp_path):
    """extend_role / infuse_known are out of scope -> SelfAuthorError."""
    gap = RoleGapFinding(
        goal_id="g", goal_summary="x", best_match_role="coding_agent",
        best_match_confidence=0.3, gap_kind="extend_role",
        proposal={"extend_role": {"role": "coding_agent", "add_skills": []}},
    )
    with pytest.raises(SelfAuthorError):
        author_role_from_gap(gap, llm_fill=_stub_fill, packages_root=tmp_path)


# ---------------------------------------------------------------------------
# (c) secrets-scan redaction path
# ---------------------------------------------------------------------------


def test_secret_in_evidence_redacted_and_audited(tmp_path):
    """A secret in the gap evidence is scrubbed before drafting + audited."""
    secret = "AKIAIOSFODNN7EXAMPLE"
    gap = _new_role_gap(
        evidence_note=f"reviewer note leaked a credential api_key={secret}",
    )
    res = author_role_from_gap(gap, llm_fill=_stub_fill, packages_root=tmp_path)

    assert res.ok is True
    # The redaction is recorded in the audit list on the result.
    assert res.redactions  # non-empty
    assert any(lbl in ("AWS_ACCESS_KEY", "CREDENTIAL") for lbl in res.redactions)

    # The secret never reaches the drafted role.md / role.yaml.
    role_dir = res.staging_dir / "roles" / res.role_name
    md = (role_dir / "role.md").read_text(encoding="utf-8")
    ry = (role_dir / "role.yaml").read_text(encoding="utf-8")
    assert secret not in md
    assert secret not in ry


def test_evidence_passed_to_fill_is_redacted(tmp_path):
    """The llm_fill seam only ever sees redacted evidence."""
    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    seen: dict = {}

    def _capturing_fill(_skeleton, ctx: DraftContext):
        seen["evidence"] = ctx.evidence
        return _stub_fill(_skeleton, ctx)

    gap = _new_role_gap(evidence_note=f"token: {secret} pasted by mistake")
    res = author_role_from_gap(
        gap, llm_fill=_capturing_fill, packages_root=tmp_path
    )
    assert res.ok is True
    blob = "".join(e.get("note", "") for e in seen["evidence"])
    assert secret not in blob
    assert "<" in blob  # a placeholder was substituted


# ---------------------------------------------------------------------------
# (d) WS-A hook degrades cleanly when the validator import fails
# ---------------------------------------------------------------------------


def test_ws_a_hook_degrades_when_validator_absent(tmp_path, monkeypatch):
    """When capability_validator can't be imported, the draft still validates+builds.

    The module now exists in-tree, so we simulate its absence by poisoning the
    import (``sys.modules[...] = None`` makes ``import`` raise). The baseline
    Pydantic gate alone must still accept + build the draft.
    """
    import sys

    monkeypatch.setitem(sys.modules, "acc.capability_validator", None)

    gap = _new_role_gap()
    res = author_role_from_gap(gap, llm_fill=_stub_fill, packages_root=tmp_path)
    # Baseline Pydantic gate alone accepted + built the draft.
    assert res.ok is True
    assert res.accpkg_path is not None and res.accpkg_path.is_file()


def test_ws_a_hook_engages_when_validator_present(tmp_path, monkeypatch):
    """When a WS-A validator IS importable, its errors reject the draft.

    Simulates the post-promotion world by injecting a fake
    ``acc.capability_validator`` module whose ``validate_role_capabilities``
    flags the draft — proving the hook wires in with no code change here.
    """
    import sys
    import types

    fake = types.ModuleType("acc.capability_validator")
    fake.ERROR = "ERROR"  # type: ignore[attr-defined]
    fake.WARNING = "WARNING"  # type: ignore[attr-defined]

    class _Finding:
        severity = "ERROR"

        def __str__(self) -> str:
            return "[ERROR] role:tax_specialist: WS-A: skill 'python_exec' exceeds the draft risk envelope"

    def _validate_role_capabilities(
        role_id, role, *, available_skills, available_mcps, unresolved_severity="ERROR"
    ):
        return [_Finding()]

    # Provide the private helpers the hook imports alongside the public fn.
    fake.validate_role_capabilities = _validate_role_capabilities  # type: ignore[attr-defined]
    fake._available_skills = lambda root: (set(), [])  # type: ignore[attr-defined]
    fake._available_mcps = lambda root: (set(), [])  # type: ignore[attr-defined]
    fake._skills_root_default = lambda: "skills"  # type: ignore[attr-defined]
    fake._mcps_root_default = lambda: "mcps"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "acc.capability_validator", fake)

    gap = _new_role_gap()
    res = author_role_from_gap(gap, llm_fill=_stub_fill, packages_root=tmp_path)

    assert res.ok is False
    assert res.stage == "validate"
    assert any("WS-A" in e for e in res.finding["errors"])
    assert not any(tmp_path.iterdir())  # nothing surfaced
