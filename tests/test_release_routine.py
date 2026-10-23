"""`20261004-public-release-channels` Phase 4 -- one tag, both channels.

The Satellite leg existed (release-pipeline.sh).  The public leg is two
scripts: COPR builds the MIRROR's tag, and the GitHub Release on the mirror
attaches what COPR built.  Held in text: nothing here pushes a spearhead tree
anywhere public, every artefact is tied to the tag, the notes come from the
CHANGELOG, and a dry run narrates all of it.  The one piece of logic that is
pure (the CHANGELOG section) is tested as code.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RPM = ROOT / "packaging" / "rpm"
sys.path.insert(0, str(RPM))

import changelog_notes as notes  # noqa: E402


def _read(name: str) -> str:
    return (RPM / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- the notes

CHANGELOG = """# Changelog

## [Unreleased]

### Packaging
- something pending

## [0.27.0] — 2026-10-07

**The release line.**

### Added
- a thing

## [0.26.4] — 2026-10-03

- older
"""


def test_a_versions_section_is_its_body_up_to_the_next_heading():
    body = notes.section(CHANGELOG, "0.27.0")
    assert body.startswith("\n**The release line.**") or body.startswith("**The release line.**")
    assert "- a thing" in body and "older" not in body and "pending" not in body
    assert body.endswith("\n")


def test_the_last_section_runs_to_the_end_and_a_missing_one_is_none():
    assert "- older" in notes.section(CHANGELOG, "0.26.4")
    assert notes.section(CHANGELOG, "9.9.9") is None
    assert notes.date_of(CHANGELOG, "0.27.0") == "2026-10-07"
    assert notes.date_of(CHANGELOG, "Unreleased") is None


def test_the_cli_prints_the_section_and_refuses_a_version_without_one(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG, encoding="utf-8")
    ok = subprocess.run([sys.executable, str(RPM / "changelog_notes.py"), "v0.27.0", str(path)],
                        capture_output=True, text=True, check=False)
    assert ok.returncode == 0 and "- a thing" in ok.stdout and "older" not in ok.stdout
    bad = subprocess.run([sys.executable, str(RPM / "changelog_notes.py"), "9.9.9", str(path)],
                         capture_output=True, text=True, check=False)
    assert bad.returncode == 1 and "no '## [9.9.9]' section" in bad.stderr


def test_the_real_changelog_has_a_section_for_the_current_version():
    """The release notes come from here: the version in pyproject must have
    one, or the release refuses -- better to learn that now."""
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    assert notes.section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version), version


# ------------------------------------------------------------ the scripts

def test_copr_builds_the_mirrors_tag_never_a_spearhead_tree():
    s = _read("release-copr.sh")
    assert 'MIRROR_URL="${MIRROR_URL:-https://github.com/flg77/acc.git}"' in s
    assert 'git ls-remote --tags "$MIRROR_URL" "refs/tags/$TAG"' in s      # the tag must be ON the mirror
    assert "acc-promote" in s                                               # and that is what puts it there
    assert "copr-cli buildscm" in s and "--method make_srpm" in s and '--commit "$TAG"' in s
    assert "copr-cli watch-build" in s
    assert '"$OUT/$VERSION.build-id"' in s                                  # the id release-github.sh reads
    assert "git push" not in s and "acc-spearhead" not in s.replace("acc-spearhead/", "")


def test_the_release_attaches_what_copr_built_and_checks_it():
    s = _read("release-github.sh")
    assert "download.copr.fedorainfracloud.org/results" in s
    assert "printf '%08d' \"$BUILD_ID\"" in s                                     # COPR's result directory name
    assert "rpm -qp --qf '%{VERSION}'" in s and "carries version $nvr, not $VERSION" in s
    assert "rpmbuild" not in s and "build.sh" not in s                      # never rebuilt locally
    assert "sha256sum" in s and "cosign sign-blob --yes --bundle SHA256SUMS.sigstore.json SHA256SUMS" in s
    assert "--no-sign" in s                                                 # unsigned is a choice, said aloud
    assert 'gh release create "$TAG" --repo "$MIRROR_REPO" --verify-tag' in s
    assert "gh release upload" in s and "--clobber" in s                    # idempotent re-run
    assert 'changelog_notes.py" "$VERSION"' in s and 'git show "$TAG:CHANGELOG.md"' in s   # notes AT the tag
    assert "release-manifests" not in s and "flg77/acc-operator" in s      # the operator releases its own manifests, elsewhere


def test_the_pipeline_has_a_public_leg_that_follows_the_satellite_proof():
    s = _read("release-pipeline.sh")
    assert "--public) PUBLIC=1" in s
    i_client = s.index("6. a client must agree with itself")
    i_public = s.index("7. the public channel")
    assert i_client < i_public                                              # public only after the proof
    assert 'bash "$HERE/release-copr.sh" "$TAG" "${pub_args[@]}"' in s
    assert 'bash "$HERE/release-github.sh" "$TAG" "${pub_args[@]}"' in s
    assert '[[ $DRY_RUN == 1 ]] && pub_args+=(--dry-run)' in s             # a dry run narrates all legs
    assert "acc-promote" in s                                               # and the promote stays a human step


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git (tags the checkout); the acc-build test image has none")
def test_a_dry_run_narrates_every_leg_without_tools_or_network(tmp_path):
    """The three scripts run end to end with --dry-run on a tag that exists
    only here, with no copr-cli, no gh, no cosign and no mirror tag: they
    describe what they would do and succeed."""
    tag = "v0.0.0-release-routine-test"
    subprocess.run(["git", "tag", tag, "HEAD"], cwd=ROOT, check=True, capture_output=True)
    try:
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "WORK": str(tmp_path / "w"),
               "LANG": "C.UTF-8"}
        copr = subprocess.run(["bash", str(RPM / "release-copr.sh"), tag, "--dry-run"],
                              cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        assert copr.returncode == 0, copr.stdout + copr.stderr
        assert "would: copr-cli buildscm" in copr.stdout and "--commit " + tag in copr.stdout
        assert "would: copr-cli watch-build" in copr.stdout
        gh = subprocess.run(["bash", str(RPM / "release-github.sh"), tag, "--dry-run"],
                            cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        # the tag's CHANGELOG has no [0.0.0-release-routine-test] section: refused, by design
        assert gh.returncode == 1 and "no section for" in gh.stderr
    finally:
        subprocess.run(["git", "tag", "-d", tag], cwd=ROOT, check=False, capture_output=True)


def test_the_readme_tells_the_routine():
    readme = _read("README.md")
    assert "## The release routine" in readme
    for leg in ("release-pipeline.sh", "acc-promote", "release-copr.sh", "release-github.sh", "--public"):
        assert leg in readme, leg
    assert "cosign verify-blob" in readme
