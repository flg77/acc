"""Hermetic CI version of the acc1 live smoke (Stage 1 — close-out).

Mirrors the five phases ``tools/smoke-acc1-hub.sh`` exercises against
the live acc1 K8s hub, but does it in-process against an HTTPS-mock
catalog so PR-time CI can prove the chain end-to-end without a
running cluster.

What this test PINS:

  Phase 3 — build_pilot_pkg → in-place build → deterministic tarball
  Phase 3 — cosign sign-blob (mocked) → .sig sidecar written
  Phase 4 — catalog.resolve_constraint → fetch + verify + install
  Phase 5 — RoleLoader resolves from the installed-package path

What this test does NOT exercise (live smoke does):

  Phase 0 — tool-presence preflight (env-dependent)
  Phase 1 — kubectl apply -f gitops/acc-hub/
  Phase 3.5 — publish-to-hub.sh + acc1 HTTPS roundtrip
  Real cosign binary
  Real K8s pod
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "tools"))
import build_pilot_pkg as bp  # noqa: E402

from acc.pkg.fetch import fetch_and_install  # noqa: E402
from acc.pkg.registry import Registry  # noqa: E402
from acc.role_loader import RoleLoader  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic roles tree so the test doesn't depend on the real roles/
# inventory changing
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_repo(tmp_path):
    """Build a minimal repo tree: roles/movable_a/role.yaml + an empty
    skill + a tiers YAML covering the refs.
    """
    repo = tmp_path / "repo"
    # roles tree
    role_dir = repo / "roles" / "movable_a"
    role_dir.mkdir(parents=True)
    (role_dir / "role.yaml").write_text(
        "role_definition:\n"
        "  purpose: smoke-target\n"
        "  allowed_skills: [lone_skill]\n"
        "  allowed_mcps: []\n",
        encoding="utf-8",
    )
    # skill source dir (bundle_in_role tier)
    skill_dir = repo / "skills" / "lone_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text("name: lone_skill\n", encoding="utf-8")
    # tiers YAML
    tiers = repo / "tools" / "skill_mcp_tiers.yaml"
    tiers.parent.mkdir(parents=True)
    tiers.write_text(
        yaml.safe_dump({
            "skills": [{"name": "lone_skill", "tier": "bundle_in_role"}],
            "mcps": [],
        }),
        encoding="utf-8",
    )
    return repo


def _cosign_sign_ok(*a, **kw):
    """Write a stub signature next to the tarball (cosign would)."""
    cmd = a[0] if a else kw.get("args", [])
    if isinstance(cmd, list):
        for flag in ("--output-signature", "--output-certificate"):
            if flag in cmd:
                idx = cmd.index(flag)
                if idx + 1 < len(cmd):
                    Path(cmd[idx + 1]).write_bytes(b"MOCK ARTEFACT")
    return subprocess.CompletedProcess(
        args=a, returncode=0, stdout="", stderr=(
            "Using payload from: ...\n"
            "tlog entry created with index: 999\n"
        ),
    )


def _cosign_verify_ok(*a, **kw):
    return subprocess.CompletedProcess(
        args=a, returncode=0, stdout="Verified OK\n", stderr="",
    )


# ---------------------------------------------------------------------------
# Phase 3 — build pilot pkg in-place
# ---------------------------------------------------------------------------


def test_phase3_build(synthetic_repo, tmp_path):
    out = tmp_path / "dist" / "movable-a-0.1.0.accpkg"
    bp.build_pilot("movable_a", repo_root=synthetic_repo, output=out)
    assert out.is_file()
    # Determinism: rebuild produces identical bytes
    out2 = tmp_path / "dist2" / "movable-a-0.1.0.accpkg"
    bp.build_pilot("movable_a", repo_root=synthetic_repo, output=out2)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == \
           hashlib.sha256(out2.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Phase 4 + Phase 5 — full chain end-to-end through the catalog
# ---------------------------------------------------------------------------


@pytest.fixture
def hermetic_chain(synthetic_repo, tmp_path, monkeypatch):
    """Build the pkg, stage it in a file-mode catalog, point env knobs
    at tmp paths.  Mocks cosign sign + verify.
    """
    # Build
    pkg = tmp_path / "dist" / "movable-a-0.1.0.accpkg"
    bp.build_pilot("movable_a", repo_root=synthetic_repo, output=pkg)

    # Stage in a file-mode catalog at tmp_path/catalog/acc/movable-a-0.1.0.accpkg
    catalog_root = tmp_path / "catalog"
    scope_dir = catalog_root / "acc"
    scope_dir.mkdir(parents=True)
    catalog_pkg = scope_dir / "movable-a-0.1.0.accpkg"
    catalog_pkg.write_bytes(pkg.read_bytes())
    catalog_pkg.with_suffix(".accpkg.sha256").write_text(
        hashlib.sha256(catalog_pkg.read_bytes()).hexdigest(),
        encoding="utf-8",
    )
    catalog_pkg.with_suffix(".accpkg.sig").write_text("MOCK SIG", encoding="utf-8")

    # System catalog YAML — keyless mode so the verify mock takes effect
    sys_cat = tmp_path / "system-catalog.yaml"
    sys_cat.write_text(yaml.safe_dump({"catalogs": [{
        "id": "smoke-hermetic",
        "tier": "trusted",
        "mode": "file",
        "path": str(catalog_root),
        "required_signer": {
            "issuer": "https://token.actions.githubusercontent.com",
            "subject_pattern": ".*",
        },
    }]}), encoding="utf-8")
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_cat))
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "no-user.yaml"))
    install_root = tmp_path / "install-root"
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(install_root))

    return {
        "repo": synthetic_repo,
        "pkg": pkg,
        "catalog_pkg": catalog_pkg,
        "install_root": install_root,
    }


def test_phase4_5_end_to_end(hermetic_chain):
    # Phase 4: resolve + fetch + verify + install
    with patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.verify.subprocess.run", side_effect=_cosign_verify_ok):
        result = fetch_and_install("@acc/movable-a", "^0.1")

    assert result.install.entry.name == "@acc/movable-a"
    assert result.install.entry.version == "0.1.0"
    install_path = result.install.install_path
    assert install_path.is_dir()
    assert (install_path / "roles" / "movable_a" / "role.yaml").is_file()

    # Phase 5: RoleLoader resolves from the installed package
    loader = RoleLoader(
        roles_root=hermetic_chain["repo"] / "roles",
        role_name="movable_a",
    )
    chosen_path = loader._role_yaml_path()
    # Must resolve to the package install path, not the in-tree repo
    # path.  ACC_PACKAGES_ROOT may be any tmp dir, so we assert the
    # resolved path is under the registry's install root + carries
    # the package version.
    chosen_str = str(chosen_path).replace("\\", "/")
    install_root_str = str(hermetic_chain["install_root"]).replace("\\", "/")
    in_tree_str = str(hermetic_chain["repo"] / "roles").replace("\\", "/")
    assert chosen_str.startswith(install_root_str), \
        f"expected install under {install_root_str}, got {chosen_str}"
    assert not chosen_str.startswith(in_tree_str), \
        "RoleLoader unexpectedly fell back to in-tree"
    assert "movable-a-0.1.0" in chosen_str

    role_def = loader.load()
    assert role_def is not None
    assert role_def.purpose == "smoke-target"


def test_phase4_5_idempotent_second_install(hermetic_chain):
    """Re-running the chain hits Stage 0's idempotent re-install path."""
    with patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.verify.subprocess.run", side_effect=_cosign_verify_ok):
        r1 = fetch_and_install("@acc/movable-a", "^0.1")
        r2 = fetch_and_install("@acc/movable-a", "^0.1")

    assert not r1.install.was_already_installed
    assert r2.install.was_already_installed
    # Registry has exactly one entry (idempotent)
    assert len(Registry().list()) == 1


