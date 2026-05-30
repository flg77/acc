"""Regression test for the apply-workspace BASE_REAL='/' glob bug.

Symptom on lighthouse (v0.3.21):
    `acc-workdir` creation never happened after picking a directory in
    the TUI workspace modal; no prompt interaction possible.

Cause:
    `acc-deploy.sh apply-workspace` enforces a host-path boundary via:

        case "$PATH_REAL/" in
            "$BASE_REAL"/*) : ;;
            *) REFUSED ;;
        esac

    With the default `ACC_WORKSPACE_HOST_ROOT=/` → `ACC_WORKSPACE_BASE=/`,
    the pattern `"$BASE_REAL"/*` expanded to `//*` (literal `/` + `/*`),
    which only matches paths starting with TWO slashes.  Every legitimate
    absolute host path (`/git/ml/agentic/acc-workdir`, …) was refused;
    the watcher logged the rejection and the TUI never saw the failure.

Fix (v0.3.22): strip the trailing slash from BASE_REAL before forming
the glob, so `BASE_REAL=/` produces pattern `/*` and any absolute path
is accepted.

This test extracts the boundary-check fragment from acc-deploy.sh and
runs it under bash with representative BASE / HOST_PATH combinations.
Pure shell — no podman, no agents.  Skipped on Windows where bash is
not guaranteed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

if sys.platform.startswith("win"):
    pytest.skip("bash-only test", allow_module_level=True)
if shutil.which("bash") is None:
    pytest.skip("bash not available", allow_module_level=True)


_SNIPPET = r"""
set -uo pipefail
BASE_REAL="$1"
PATH_REAL="$2"
BASE_GLOB="${BASE_REAL%/}"
case "$PATH_REAL/" in
    "$BASE_GLOB"/*) echo OK ; exit 0 ;;
    *)              echo REFUSED ; exit 2 ;;
esac
"""


def _run(base: str, path: str) -> tuple[int, str]:
    """Run the snippet with the given BASE / PATH and return (exit, stdout)."""
    proc = subprocess.run(
        ["bash", "-c", _SNIPPET, "_", base, path],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode, proc.stdout.strip()


# Whole-host root — the regression case.

def test_root_base_accepts_arbitrary_absolute_path():
    code, out = _run("/", "/git/ml/agentic/acc-workdir")
    assert code == 0 and out == "OK"


def test_root_base_accepts_home_subdir():
    code, out = _run("/", "/home/flg/proj")
    assert code == 0 and out == "OK"


def test_root_base_accepts_root_itself():
    code, out = _run("/", "/")
    assert code == 0 and out == "OK"


# Narrowed base — boundary must still hold.

def test_home_base_accepts_subdir():
    code, out = _run("/home/flg", "/home/flg/proj/foo")
    assert code == 0 and out == "OK"


def test_home_base_refuses_sibling():
    code, out = _run("/home/flg", "/home/other/proj")
    assert code == 2 and out == "REFUSED"


def test_home_base_refuses_etc():
    code, out = _run("/home/flg", "/etc/passwd")
    assert code == 2 and out == "REFUSED"


def test_home_base_accepts_base_itself():
    code, out = _run("/home/flg", "/home/flg")
    assert code == 0 and out == "OK"
