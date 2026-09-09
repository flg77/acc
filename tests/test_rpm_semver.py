"""The project version -> an RPM (Version, Release), for the Satellite channel.

The internal Satellite is the distribution base now, so an artefact that lands in
it must sort the way its semantic version says.  RPM cannot hold a semantic
version directly: `-` is illegal in both fields and ordering is per field.  The
one rule that matters is that a pre-release or a snapshot takes a `0.…` release,
which RPM orders below the plain `1` of the real release.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# By path, not by sys.path: `packaging/rpm/` on sys.path would make `rpm` resolve
# as a namespace package and shadow the real RPM bindings on an RPM host.
_spec = importlib.util.spec_from_file_location(
    "acc_rpm_version", ROOT / "packaging" / "rpm" / "version.py"
)
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)


@pytest.mark.parametrize(
    "project, rpm_version, rpm_release",
    [
        ("0.14.4", "0.14.4", "1"),                       # the release
        ("1.0.0", "1.0.0", "1"),
        ("0.15.0-rc.1", "0.15.0", "0.rc.1"),             # semver pre-release
        ("0.15.0-rc.2", "0.15.0", "0.rc.2"),
        ("0.15.0rc1", "0.15.0", "0.rc1"),                # the PEP 440 spelling
        ("1.0.0b2", "1.0.0", "0.b2"),
        ("0.14.4+build.7", "0.14.4", "1.build.7"),       # build metadata
        ("1.0.0-beta.2+g9f1c", "1.0.0", "0.beta.2.g9f1c"),
    ],
)
def test_the_mapping(project, rpm_version, rpm_release):
    assert V.split_version(project)[0] == rpm_version
    assert V.release(project) == rpm_release


def test_no_field_carries_a_character_rpm_rejects():
    """`-` ends a field and `~`/`^` carry ordering meaning; neither may slip in."""
    for project in ("0.15.0-rc.1", "1.0.0-beta.2+g9f1c", "0.14.4+build.7", "2.0.0a1"):
        version, rel = V.split_version(project)[0], V.release(project)
        for field in (version, rel):
            assert field and not set(field) & set("-~^: "), field


def test_a_snapshot_never_impersonates_the_release():
    snap = V.release("0.14.4", snapshot="1757000000.gabc123")
    assert snap.startswith("0.") and V.release("0.14.4") == "1"


def test_a_pre_release_snapshot_keeps_both_markers():
    snap = V.release("0.15.0-rc.1", snapshot="1757000000.gabc")
    assert snap.startswith("0.rc.1.") and "gabc" in snap


@pytest.mark.parametrize(
    "lower, higher",
    [
        (("0.14.4", "0.rc.1"), ("0.14.4", "1")),           # pre-release before release
        (("0.14.4", "0.1757000000.gabc"), ("0.14.4", "1")),  # snapshot before release
        (("0.14.4", "1"), ("0.14.4", "2")),                # a packaging rebuild
        (("0.14.4", "1"), ("0.15.0", "0.rc.1")),           # next version's rc still wins
        (("0.15.0", "0.rc.1"), ("0.15.0", "0.rc.2")),
    ],
)
def test_rpm_itself_agrees_with_the_ordering(lower, higher):
    rpm = pytest.importorskip("rpm", reason="rpm bindings only exist on an RPM host")
    if not hasattr(rpm, "labelCompare"):
        pytest.skip("not the RPM bindings")
    assert rpm.labelCompare((None, *lower), (None, *higher)) < 0


def test_git_snapshot_marks_a_tree_that_is_not_the_tagged_release(tmp_path):
    """A build that is not the clean, tagged tree says so in its Release."""
    def git(*args, cwd=tmp_path):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    try:
        git("init", "-q", "-b", "main")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git unavailable")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (tmp_path / "f").write_text("1", encoding="utf-8")
    git("add", "f")
    git("commit", "-qm", "one")

    assert V.git_snapshot(tmp_path, "0.14.4") != ""        # untagged -> a snapshot
    git("tag", "v0.14.4")
    assert V.git_snapshot(tmp_path, "0.14.4") == ""        # the tagged, clean tree
    assert V.git_snapshot(tmp_path, "0.14.5") != ""        # a tag for another version
    (tmp_path / "f").write_text("2", encoding="utf-8")
    assert ".dirty" in V.git_snapshot(tmp_path, "0.14.4")  # local changes


def test_a_tree_that_is_not_a_checkout_is_taken_at_its_word(tmp_path):
    """The RPM build unpacks an archive; there is no git there to ask."""
    assert V.git_snapshot(tmp_path, "0.14.4") == ""


def test_the_spec_and_build_script_use_the_mapping():
    spec = (ROOT / "packaging" / "rpm" / "acc.spec").read_text(encoding="utf-8")
    build = (ROOT / "packaging" / "rpm" / "build.sh").read_text(encoding="utf-8")
    assert "Release:        %{acc_release}%{?dist}" in spec
    assert "%{!?acc_release: %global acc_release 1}" in spec
    # the wheel keeps the project version even when the RPM Version differs
    assert "agentic_cell_corpus-%{acc_wheel_version}-py3-none-any.whl" in spec
    assert 'version.py" "$VERSION" "$ROOT"' in build
    assert 'RPM_RELEASE="${RELEASE:-${_VR[1]}}"' in build


def test_the_venv_bytecode_is_package_owned_and_checked_hash():
    """acc1 upgraded 0.14.3 -> 0.14.4 and kept running 0.14.3: a runtime-written,
    timestamp-invalidated .pyc owned by no package survived the upgrade."""
    spec = (ROOT / "packaging" / "rpm" / "acc.spec").read_text(encoding="utf-8")
    assert "-m compileall" in spec and "--invalidation-mode checked-hash" in spec
    # the buildroot must not end up inside the embedded file names
    assert "-s %{buildroot} -p /" in spec
    # and it has to run after pip, or there is nothing to compile
    assert spec.index("pip install") < spec.index("-m compileall")
