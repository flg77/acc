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


# ---------------------------------------------------------------------------
# the release pipeline
# ---------------------------------------------------------------------------

def _pipeline() -> str:
    return (ROOT / "packaging" / "rpm" / "release-pipeline.sh").read_text(encoding="utf-8")


def test_the_pipeline_builds_the_tag_not_the_working_tree():
    """What lands in the channel must be exactly what was tagged."""
    s = _pipeline()
    assert "git archive --format=tar" in s and '"$TAG"' in s
    # and it refuses a tag that does not exist, or one whose pyproject disagrees
    assert 'git rev-parse -q --verify "refs/tags/$TAG"' in s
    assert '"$TAG_VERSION" != "$VERSION"' in s


def test_the_pipeline_refuses_a_package_carrying_cuda():
    s = _pipeline()
    # `/nvidia/` and not `nvidia`: sympy ships files whose NAMES contain it.
    assert "grep -c /nvidia/" in s and "the CPU torch pin did not hold" in s


def test_the_pipeline_checks_the_layout_it_promises():
    s = _pipeline()
    for path in ("/usr/bin/acc", "/usr/share/acc", "/etc/acc", "/var/lib/acc"):
        assert path in s, path


def test_the_pipeline_makes_the_client_agree_with_itself():
    """`rpm -q` alone once called a broken upgrade a success: the package said
    0.14.4 while the command still ran 0.14.3."""
    s = _pipeline()
    assert "rpm -q --qf '%{VERSION}' acc" in s
    assert "acc --version" in s
    assert '"$cmd" != "$VERSION"' in s
    assert "Do not ship this" in s


def test_the_pipeline_can_say_what_it_would_do_without_doing_it():
    s = _pipeline()
    assert "--dry-run" in s and "would:" in s


def test_the_pipeline_ships_both_packages_runtime_first():
    """The split made this a two-package release.  Publishing only `acc` leaves
    `dnf install acc` unresolvable, so the pipeline finds, checks and publishes
    both — and the runtime goes first, so the window between the two uploads has
    a runtime with no host package rather than the reverse."""
    s = _pipeline()
    assert "acc-runtime-$VERSION-$RELEASE_N" in s
    assert "breaks 'dnf install acc'" in s
    runtime_publish = s.index('publish-satellite.sh" "$LOCAL_RUNTIME"')
    host_publish = s.index('publish-satellite.sh" "$LOCAL_RPM"')
    assert runtime_publish < host_publish, "the runtime must be published first"


def test_the_pipeline_checks_each_package_for_what_it_should_hold():
    """After the split, looking for /usr/bin/acc in the host package finds
    nothing — which is exactly how the v0.16.0 run failed, correctly."""
    s = _pipeline()
    assert "acc-runtime:$path" in s and "acc:$path" in s
    # the host package must depend on the runtime, or a host installs nothing
    assert "acc does not require acc-runtime" in s
    # CUDA would show up in the runtime, which is where the ML stack lives
    assert "CUDA files in acc-runtime" in s


# ---------------------------------------------------------------------------
# signing, with the channel's key from OpenBao
# ---------------------------------------------------------------------------

def _rpm_file(name: str) -> str:
    return (ROOT / "packaging" / "rpm" / name).read_text(encoding="utf-8")


def test_the_signer_has_no_network_and_keeps_the_key_on_a_tmpfs():
    """The private key is piped in over ssh: it must never touch a disk, and the
    container it lives in must not be able to send it anywhere."""
    s = _rpm_file("sign-rpms.sh")
    assert "--network none" in s
    assert "--tmpfs /gnupg:rw,mode=0700" in s
    assert "field private_key | ssh" in s          # on stdin, never through a file


def test_the_signer_image_holds_nothing_but_the_signing_tools():
    c = _rpm_file("signer/Containerfile")
    assert "rpm-sign gnupg2" in c and 'ENTRYPOINT ["/usr/local/bin/acc-sign"]' in c


def test_every_signature_is_checked_against_the_public_key_alone():
    inner = _rpm_file("signer/acc-sign.sh")
    assert "rpmkeys --dbpath /tmp/rpmdb --import /tmp/channel.pub" in inner
    assert '*"signatures OK"*' in inner


def test_the_signer_refuses_a_key_that_is_not_the_one_the_vault_named():
    assert "^fpr:::::::::${FPR}:" in _rpm_file("signer/acc-sign.sh")


def test_sign_rpms_finds_a_python_that_actually_runs():
    """On Windows `python3` can be a Microsoft Store stub that prints an install
    prompt and fails -- the first run picked exactly that."""
    s = _rpm_file("sign-rpms.sh")
    assert "for c in python3 python py" in s and "-c 'import json'" in s


def test_publishing_refuses_an_unsigned_package_into_a_keyed_channel():
    """A subscribed host is handed gpgcheck=1 for a channel with a key, so an
    unsigned package there is an install failure on every such host."""
    s = _rpm_file("publish-satellite.sh")
    assert "REFUSING:" in s and "does not verify against the signing key" in s
    # checked against the key the CHANNEL serves -- the one a client verifies with
    assert "content-credentials info --id" in s and '["Content"]' in s
    # a channel with no key yet still takes packages
    assert '"NO-KEY"' in s or "NO-KEY)" in s


def test_the_pipeline_signs_before_it_publishes():
    s = _pipeline()
    assert s.index('bash "$HERE/sign-rpms.sh"') < s.index('publish-satellite.sh" "$LOCAL_RUNTIME"')
    assert "--unsigned) SIGN=0" in s
    assert "signing needs BAO_TOKEN" in s
