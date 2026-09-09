# packaging/rpm/version.py -- the project version -> an RPM (Version, Release).
#
# The internal Satellite channel is now the distribution base, so every artefact
# that lands in it must carry a NEVRA that sorts the way the project's semantic
# version says it should.  RPM cannot express a semantic version directly: it
# compares Version and Release separately, and `-` is not allowed in either.
#
#   0.14.4              -> 0.14.4  1              the release
#   0.15.0-rc.1         -> 0.15.0  0.rc.1         BEFORE 0.15.0-1, as semver says
#   0.15.0rc1 (PEP 440) -> 0.15.0  0.rc1          the same, spelled Python's way
#   0.14.4 + dirty tree -> 0.14.4  0.<stamp>.g<sha>   a snapshot, never the release
#
# The rule that matters: a pre-release or snapshot takes release `0.…`, which RPM
# orders below the plain `1` of the real release.  A build that is not exactly the
# tagged, clean tree can therefore never impersonate the release in the channel.
from __future__ import annotations

import re
import subprocess
from pathlib import Path

#: `-` is forbidden and `~`/`^` carry ordering meaning in an RPM Release; keep to
#: what an RPM field may hold, and let `.` be the only separator.
_UNSAFE = re.compile(r"[^A-Za-z0-9.]+")

#: A PEP 440 pre-release glued to the version (`0.15.0rc1`, `1.0.0b2`, `2.0.0a1`).
_PEP440_PRE = re.compile(r"^(\d+(?:\.\d+)*)((?:a|b|rc|alpha|beta|dev)\.?\d*)$")


def _clean(text: str) -> str:
    return _UNSAFE.sub(".", text).strip(".")


def split_version(version: str) -> tuple[str, str]:
    """A project version -> (rpm_version, pre_release), the second often empty.

    Understands the semantic form (`0.15.0-rc.1`, with optional `+build`) and the
    PEP 440 spelling setuptools accepts (`0.15.0rc1`), because the version this
    reads is the one in `pyproject.toml`.
    """
    core = version.strip().split("+", 1)[0]
    if "-" in core:
        head, _, tail = core.partition("-")
        return _clean(head), _clean(tail)
    match = _PEP440_PRE.match(core)
    if match:
        return _clean(match.group(1)), _clean(match.group(2))
    return _clean(core), ""


def build_metadata(version: str) -> str:
    """The `+…` part of a semantic version, as an RPM-safe fragment."""
    _, _, meta = version.strip().partition("+")
    return _clean(meta)


def release(version: str, *, snapshot: str = "") -> str:
    """The Release field (without `%{?dist}`) for *version*.

    *snapshot* names a build that is not the tagged, clean tree; it always sorts
    below the real release.
    """
    _, pre = split_version(version)
    meta = build_metadata(version)
    if snapshot:
        parts = ["0", pre, _clean(snapshot), meta]
    elif pre:
        parts = ["0", pre, meta]
    else:
        parts = ["1", meta]
    return ".".join(p for p in parts if p)


def git_snapshot(root: Path | str = ".", version: str = "") -> str:
    """`""` when this tree *is* the released tag, else a snapshot fragment.

    A tree that is clean and sits exactly on `v<version>` is the release.  Any
    other tree -- untagged, a tag for another version, or carrying local changes
    -- is a snapshot, and says so in its Release.  A tree that is not a git
    checkout at all (an unpacked archive, a build root) is taken at its word.
    """
    root = Path(root)

    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True, text=True, check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return out.stdout.strip()

    if git("rev-parse", "--is-inside-work-tree") != "true":
        return ""
    head = git("rev-parse", "--short=12", "HEAD") or "unknown"
    dirty = bool(git("status", "--porcelain"))
    tags = (git("tag", "--points-at", "HEAD") or "").split()
    on_tag = version and f"v{version}" in tags
    if on_tag and not dirty:
        return ""
    stamp = git("show", "-s", "--format=%ct", "HEAD") or "0"
    return f"{stamp}.g{head}" + (".dirty" if dirty else "")


def version_release(version: str, root: Path | str = ".") -> tuple[str, str]:
    """(Version, Release) for *version* as built from *root*."""
    rpm_version, _ = split_version(version)
    return rpm_version, release(version, snapshot=git_snapshot(root, version))


if __name__ == "__main__":                                       # build.sh calls this
    import sys

    project_version = sys.argv[1]
    where = sys.argv[2] if len(sys.argv) > 2 else "."
    print("\n".join(version_release(project_version, where)))
