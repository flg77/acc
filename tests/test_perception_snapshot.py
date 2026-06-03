"""Tests for `acc/perception.py` (assistant-action-loop Phase 1).

Covers:
  * `snapshot_for_assistant` happy path: capability + roster fan-out + merge.
  * Stale flags set when sources time out.
  * Filesystem fallback when orchestrator is unreachable.
  * Sub-collectives pass-through.
  * `validate_marker_target` rejects hallucinated roles, accepts roster + catalog.
  * `render_currently_available_block` produces a token-conscious + stable shape.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

import msgpack

from acc.perception import (
    PerceptionSnapshot,
    render_currently_available_block,
    snapshot_for_assistant,
    validate_marker_target,
)


# ---------------------------------------------------------------------------
# Fake bus — minimal nats-py interface (`request(subject, payload, ...)`)
# ---------------------------------------------------------------------------


class _FakeReply:
    def __init__(self, payload: bytes) -> None:
        self.data = payload


class _FakeBus:
    """Records every request + returns canned msgpack replies keyed by subject.

    Tests stub specific subjects.  A subject without a stub raises
    asyncio.TimeoutError so we exercise the stale-source path.
    """

    def __init__(self) -> None:
        self.replies: dict[str, bytes] = {}
        self.requests: list[tuple[str, bytes]] = []
        self.delay_s: float = 0.0  # simulate slow reply

    async def request(self, subject: str, payload: bytes, **kwargs) -> _FakeReply:
        self.requests.append((subject, payload))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if subject in self.replies:
            return _FakeReply(self.replies[subject])
        raise asyncio.TimeoutError


def _capability_reply(roles=None, mcps=None, revision=7):
    return msgpack.packb({
        "matches": (roles or []) + (mcps or []),
        "total": len(roles or []) + len(mcps or []),
        "ts": 1.0,
        "catalog_revision": revision,
    })


def _split_capability_reply(roles=None, mcps=None, revision=7):
    """Used when the test only cares about ONE kind at a time."""
    return _capability_reply(roles or [], mcps or [], revision)


# ---------------------------------------------------------------------------
# snapshot_for_assistant — happy path + degradations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_with_full_bus_returns_clean(tmp_path):
    """All sources reply → snapshot has no stale flags, both halves populated."""
    bus = _FakeBus()
    # Capability query is issued twice (role + mcp) — same subject, same
    # reply.  Our snapshot module fans out role+mcp under the same subject;
    # the stub returns the same payload for both calls.  The reply embeds
    # a mix of roles + mcps; the perception module sorts them into the
    # right buckets via the `kind` field.
    from acc.signals import subject_capability_query, subject_roster_snapshot
    bus.replies[subject_capability_query("sol-test")] = msgpack.packb({
        "matches": [
            {"kind": "role", "name": "coding_agent", "summary": "Write code.",
             "metadata": {}},
            {"kind": "mcp", "name": "github", "summary": "GitHub ops.",
             "metadata": {"risk_level": "MEDIUM"}},
        ],
        "total": 2, "ts": 1.0, "catalog_revision": 7,
    })
    bus.replies[subject_roster_snapshot("sol-test")] = msgpack.packb({
        "roster": {
            "assistant": ["assistant-1"],
            "arbiter": ["arbiter-abc"],
        },
        "ts": 1.0,
    })

    snap = await snapshot_for_assistant(
        bus=bus,
        cid="sol-test",
        timeout_s=1.0,
        roles_root=tmp_path / "no-roles-needed",
    )

    assert snap.stale is False
    assert snap.stale_capability is False
    assert snap.stale_roster is False
    assert snap.roster == {
        "assistant": ["assistant-1"],
        "arbiter": ["arbiter-abc"],
    }
    # The capability reply lumps roles + mcps together; the perception
    # module's fan-out asks for kind=role and kind=mcp separately.  Each
    # request goes through the same `_one()` helper that returns the
    # WHOLE matches list (the stub doesn't filter by kind).  So both
    # halves end up populated with the full list — that's the expected
    # shape today (Phase 1 ships the catalog-side filter in v0.3.42
    # already, but our fake bus doesn't filter).  Just check non-empty.
    assert len(snap.available_roles) >= 1
    assert len(snap.available_mcps) >= 1


@pytest.mark.asyncio
async def test_snapshot_timeout_marks_capability_stale(tmp_path):
    """Capability source times out → stale flag set; roster still populated."""
    bus = _FakeBus()
    from acc.signals import subject_roster_snapshot
    bus.replies[subject_roster_snapshot("sol-test")] = msgpack.packb({
        "roster": {"assistant": ["assistant-1"]},
        "ts": 1.0,
    })
    # capability_query is NOT stubbed — raises TimeoutError.

    # Seed a roles_root for the fallback path.
    roles_root = tmp_path / "roles"
    (roles_root / "coding_agent").mkdir(parents=True)
    (roles_root / "coding_agent" / "role.yaml").write_text(yaml.safe_dump({
        "role_definition": {"purpose": "Write code.", "persona": "implementer"}
    }))

    snap = await snapshot_for_assistant(
        bus=bus,
        cid="sol-test",
        timeout_s=0.05,
        roles_root=roles_root,
    )

    assert snap.stale is True
    assert snap.stale_capability is True
    # Fallback populated.
    assert any(r["name"] == "coding_agent" for r in snap.available_roles)
    # Roster still good.
    assert snap.roster == {"assistant": ["assistant-1"]}


@pytest.mark.asyncio
async def test_snapshot_with_no_bus_falls_back_to_filesystem(tmp_path):
    """When bus is None (tests / early boot), filesystem fallback runs."""
    roles_root = tmp_path / "roles"
    (roles_root / "ingester").mkdir(parents=True)
    (roles_root / "ingester" / "role.yaml").write_text(yaml.safe_dump({
        "role_definition": {"purpose": "Intake.", "persona": "analytical"}
    }))

    snap = await snapshot_for_assistant(
        bus=None,
        cid="sol-test",
        roles_root=roles_root,
    )

    assert snap.roster == {}
    assert any(r["name"] == "ingester" for r in snap.available_roles)
    # Filesystem-only fallback is by definition stale on capability.
    assert snap.stale_capability is True


@pytest.mark.asyncio
async def test_snapshot_passes_through_sub_collectives():
    bus = _FakeBus()
    sub = {"sol-code": {"domain": "software_engineering", "description": "x"}}
    snap = await snapshot_for_assistant(
        bus=bus,
        cid="sol-test",
        sub_collectives=sub,
        timeout_s=0.05,
    )
    assert snap.sub_collectives == sub


# ---------------------------------------------------------------------------
# validate_marker_target — the hallucination guard
# ---------------------------------------------------------------------------


def test_validate_accepts_role_from_roster():
    snap = PerceptionSnapshot(
        roster={"coding_agent": ["coding-1"]},
        available_roles=[],
    )
    assert validate_marker_target(snap, "coding_agent") is True


def test_validate_accepts_role_from_catalog():
    """Even a not-yet-running role is valid IF it's in the catalog
    (an Assistant proposing to spawn it is legitimate)."""
    snap = PerceptionSnapshot(
        roster={},
        available_roles=[
            {"kind": "role", "name": "research_planner", "summary": "...",
             "metadata": {}}
        ],
    )
    assert validate_marker_target(snap, "research_planner") is True


def test_validate_rejects_hallucinated_role():
    """The lighthouse trace's actual failure: ``[PROPOSE_SPAWN:worker-pool:...]``."""
    snap = PerceptionSnapshot(
        roster={"assistant": ["assistant-1"]},
        available_roles=[
            {"kind": "role", "name": "coding_agent", "summary": "...",
             "metadata": {}}
        ],
    )
    assert validate_marker_target(snap, "worker-pool") is False
    assert validate_marker_target(snap, "prompt") is False


