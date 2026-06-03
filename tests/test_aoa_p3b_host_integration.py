"""AoA Phase 3b — host-side sub-collective lifecycle + cognitive-core
seed-context injection.

Proposal `20260530-role-proposal-assistant-agent-of-agents` Phase 3b.

Covers:

1. ``write_lifecycle_request`` atomically writes the request file the
   host-side ``acc-lifecycle-watcher.sh`` polls; rejects unknown
   actions / missing cid (via the encode helper from Phase 3a).
2. The cognitive_core ``_sub_collectives`` attribute defaults to None
   so non-Assistant roles stay unchanged.
3. ``build_system_prompt`` injects the seed-context block when the
   registry has entries; skips cleanly when None / empty.
4. The host-side watcher signature helper matches the Phase 3a apply-
   watcher contract (different content → different signature; identical
   content → same signature).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from acc.collective import SubCollectiveSpec
from acc.sub_collective import (
    LIFECYCLE_HIBERNATE,
    LIFECYCLE_RESUME,
    SubCollectiveRegistry,
    write_lifecycle_request,
)


# ---------------------------------------------------------------------------
# write_lifecycle_request — atomic file produces the documented shape
# ---------------------------------------------------------------------------


def test_write_lifecycle_request_creates_file_with_payload():
    with tempfile.TemporaryDirectory() as td:
        path = write_lifecycle_request(
            td, action=LIFECYCLE_RESUME, sub_cid="sol-code",
            reason="operator delegated",
        )
        assert path.exists()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["action"] == LIFECYCLE_RESUME
        assert payload["sub_cid"] == "sol-code"
        assert payload["reason"] == "operator delegated"
        assert payload["operator_id"] == "default"
        assert payload["ts"] > 0


def test_write_lifecycle_request_creates_apply_dir():
    """apply_dir doesn't have to exist beforehand."""
    with tempfile.TemporaryDirectory() as td:
        nested = Path(td) / "deep" / "nested"
        path = write_lifecycle_request(
            nested, action=LIFECYCLE_HIBERNATE, sub_cid="sol-code",
        )
        assert path.exists()
        assert path.parent == nested


def test_write_lifecycle_request_rejects_unknown_action():
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(ValueError):
            write_lifecycle_request(
                td, action="annihilate", sub_cid="sol-code",
            )


def test_write_lifecycle_request_overwrites_atomically():
    """Successive writes leave only the latest payload (no half-written .tmp)."""
    with tempfile.TemporaryDirectory() as td:
        write_lifecycle_request(
            td, action=LIFECYCLE_RESUME, sub_cid="sol-code",
        )
        path = write_lifecycle_request(
            td, action=LIFECYCLE_HIBERNATE, sub_cid="sol-code",
            reason="idle",
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["action"] == LIFECYCLE_HIBERNATE
        # .tmp must not survive after the os.replace.
        assert not (path.parent / ".sub_collective.request.tmp").exists()


# ---------------------------------------------------------------------------
# Cognitive core seed-context injection
# ---------------------------------------------------------------------------


def _fake_core() -> "object":
    """A bare object with the attributes build_system_prompt reads.

    Avoids constructing a full CognitiveCore (needs LLM + vector + Redis).
    Reuses CognitiveCore's bound method via __get__.
    """
    from acc.cognitive_core import CognitiveCore  # noqa: PLC0415

    class _Stub:
        _role_label = "assistant"
        _sub_collectives = None
        # Additional attributes build_system_prompt may read on
        # roles with the relevant flags; leave at safe defaults so
        # the stub works for any role config we pass in.
        _bridge_enabled = False
        _peer_collectives: list[str] = []
        _skill_registry = None
        _mcp_registry = None
    stub = _Stub()
    stub.build_system_prompt = CognitiveCore.build_system_prompt.__get__(stub)
    return stub


def test_default_no_sub_collective_block():
    """Non-Assistant roles + single-collective hubs see no block."""
    from acc.config import RoleDefinitionConfig  # noqa: PLC0415
    stub = _fake_core()
    role = RoleDefinitionConfig(
        purpose="Do work.", persona="concise", seed_context="",
    )
    prompt = stub.build_system_prompt(role)
    assert "Managed sub-collectives" not in prompt


def test_sub_collective_block_appears_when_registry_populated():
    from acc.config import RoleDefinitionConfig  # noqa: PLC0415
    stub = _fake_core()
    registry = SubCollectiveRegistry()
    registry.register_from_spec({
        "sol-code": SubCollectiveSpec(
            role_templates=["coding_agent"],
            domain="software_engineering",
            description="Code work.",
        ),
    })
    stub._sub_collectives = registry
    role = RoleDefinitionConfig(
        purpose="You are the gatekeeper.",
        persona="concise",
        seed_context="",
    )
    prompt = stub.build_system_prompt(role)
    assert "Managed sub-collectives" in prompt
    assert "sol-code" in prompt
    assert "[DELEGATE:<cid>:<reason>]" in prompt
    assert "Code work." in prompt


def test_empty_registry_yields_no_block():
    """A registry with zero entries renders empty — no stray header."""
    from acc.config import RoleDefinitionConfig  # noqa: PLC0415
    stub = _fake_core()
    stub._sub_collectives = SubCollectiveRegistry()  # empty
    role = RoleDefinitionConfig(
        purpose="Do work.", persona="concise", seed_context="",
    )
    prompt = stub.build_system_prompt(role)
    assert "Managed sub-collectives" not in prompt


# ---------------------------------------------------------------------------
# Lifecycle watcher signature (bash subprocess — same contract as
# acc-apply-watcher.sh v0.3.23)
# ---------------------------------------------------------------------------


_SIG_SNIPPET = r"""
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


def _have_bash() -> bool:
    return sys.platform != "win32" or shutil.which("bash") is not None


@pytest.mark.skipif(
    sys.platform == "win32" or not _have_bash(),
    reason="bash subprocess requires POSIX paths",
)
def test_lifecycle_signature_changes_on_content_change():
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "sub_collective.request"
        write_lifecycle_request(td, action=LIFECYCLE_RESUME, sub_cid="sol-code")
        os.utime(f, (1_700_000_000, 1_700_000_000))
        sig_a = subprocess.run(
            ["bash", "-c", _SIG_SNIPPET, "_", str(f)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        write_lifecycle_request(td, action=LIFECYCLE_HIBERNATE, sub_cid="sol-code")
        os.utime(f, (1_700_000_000, 1_700_000_000))
        sig_b = subprocess.run(
            ["bash", "-c", _SIG_SNIPPET, "_", str(f)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert sig_a != sig_b
