"""AoA Phase 6 — role.yaml policy block + harness threading.

Proposal `20260530-role-proposal-assistant-agent-of-agents` Phase 6.

Pins:

1. `RoleDefinitionConfig` carries the four new policy fields with
   documented defaults that preserve today's behaviour
   (`policy_enabled=False` → harness pins everything → no θ updates).
2. The Assistant role.yaml ships `policy_enabled: true` and pins
   every knob *except* `route_confidence_threshold` — the one
   operator-visible knob we start learning first.
3. RewardHarness honours per-role pin / cadence / drift_cap when
   constructed with them (regression that SIP-P2's surface lands
   the values).
4. AUTO-mode frozen contract: `observe_task` is a no-op under AUTO
   no matter the role-supplied cadence.
5. Mode hint suffix: the Prompt screen's mode hint includes the
   "💤 policy frozen" badge only in AUTO.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from acc.config import RoleDefinitionConfig
from acc.policy_layer import (
    DEFAULT_POLICY_VECTOR,
    REWARD_EVAL_OUTCOME,
    RewardHarness,
)


# ---------------------------------------------------------------------------
# RoleDefinitionConfig defaults
# ---------------------------------------------------------------------------


def test_role_definition_defaults_preserve_no_learning():
    r = RoleDefinitionConfig()
    assert r.policy_enabled is False, (
        "Default must be opt-out so existing roles keep SIP-P1 behaviour"
    )
    assert r.policy_pinned == []
    assert r.policy_update_every_n_tasks == 100
    assert r.policy_drift_cap == 0.8


def test_role_definition_accepts_partial_pin_list():
    r = RoleDefinitionConfig(
        policy_enabled=True,
        policy_pinned=["spawn_threshold", "memory_top_k"],
        policy_update_every_n_tasks=50,
        policy_drift_cap=0.6,
    )
    assert r.policy_enabled is True
    assert r.policy_pinned == ["spawn_threshold", "memory_top_k"]
    assert r.policy_update_every_n_tasks == 50
    assert r.policy_drift_cap == 0.6


# ---------------------------------------------------------------------------
# Assistant role.yaml ships the gatekeeper learning contract
# ---------------------------------------------------------------------------


def _load_assistant_role_yaml() -> dict:
    path = Path(__file__).resolve().parent.parent / "roles" / "assistant" / "role.yaml"
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_assistant_role_yaml_opts_into_policy_learning():
    raw = _load_assistant_role_yaml()
    rd = raw["role_definition"]
    assert rd["policy_enabled"] is True
    # Bootstrap pins everything EXCEPT route_confidence_threshold —
    # the most operator-visible knob, learned first.
    pinned = set(rd["policy_pinned"])
    all_knobs = set(DEFAULT_POLICY_VECTOR.keys())
    assert "route_confidence_threshold" not in pinned
    assert pinned == all_knobs - {"route_confidence_threshold"}, (
        "Phase 6 bootstrap: only route_confidence_threshold is "
        f"unpinned; got {pinned}"
    )


def test_assistant_role_yaml_carries_cadence_and_drift_cap():
    raw = _load_assistant_role_yaml()
    rd = raw["role_definition"]
    assert rd["policy_update_every_n_tasks"] == 100
    assert rd["policy_drift_cap"] == 0.8


# ---------------------------------------------------------------------------
# RewardHarness honours role-supplied pin / cadence / cap
# ---------------------------------------------------------------------------


def test_reward_harness_accepts_role_supplied_config():
    h = RewardHarness(
        MagicMock(), "sol-01", role="assistant",
        pinned=frozenset({"spawn_threshold", "memory_top_k"}),
        update_every=50,
        drift_cap=0.6,
    )
    snap = h.snapshot()
    assert snap["alpha"] >= 0.0  # sanity
    # Pinned set surfaces in the snapshot (SIP-P2 surface).
    assert set(snap.get("pinned", set())) == {"spawn_threshold", "memory_top_k"}
    assert snap.get("update_every") == 50
    assert snap.get("drift_cap") == 0.6


def test_observe_task_is_noop_under_auto():
    """Frozen-in-AUTO contract (rail 6): no θ update even when the
    window has been exceeded."""
    h = RewardHarness(
        MagicMock(), "sol-01", role="assistant",
        pinned=frozenset(),  # all unpinned so updates *could* fire
        update_every=1,       # every task would fire normally
        drift_cap=0.8,
    )
    # Seed a reward so the EWMA exists.
    fake = MagicMock(); fake.data = b'{"score": 0.7}'
    h._record(REWARD_EVAL_OUTCOME, fake)
    fired = asyncio.run(h.observe_task(operating_mode="AUTO", drift=0.2))
    assert fired is False, "AUTO mode must freeze θ updates"


def test_observe_task_fires_under_ask_permissions_when_window_due():
    h = RewardHarness(
        MagicMock(), "sol-01", role="assistant",
        pinned=frozenset(),
        update_every=1,
        drift_cap=0.8,
    )
    fake = MagicMock(); fake.data = b'{"score": 0.7}'
    h._record(REWARD_EVAL_OUTCOME, fake)
    fired = asyncio.run(
        h.observe_task(operating_mode="ASK_PERMISSIONS", drift=0.2),
    )
    assert fired is True


def test_observe_task_respects_update_every_cadence():
    """update_every=3 → first two calls don't fire, third does."""
    h = RewardHarness(
        MagicMock(), "sol-01", role="assistant",
        pinned=frozenset(),
        update_every=3,
        drift_cap=0.8,
    )
    fake = MagicMock(); fake.data = b'{"score": 0.5}'
    h._record(REWARD_EVAL_OUTCOME, fake)

    async def _run() -> list[bool]:
        return [
            await h.observe_task("ACCEPT_EDITS", drift=0.2)
            for _ in range(4)
        ]
    fired = asyncio.run(_run())
    assert fired == [False, False, True, False]
