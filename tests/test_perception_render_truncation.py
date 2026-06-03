"""Tests for OpenSpec `20260602-role-proposal-assistant-blindspots` Phase 1.2 — the
control profile no longer hides catalog entries behind the opaque
"... and N more" line.  Overflow now lands as a single comma-joined
name-only tail line so the LLM at least sees that the roles exist.
"""

from __future__ import annotations

from acc.perception import (
    PerceptionSnapshot,
    _DETAILED_ROLE_CAP,
    _render_control,
)


def _snap(n: int) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        available_roles=[
            {"kind": "role", "name": f"r{i:03d}", "summary": f"role number {i}"}
            for i in range(n)
        ],
    )


class TestNoTruncationCliff:
    def test_under_cap_renders_all_detailed(self) -> None:
        out = _render_control(_snap(10), None)
        for i in range(10):
            assert f"- r{i:03d}: role number {i}" in out
        assert "(also available" not in out

    def test_exactly_at_cap_no_tail(self) -> None:
        out = _render_control(_snap(_DETAILED_ROLE_CAP), None)
        assert f"- r{_DETAILED_ROLE_CAP - 1:03d}" in out
        assert "(also available" not in out

    def test_over_cap_tail_lists_names(self) -> None:
        out = _render_control(_snap(_DETAILED_ROLE_CAP + 5), None)
        tail_lines = [l for l in out.splitlines() if "(also available" in l]
        assert len(tail_lines) == 1, tail_lines
        tail = tail_lines[0]
        for i in range(_DETAILED_ROLE_CAP, _DETAILED_ROLE_CAP + 5):
            assert f"r{i:03d}" in tail

    def test_no_ellipsis_count_line(self) -> None:
        # The v0.3.43 "... and N more" string must NOT appear.
        out = _render_control(_snap(_DETAILED_ROLE_CAP + 20), None)
        assert "more" not in out.lower() or "(also available" in out
        for line in out.splitlines():
            assert not line.strip().startswith("- ..."), line

    def test_large_catalog_still_bounded(self) -> None:
        # 200 roles → 40 detailed + 160 names on one line.
        out = _render_control(_snap(200), None)
        # All 160 tail names appear.
        for i in range(_DETAILED_ROLE_CAP, 200):
            assert f"r{i:03d}" in out
        # Block stays under 8 KB even at this size — sanity bound.
        assert len(out) < 8_000


class TestExistingBehaviourPreserved:
    def test_running_agents_section_intact(self) -> None:
        snap = PerceptionSnapshot(
            roster={"assistant": ["assistant-1"]},
            available_roles=[
                {"kind": "role", "name": "coding_agent", "summary": "code"},
            ],
        )
        out = _render_control(snap, None)
        assert "Running agents" in out
        assert "assistant → assistant-1" in out
        assert "MUST appear above" in out

    def test_sub_collectives_section_intact(self) -> None:
        snap = PerceptionSnapshot(
            sub_collectives={
                "deep-research": {
                    "domain": "research",
                    "description": "research collective",
                }
            }
        )
        out = _render_control(snap, None)
        assert "Managed sub-collectives" in out
        assert "deep-research" in out
        assert "domain=research" in out
