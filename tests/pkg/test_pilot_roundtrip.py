"""End-to-end pilot roundtrip — Stage 0 slice 10.

The pilot contract: starting from ``acc/roles/coding_agent/`` in
THIS repo, build a ``.accpkg``, sign it (mocked cosign), install it
into a *vanilla* target where the role does NOT exist in
``roles/``, and verify the unpacked role lands where the dual-source
loader (Stage 1) will look for it.

This is the in-process variant — it exercises the full
build_pilot_pkg → cosign verify → acc-pkg install → registry pipeline
without a Docker dependency.  The vanilla-container variant
(Dockerfile + `kubectl cp` into the acc1 K8s hub) is a manual smoke
the operator runs after the hub is bootstrapped; that doesn't
exercise additional code paths and is documented in Stage 0's
``tasks.md`` 1.7 row.

Coverage of the proposal's section 1.7 (in-process portion):

* Extract ``coding_agent`` → build ``.accpkg``
* Sign with mocked cosign (pilot keypair shape)
* Install into a clean registry (different root from any prior run)
* Confirm role.yaml lands at the install path the dual-source loader
  will read from
* Confirm registry contains exactly one entry naming the package
* Confirm a second install is idempotent (was_already_installed=True)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Stage 2 cutover removed roles/coding_agent/ from in-tree.  The Stage 0
# pilot single-role roundtrip targeted that exact path; its successor is
# the Stage 2 family-pack roundtrip in tests/pkg/test_build_family_pkg.py
# plus the live smoke tools/smoke-acc1-hub.sh.  Whole file is skipped
# rather than deleted so the contract stays auditable.
pytestmark = pytest.mark.skip(
    reason="Stage 2 cutover: pilot single-role roundtrip superseded by "
    "family-pack roundtrip (tests/pkg/test_build_family_pkg.py)."
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "tools"))
import build_pilot_pkg as bp  # noqa: E402

from acc.pkg.cli import EXIT_OK, main  # noqa: E402
from acc.pkg.registry import Registry  # noqa: E402


@pytest.fixture
def autoroot(monkeypatch, tmp_path):
    """Route the packages root to a tmp dir so we install into a
    vanilla target (no coding_agent in /var/lib/acc/packages/).
    """
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "vanilla-target"))
    return tmp_path / "vanilla-target"


@pytest.fixture
def pilot_pkg(tmp_path):
    """Build the real coding_agent pilot pack into a tmp dist dir."""
    out = tmp_path / "dist" / "acc-coding-agent-0.1.0.accpkg"
    out.parent.mkdir(parents=True)
    bp.build_pilot("coding_agent", output=out)
    # Stand-in cosign signature artefact (Stage-0 sign step uses real
    # cosign offline; this test mocks the verify step so the sidecar
    # only needs to exist).
    sig = out.parent / (out.name + ".sig")
    sig.write_text("MOCK SIGNATURE", encoding="utf-8")
    pub = tmp_path / "pilot.pub"
    pub.write_text("-----BEGIN PUBLIC KEY-----\nFAKE\n-----END PUBLIC KEY-----\n",
                   encoding="utf-8")
    return out, sig, pub


def _ok_cosign(*a, **kw):
    return subprocess.CompletedProcess(
        args=a, returncode=0, stdout="Verified OK\n", stderr=""
    )


def _mock_cosign():
    return (
        patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"),
        patch("acc.pkg.verify.subprocess.run", side_effect=_ok_cosign),
    )


# ---------------------------------------------------------------------------
# Roundtrip
# ---------------------------------------------------------------------------


def test_pilot_extract_build_sign_install_roundtrip(autoroot, pilot_pkg):
    """The Stage-0 happy path, end to end."""
    pkg, sig, pub = pilot_pkg
    p_which, p_run = _mock_cosign()
    with p_which, p_run:
        rc = main([
            "install", str(pkg),
            "--signature", str(sig),
            "--key", str(pub),
        ])
    assert rc == EXIT_OK

    # 1) The unpacked role.yaml lands where the dual-source loader
    #    (Stage 1) will look for it.
    install_path = autoroot / "acc" / "coding-agent-0.1.0"
    role_yaml = install_path / "roles" / "coding_agent" / "role.yaml"
    assert role_yaml.is_file(), \
        f"expected role.yaml at {role_yaml} but it's missing"

    # 2) The bundled skill + MCPs landed too.
    assert (install_path / "skills" / "echo").is_dir()
    assert (install_path / "mcps" / "web_fetch").is_dir()
    assert (install_path / "mcps" / "echo_server").is_dir()

    # 3) Baseline refs (shell_exec, ssh_exec, arxiv, wikipedia) were
    #    NOT bundled — they stay in ACC core.
    assert not (install_path / "skills" / "shell_exec").exists()
    assert not (install_path / "skills" / "ssh_exec").exists()
    assert not (install_path / "mcps" / "arxiv").exists()
    assert not (install_path / "mcps" / "wikipedia").exists()

    # 4) The on-disk manifest copy was written by the installer.
    assert (install_path / "accpkg.yaml").is_file()

    # 5) Registry has exactly one entry.
    reg = Registry()
    entries = reg.list()
    assert len(entries) == 1
    assert entries[0].name == "@acc/coding-agent"
    assert entries[0].version == "0.1.0"


def test_pilot_idempotent_second_install(autoroot, pilot_pkg):
    """Reinstalling the same pilot pack is a no-op + exit 0 (matches
    the CLI contract in the proposal).
    """
    pkg, sig, pub = pilot_pkg
    p_which, p_run = _mock_cosign()
    with p_which, p_run:
        rc1 = main(["install", str(pkg), "--signature", str(sig), "--key", str(pub)])
        rc2 = main(["install", str(pkg), "--signature", str(sig), "--key", str(pub)])
    assert rc1 == EXIT_OK
    assert rc2 == EXIT_OK

    # Still exactly one registry entry — no duplication.
    reg = Registry()
    assert len(reg.list()) == 1


def test_pilot_install_refuses_without_signature(autoroot, pilot_pkg):
    """Stage 0's signing floor: install must refuse if no signature
    AND no --allow-unsigned override.
    """
    pkg, _, pub = pilot_pkg
    # Tamper: delete the signature so the inferred sidecar is missing.
    sig_path = pkg.parent / (pkg.name + ".sig")
    sig_path.unlink()
    rc = main(["install", str(pkg), "--key", str(pub)])
    # Exit 5 = EXIT_SIGNATURE per the CLI contract.
    from acc.pkg.cli import EXIT_SIGNATURE
    assert rc == EXIT_SIGNATURE


def test_pilot_install_allow_unsigned_bypass(autoroot, pilot_pkg):
    """``--allow-unsigned`` is the operator-explicit, audit-logged escape
    hatch from the signing floor.
    """
    pkg, _, _ = pilot_pkg
    rc = main(["install", str(pkg), "--allow-unsigned"])
    assert rc == EXIT_OK

    install_path = autoroot / "acc" / "coding-agent-0.1.0"
    assert (install_path / "roles" / "coding_agent" / "role.yaml").is_file()


# ---------------------------------------------------------------------------
# Vanilla-target sanity — these tests document the contract that
# Stage 1's dual-source loader depends on.
# ---------------------------------------------------------------------------


def test_install_path_matches_dual_source_loader_expectation(autoroot, pilot_pkg):
    """Stage 1's ``acc/cognitive_core.py`` dual-source loader will look
    for installed roles at::

        <ACC_PACKAGES_ROOT>/<scope>/<name>-<version>/roles/<role>/role.yaml

    This test pins the layout so a Stage 1 PR that changes the path
    catches the breakage here.
    """
    pkg, sig, pub = pilot_pkg
    p_which, p_run = _mock_cosign()
    with p_which, p_run:
        main(["install", str(pkg), "--signature", str(sig), "--key", str(pub)])

    expected = (
        autoroot / "acc" / "coding-agent-0.1.0"
        / "roles" / "coding_agent" / "role.yaml"
    )
    assert expected.is_file()


def test_registry_records_content_hash_for_drift_detection(autoroot, pilot_pkg):
    """``RegistryEntry.content_sha256`` is the same value the build
    stamped into the manifest — drift detection (Stage 1's
    ``acc-bench``) compares against this.
    """
    pkg, _, _ = pilot_pkg
    main(["install", str(pkg), "--allow-unsigned"])

    reg = Registry()
    entry = reg.find("@acc/coding-agent", "0.1.0")
    assert entry is not None
    assert len(entry.content_sha256) == 64
    assert all(c in "0123456789abcdef" for c in entry.content_sha256)