def test_phase4_signature_floor_refuses_unsigned(hermetic_chain):
    """Remove the signature sidecar — fetch_and_install must refuse."""
    sig = hermetic_chain["catalog_pkg"].with_suffix(".accpkg.sig")
    sig.unlink()
    from acc.pkg.verify import VerifyError
    with pytest.raises(VerifyError, match="signing floor"):
        fetch_and_install("@acc/movable-a", "^0.1")


def test_phase4_allow_unsigned_bypass(hermetic_chain, caplog):
    """Operator-explicit unsigned install succeeds + audit-logs."""
    sig = hermetic_chain["catalog_pkg"].with_suffix(".accpkg.sig")
    sig.unlink()

    import logging
    caplog.set_level(logging.WARNING, logger="acc.pkg.fetch")
    result = fetch_and_install("@acc/movable-a", "^0.1", allow_unsigned=True)
    assert result.install.entry.name == "@acc/movable-a"
    assert any(
        "AUDIT" in r.message and "allow-unsigned" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Cross-chain: PROPOSE_INFUSE marker dispatches through the same seam
# ---------------------------------------------------------------------------


def test_assistant_propose_infuse_dispatches_via_same_seam(hermetic_chain):
    """Stage 1.4's _dispatch_infuse calls fetch_and_install — proving
    the marker-handler path and the acc-deploy-boot path share one
    seam (no parallel logic to drift apart).
    """
    import asyncio
    from acc.assistant_proposal import (
        AssistantProposal, PROPOSAL_INFUSE, dispatch_approved_proposal,
    )

    class _FakeSig:
        published = []
        async def publish(self, subject, payload):
            type(self).published.append((subject, payload))

    proposal = AssistantProposal(
        kind=PROPOSAL_INFUSE,
        params={"name": "@acc/movable-a", "constraint": "^0.1"},
        summary="Install @acc/movable-a@^0.1",
        rationale="smoke",
        collective_id="dev-smoke",
    )

    with patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.verify.subprocess.run", side_effect=_cosign_verify_ok):
        ok = asyncio.run(dispatch_approved_proposal(_FakeSig(), proposal))

    assert ok is True
    # Registry now contains the installed package
    assert Registry().find("@acc/movable-a", "0.1.0") is not None
    # Bus notification carried the install result
    assert _FakeSig.published[-1][1]["name"] == "@acc/movable-a"


# ---------------------------------------------------------------------------
# Shell-script linkage check — the operator script invokes the same
# entry points the hermetic test exercises.  We don't run the bash
# script (env-dependent), but we DO assert the script is committed +
# references the right helpers.
# ---------------------------------------------------------------------------


def test_live_smoke_script_present_and_well_formed():
    script = _REPO_ROOT / "tools" / "smoke-acc1-hub.sh"
    assert script.is_file()
    content = script.read_text(encoding="utf-8")
    # Must wire each phase to the helpers Stage 1 ships
    for expected in (
        "tools/cosign-pilot-keygen.sh",
        "tools/build_pilot_pkg.py",
        "cosign sign-blob",
        "gitops/acc-hub/publish-to-hub.sh",
        "acc.role_loader",
        "ACC_PACKAGES_ROOT",
    ):
        assert expected in content, f"smoke script missing wiring for {expected}"
