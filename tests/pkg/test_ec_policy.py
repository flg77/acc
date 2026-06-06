"""Tests for the EC policy checker (Stage 1.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from acc.pkg.ec_policy import (
    Attestation,
    EnterpriseContractPolicy,
    RequiredAttestation,
    check_policy,
    load_attestations,
    load_policy,
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def test_minimal_policy_loads():
    p = EnterpriseContractPolicy()
    assert p.schema_version == 1
    assert p.required_attestations == []
    assert p.allow_empty_attestation_bundle is False
    assert p.minimum_signers == 1


def test_policy_extra_field_refused():
    with pytest.raises(ValidationError):
        EnterpriseContractPolicy(rogue=True)


def test_required_attestation_strict():
    with pytest.raises(ValidationError):
        RequiredAttestation(kind="x", rogue=True)


def test_duplicate_required_kinds_refused():
    with pytest.raises(ValidationError, match="duplicate kind"):
        EnterpriseContractPolicy(
            required_attestations=[
                {"kind": "build_provenance"},
                {"kind": "build_provenance"},
            ],
        )


def test_attestation_sha256_length_enforced():
    with pytest.raises(ValidationError):
        Attestation(kind="x", sha256="short")


def test_minimum_signers_range():
    with pytest.raises(ValidationError):
        EnterpriseContractPolicy(minimum_signers=0)
    with pytest.raises(ValidationError):
        EnterpriseContractPolicy(minimum_signers=99)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_load_policy_missing_file_returns_empty(tmp_path):
    policy = load_policy(tmp_path / "no-such.yaml")
    assert policy.required_attestations == []


def test_load_policy_uses_default_when_none(monkeypatch, tmp_path):
    """No explicit path + no file at default → empty policy."""
    monkeypatch.setattr(
        "acc.pkg.ec_policy.DEFAULT_POLICY_PATH", tmp_path / "no.yaml",
    )
    p = load_policy()
    assert p.required_attestations == []


def test_load_policy_from_yaml(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "allow_empty_attestation_bundle": True,
        "required_attestations": [
            {"kind": "build_provenance"},
            {"kind": "eval_pass", "require_verdict_pass": True},
        ],
    }), encoding="utf-8")
    p = load_policy(path)
    assert p.allow_empty_attestation_bundle is True
    assert len(p.required_attestations) == 2


def test_load_policy_malformed_yaml(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text(": : not valid", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        load_policy(path)


def test_load_attestations_missing_returns_empty(tmp_path):
    assert load_attestations(None) == []
    assert load_attestations(tmp_path / "no.yaml") == []


def test_load_attestations_from_yaml(tmp_path):
    path = tmp_path / "att.yaml"
    path.write_text(yaml.safe_dump([
        {"kind": "build_provenance", "sha256": "a" * 64,
         "predicate_type": "x", "data": {"k": "v"}},
    ]), encoding="utf-8")
    rows = load_attestations(path)
    assert len(rows) == 1
    assert rows[0].kind == "build_provenance"


def test_load_attestations_non_list_refused(tmp_path):
    path = tmp_path / "att.yaml"
    path.write_text(yaml.safe_dump({"not": "a list"}), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a list"):
        load_attestations(path)


# ---------------------------------------------------------------------------
# Checker — empty bundle handling
# ---------------------------------------------------------------------------


def test_empty_policy_passes_empty_bundle():
    """No required attestations + empty bundle → pass."""
    result = check_policy(EnterpriseContractPolicy(), [])
    assert result.ok
    assert result.violations == ()


def test_empty_bundle_refused_when_policy_demands_attestations():
    policy = EnterpriseContractPolicy(
        required_attestations=[{"kind": "build_provenance"}],
    )
    result = check_policy(policy, [])
    assert not result.ok
    # Two violations: empty bundle + missing kind
    assert any("empty" in v for v in result.violations)
    assert any("missing required" in v for v in result.violations)


def test_empty_bundle_allowed_when_flag_set():
    policy = EnterpriseContractPolicy(
        required_attestations=[{"kind": "build_provenance"}],
        allow_empty_attestation_bundle=True,
    )
    result = check_policy(policy, [])
    # Empty-bundle check is skipped, but the missing-required check
    # still fails.  Caller decides whether to require zero violations
    # or "no empty-bundle but missing-required is OK"; ec_policy is
    # strict.  This documents the current behaviour.
    assert not result.ok
    assert all("empty" not in v for v in result.violations)


# ---------------------------------------------------------------------------
# Checker — required attestation matching
# ---------------------------------------------------------------------------


def _att(kind: str, **extras) -> Attestation:
    return Attestation(
        kind=kind, sha256="a" * 64,
        predicate_type=extras.get("predicate_type", ""),
        data=extras.get("data", {}),
    )


def test_all_required_present_passes():
    policy = EnterpriseContractPolicy(
        required_attestations=[
            {"kind": "build_provenance"},
            {"kind": "eval_pass"},
            {"kind": "cat_abc_smoke"},
        ],
    )
    bundle = [_att("build_provenance"), _att("eval_pass"), _att("cat_abc_smoke")]
    result = check_policy(policy, bundle)
    assert result.ok
    assert set(result.matched_kinds) == {"build_provenance", "eval_pass", "cat_abc_smoke"}


def test_missing_required_fails():
    policy = EnterpriseContractPolicy(
        required_attestations=[
            {"kind": "build_provenance"},
            {"kind": "eval_pass"},
        ],
    )
    bundle = [_att("build_provenance")]  # eval_pass missing
    result = check_policy(policy, bundle)
    assert not result.ok
    assert any("eval_pass" in v for v in result.violations)


def test_extra_attestation_does_not_fail():
    """Bundle may contain attestations beyond what policy requires."""
    policy = EnterpriseContractPolicy(
        required_attestations=[{"kind": "build_provenance"}],
    )
    bundle = [_att("build_provenance"), _att("bonus_attest")]
    result = check_policy(policy, bundle)
    assert result.ok


# ---------------------------------------------------------------------------
# Checker — predicate_type matching
# ---------------------------------------------------------------------------


def test_predicate_type_match_passes():
    policy = EnterpriseContractPolicy(required_attestations=[{
        "kind": "build_provenance",
        "predicate_type": "https://slsa.dev/provenance/v0.2",
    }])
    bundle = [_att(
        "build_provenance",
        predicate_type="https://slsa.dev/provenance/v0.2",
    )]
    result = check_policy(policy, bundle)
    assert result.ok


def test_predicate_type_mismatch_fails():
    policy = EnterpriseContractPolicy(required_attestations=[{
        "kind": "build_provenance",
        "predicate_type": "https://slsa.dev/provenance/v0.2",
    }])
    bundle = [_att(
        "build_provenance",
        predicate_type="https://slsa.dev/provenance/v0.1",   # wrong version
    )]
    result = check_policy(policy, bundle)
    assert not result.ok
    assert any("predicate_type" in v for v in result.violations)


# ---------------------------------------------------------------------------
# Checker — require_verdict_pass
# ---------------------------------------------------------------------------


def test_verdict_pass_all_models_pass():
    policy = EnterpriseContractPolicy(required_attestations=[{
        "kind": "eval_pass", "require_verdict_pass": True,
    }])
    bundle = [_att("eval_pass", data={"verdicts": {
        "claude-sonnet": "pass", "llama-3": "pass",
    }})]
    assert check_policy(policy, bundle).ok


def test_verdict_pass_one_fail_blocks():
    policy = EnterpriseContractPolicy(required_attestations=[{
        "kind": "eval_pass", "require_verdict_pass": True,
    }])
    bundle = [_att("eval_pass", data={"verdicts": {
        "claude-sonnet": "pass", "llama-3": "fail",
    }})]
    result = check_policy(policy, bundle)
    assert not result.ok
    assert any("non-pass" in v and "llama-3" in v for v in result.violations)


def test_verdict_pass_empty_verdicts_dict_fails():
    policy = EnterpriseContractPolicy(required_attestations=[{
        "kind": "eval_pass", "require_verdict_pass": True,
    }])
    bundle = [_att("eval_pass", data={"verdicts": {}})]
    result = check_policy(policy, bundle)
    assert not result.ok
    assert any("no verdicts" in v for v in result.violations)


def test_verdict_pass_missing_verdicts_key_fails():
    policy = EnterpriseContractPolicy(required_attestations=[{
        "kind": "eval_pass", "require_verdict_pass": True,
    }])
    bundle = [_att("eval_pass", data={})]   # no "verdicts" key
    result = check_policy(policy, bundle)
    assert not result.ok


# ---------------------------------------------------------------------------
# All violations reported (not just first failure)
# ---------------------------------------------------------------------------


def test_multiple_violations_reported():
    policy = EnterpriseContractPolicy(required_attestations=[
        {"kind": "build_provenance"},
        {"kind": "eval_pass"},
        {"kind": "cat_abc_smoke"},
    ])
    bundle = [_att("build_provenance")]   # eval_pass + cat_abc_smoke missing
    result = check_policy(policy, bundle)
    assert len(result.violations) == 2


# ---------------------------------------------------------------------------
# Shipped default policy parses
# ---------------------------------------------------------------------------


def test_shipped_default_policy_parses():
    """The repo's ``policy/enterprise-contract.yaml`` must validate."""
    repo_root = Path(__file__).resolve().parents[2]
    default_path = repo_root / "policy" / "enterprise-contract.yaml"
    if not default_path.is_file():
        pytest.skip("policy/enterprise-contract.yaml not shipped")
    policy = load_policy(default_path)
    assert policy.schema_version == 1
    # The shipped policy demands 3 attestation kinds per brainstorm Q4.
    kinds = {r.kind for r in policy.required_attestations}
    assert {"build_provenance", "eval_pass", "cat_abc_smoke"} <= kinds