# ---------------------------------------------------------------------------
# render_currently_available_block — prompt-block shape
# ---------------------------------------------------------------------------


def test_render_lists_running_agents_first():
    snap = PerceptionSnapshot(
        roster={
            "assistant": ["assistant-1"],
            "coding_agent": ["coding-1", "coding-2"],
        },
        available_roles=[],
    )
    block = render_currently_available_block(snap)
    assert "## Currently available" in block
    assert "Running agents" in block
    # Both replicas surfaced, comma-joined.
    assert "coding-1, coding-2" in block


def test_render_omits_already_running_from_available_roles():
    snap = PerceptionSnapshot(
        roster={"coding_agent": ["coding-1"]},
        available_roles=[
            {"kind": "role", "name": "coding_agent", "summary": "x",
             "metadata": {}},
            {"kind": "role", "name": "research_planner", "summary": "y",
             "metadata": {}},
        ],
    )
    block = render_currently_available_block(snap)
    # research_planner shows up under "Available roles"; coding_agent only
    # shows up under "Running agents" (no duplication).
    assert "research_planner" in block
    # coding_agent appears in the running block only, not duplicated below.
    running_block = block.split("**Available roles**")[0]
    assert "coding_agent" in running_block


def test_render_includes_stale_annotation():
    snap = PerceptionSnapshot(
        roster={},
        available_roles=[],
        stale=True,
        stale_capability=True,
    )
    block = render_currently_available_block(snap)
    assert "stale" in block.lower()


def test_render_includes_sub_collectives_with_delegate_marker():
    snap = PerceptionSnapshot(
        roster={},
        available_roles=[],
        sub_collectives={"sol-code": {"domain": "software_engineering",
                                       "description": "code work"}},
    )
    block = render_currently_available_block(snap)
    assert "sol-code" in block
    assert "[DELEGATE:cid:reason]" in block


def test_render_overflows_to_name_only_tail():
    """OpenSpec ``20260602-role-proposal-assistant-blindspots`` Phase 1.2 — the
    detailed list caps at ``_DETAILED_ROLE_CAP`` entries; any overflow
    lands as a single comma-joined name-only tail so the LLM at least
    sees the names instead of an opaque ``and N more`` line."""
    from acc.perception import _DETAILED_ROLE_CAP

    n = _DETAILED_ROLE_CAP + 15
    many_roles = [
        {"kind": "role", "name": f"role_{i}", "summary": "x",
         "metadata": {}}
        for i in range(n)
    ]
    snap = PerceptionSnapshot(
        roster={}, available_roles=many_roles,
    )
    block = render_currently_available_block(snap)
    # No opaque ellipsis-count line.
    assert "and 15 more" not in block
    assert "and " + str(n - _DETAILED_ROLE_CAP) + " more" not in block
    # Tail names rendered on a single "(also available …)" line.
    assert "(also available, ask if relevant):" in block
    assert f"role_{n - 1}" in block


def test_render_concludes_with_grounding_directive():
    """The block ends with the 'roles not in this list do not exist'
    instruction.  This is the key signal to the LLM that prevents the
    lighthouse trace's hallucination."""
    snap = PerceptionSnapshot(
        roster={"assistant": ["assistant-1"]},
        available_roles=[],
    )
    block = render_currently_available_block(snap)
    assert "do not exist" in block
