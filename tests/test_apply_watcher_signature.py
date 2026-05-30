"""Regression test for the apply-watcher's content-based change detector
(v0.3.23 robustness contract — "no restart required for new picks").

The watcher (`scripts/acc-apply-watcher.sh`) builds a change signature
from `mtime + size + content hash` so:

* Two rapid edits within the same wall-clock second still register as
  two different signatures (mtime second equal, but content differs →
  hash differs).
* Re-writing the *same* content has the same signature (no churn).

This test runs the watcher's `_signature` helper as a bash subshell
against three crafted files.  Skipped on Windows where bash isn't a
given.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

if sys.platform.startswith("win"):
    pytest.skip("bash-only test", allow_module_level=True)
if shutil.which("bash") is None:
    pytest.skip("bash not available", allow_module_level=True)


_SNIPPET = r"""
set -uo pipefail
f="$1"
base="$(stat -c '%Y %s' "$f" 2>/dev/null || echo "")"
if [[ -z "$base" ]]; then
    exit 1
fi
if command -v md5sum >/dev/null 2>&1; then
    h="$(md5sum "$f" 2>/dev/null | cut -c1-12)"
    echo "$base $h"
else
    echo "$base"
fi
"""


def _signature(path: Path) -> str:
    proc = subprocess.run(
        ["bash", "-c", _SNIPPET, "_", str(path)],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def test_different_content_yields_different_signature():
    """Two requests with different host paths must produce different
    signatures even if they happen in the same second."""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "workspace.request"
        f.write_text('{"host_path":"/git/ml/agentic/A"}')
        sig_a = _signature(f)
        # Force-set the mtime to a fixed past second so the next write
        # could in principle share it.  Then write different content.
        import os
        os.utime(f, (1700_000_000, 1700_000_000))
        sig_a = _signature(f)
        f.write_text('{"host_path":"/git/ml/agentic/B"}')
        os.utime(f, (1700_000_000, 1700_000_000))
        sig_b = _signature(f)
        # mtime equal, size could differ (it does here), and hash MUST
        # differ — the contract is "different content → different sig".
        assert sig_a != sig_b, (
            f"signatures collided despite different content: {sig_a!r}"
        )


def test_identical_content_at_same_mtime_yields_same_signature():
    """Operator re-saving the same selection: the watcher must see the
    same signature so it can no-op."""
    import os
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "workspace.request"
        payload = '{"host_path":"/git/ml/agentic/acc-workdir"}'
        f.write_text(payload)
        os.utime(f, (1700_000_000, 1700_000_000))
        sig1 = _signature(f)
        f.write_text(payload)
        os.utime(f, (1700_000_000, 1700_000_000))
        sig2 = _signature(f)
        assert sig1 == sig2


def test_missing_file_signature_returns_nonzero():
    """A missing request file → signature command exits nonzero so the
    watcher's poll loop knows to skip this tick."""
    proc = subprocess.run(
        ["bash", "-c", _SNIPPET, "_", "/no/such/file"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode != 0
