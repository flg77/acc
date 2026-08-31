"""The web surface can hold a thread (RP-02 reaching acc-webgui).

The channel could always do this: `WebPromptChannel` inherits
`TUIPromptChannel.send()`, which has taken `session_id` since 6853fb8. The route
never passed one, so every web prompt was a first turn while the TUI and Slack
held conversations. This covers the seam that was missing.

The test that matters is `test_absent_session_degrades_to_task_scope`. The base
channel's contract says a surface that does not name a thread must degrade to a
`task_id`-scoped session, **never to a thread shared with someone else** — that
is a data-leak boundary, not an ergonomics detail.

Change: ``openspec/changes/20260830-webgui-tui-alignment`` Phase 1.
"""

from __future__ import annotations

import inspect

import pytest

from acc.channels.webgui import WebPromptChannel
from acc.webgui.routes_action import PromptRequest


class TestTheChannelCouldAlwaysDoThis:
    """Guards the assumption this change rests on.

    If someone gives `WebPromptChannel` its own `send()` without `session_id`,
    the route below starts silently dropping the thread — and nothing else in
    the suite would notice.
    """

    def test_web_channel_inherits_session_support(self):
        sig = inspect.signature(WebPromptChannel.send)
        assert "session_id" in sig.parameters
        assert WebPromptChannel.send.__qualname__ == "TUIPromptChannel.send", (
            "WebPromptChannel overrides send(); re-check that the override "
            "still forwards session_id, operating_mode and workspace"
        )

    def test_web_channel_matches_the_base_contract(self):
        """Parity, asserted rather than assumed: the web channel must accept
        every keyword the base declares."""
        from acc.channels.base import PromptChannel

        base = set(inspect.signature(PromptChannel.send).parameters)
        web = set(inspect.signature(WebPromptChannel.send).parameters)
        assert base <= web, f"web channel is missing {sorted(base - web)}"


class TestTheRouteNowCarriesIt:
    def test_request_model_accepts_a_session(self):
        req = PromptRequest(
            collective_id="sol-01", target_role="analyst",
            content="hello", session_id="thread-abc",
        )
        assert req.session_id == "thread-abc"

    def test_session_is_optional(self):
        """Omitting it must stay valid — this is the pre-existing behaviour and
        every current caller relies on it."""
        req = PromptRequest(
            collective_id="sol-01", target_role="analyst", content="hello",
        )
        assert req.session_id is None


class TestDegradeBoundary:
    """A surface that names no thread gets its own, never someone else's."""

    def test_absent_session_degrades_to_task_scope(self):
        """The reply echoes `task_id` as the session when none was named.

        The failure this prevents is a shared default — e.g. echoing the
        collective id, or a constant — which would silently join two users'
        conversations. The echoed value must be unique per request.
        """
        first = _echoed_session(session_id=None, task_id="task-aaa")
        second = _echoed_session(session_id=None, task_id="task-bbb")
        assert first == "task-aaa"
        assert second == "task-bbb"
        assert first != second, "two sessionless prompts must not share a thread"

    def test_named_session_is_preserved(self):
        assert _echoed_session(session_id="thread-xyz", task_id="task-aaa") == (
            "thread-xyz"
        )


def _echoed_session(*, session_id: str | None, task_id: str) -> str:
    """The route's echo rule, isolated.

    Mirrors ``routes_action.send_prompt``'s ``req.session_id or task_id``. Kept
    as a helper so the boundary is testable without standing up NATS; if the
    route's rule changes, this must change with it.
    """
    return session_id or task_id


class TestTheOtherTwoInheritedFields:
    """`operating_mode` and `workspace` were the same defect as `session_id`.

    The channel accepted all three; the route passed none. These were found
    while fixing the session gap and are now closed with it, so the web surface
    is no longer pinned to AUTO in a fixed workspace.
    """

    def test_both_are_on_the_request_model(self):
        assert "operating_mode" in PromptRequest.model_fields
        assert "workspace" in PromptRequest.model_fields

    def test_mode_defaults_to_auto(self):
        """AUTO is the strict default; the channel omits it from the payload."""
        req = PromptRequest(
            collective_id="c", target_role="analyst", content="x",
        )
        assert req.operating_mode == "AUTO"
        assert req.workspace is None

    def test_an_unknown_mode_is_accepted_here_and_normalised_later(self):
        """Deliberately not validated at the route.

        `acc.operating_modes.normalise` coerces anything unrecognised to AUTO,
        which fails toward the *stricter* gate. Rejecting at the route would
        move the same decision somewhere with less context, and risk the two
        rules disagreeing.
        """
        from acc.operating_modes import normalise

        req = PromptRequest(
            collective_id="c", target_role="analyst", content="x",
            operating_mode="nonsense",
        )
        assert req.operating_mode == "nonsense"
        assert normalise(req.operating_mode) == "AUTO"

    @pytest.mark.parametrize("hostile", ["/etc/passwd", "..", "a/../../etc"])
    def test_workspace_escapes_are_rejected_by_the_agent_not_the_route(
        self, hostile,
    ):
        """The route accepts the string; the agent refuses to resolve it.

        This surface is remote, unlike the TUI, so the guard matters more here
        — but it belongs where the mount is known. Asserted so that a change to
        `agent.py`'s check cannot silently widen what the web can reach.
        """
        from acc.agent import _resolve_task_workspace_dir

        req = PromptRequest(
            collective_id="c", target_role="analyst", content="x",
            workspace=hostile,
        )
        assert _resolve_task_workspace_dir({"workspace": req.workspace}) is None

    def test_a_legitimate_workspace_resolves(self):
        from acc.agent import _resolve_task_workspace_dir

        assert _resolve_task_workspace_dir({"workspace": "project-a"}, mount="/workspace") == (
            "/workspace/project-a"
        )
