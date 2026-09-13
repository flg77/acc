"""MC-02 — the prompt names a server's tools, not just the server.

An agent was told the marker syntax (``[MCP: <server_id>.<tool_name> {...}]``)
and the server id, and never the tool names, so it invented them: measured
against midojo's weather suite on 2026-09-12, the model called ``get_current``
on a server whose tool is ``get_weather``, A-018 refused the invention, and 13
of 15 evaluation cells scored N/A because no tool ever ran.

The manifest already knows: ``allowed_tools`` is what A-018 enforces, and a
manifest may now also describe each tool.  These tests hold the three sources
(declared tools, the allowlist, neither), the cap that keeps a large server out
of the whole context window, and the rule that matters most -- a manifest can
never advertise a call its own lists would block.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from acc.mcp.manifest import (
    MAX_ADVERTISED_TOOLS,
    MCPManifest,
    advertised_tool_lines,
)


def _manifest(**kw) -> MCPManifest:
    base = dict(server_id="midojo_weather", purpose="Weather for end users.",
                url="http://127.0.0.1:8082/")
    base.update(kw)
    return MCPManifest(**base)


# ---------------------------------------------------------------------------
# the three sources
# ---------------------------------------------------------------------------


def test_declared_tools_render_with_their_arguments_and_summary():
    m = _manifest(
        allowed_tools=["get_weather", "send_weather_alert"],
        tools=[
            {"name": "get_weather", "summary": "current weather for one city",
             "args": ["city"]},
            {"name": "send_weather_alert", "summary": "send an alert",
             "args": ["city", "message"]},
        ],
    )
    lines = advertised_tool_lines(m)
    assert lines == [
        'get_weather {"city": ...} -- current weather for one city',
        'send_weather_alert {"city": ..., "message": ...} -- send an alert',
    ]


def test_a_tool_without_a_summary_still_names_itself():
    m = _manifest(allowed_tools=["ping"], tools=[{"name": "ping"}])
    assert advertised_tool_lines(m) == ["ping {}"]


def test_the_allowlist_is_used_when_no_tools_are_declared():
    """The free win: most manifests already write their allowlist down."""
    m = _manifest(allowed_tools=["search_repositories", "search_issues"])
    assert advertised_tool_lines(m) == ["search_repositories", "search_issues"]


def test_a_manifest_that_declares_neither_advertises_nothing():
    """Exactly the behaviour every manifest had before MC-02."""
    assert advertised_tool_lines(_manifest()) == []


# ---------------------------------------------------------------------------
# the cap
# ---------------------------------------------------------------------------


def test_a_large_server_is_capped_and_says_how_many_it_kept_back():
    names = [f"tool_{i}" for i in range(MAX_ADVERTISED_TOOLS + 5)]
    lines = advertised_tool_lines(_manifest(allowed_tools=names))
    assert len(lines) == MAX_ADVERTISED_TOOLS + 1
    assert lines[-1] == "... and 5 more"


def test_the_cap_applies_to_declared_tools_too():
    tools = [{"name": f"tool_{i}"} for i in range(MAX_ADVERTISED_TOOLS + 2)]
    lines = advertised_tool_lines(_manifest(tools=tools))
    assert lines[-1] == "... and 2 more"


def test_the_cap_is_caller_adjustable():
    m = _manifest(allowed_tools=["a", "b", "c"])
    assert advertised_tool_lines(m, limit=2) == ["a", "b", "... and 1 more"]


# ---------------------------------------------------------------------------
# a manifest may not advertise what it would block
# ---------------------------------------------------------------------------


def test_a_tool_outside_the_allowlist_is_refused():
    with pytest.raises(ValidationError, match="refused by allowed_tools"):
        _manifest(allowed_tools=["get_weather"], tools=[{"name": "drop_db"}])


def test_a_denied_tool_cannot_be_advertised():
    with pytest.raises(ValidationError, match="refused by allowed_tools"):
        _manifest(denied_tools=["drop_db"], tools=[{"name": "drop_db"}])


def test_a_tool_declared_twice_is_refused():
    with pytest.raises(ValidationError, match="declared twice"):
        _manifest(tools=[{"name": "echo"}, {"name": "echo"}])


def test_everything_advertised_would_actually_be_permitted():
    """The property that matters: the prompt never teaches a blocked call."""
    m = _manifest(
        allowed_tools=["get_weather", "list_cities"],
        tools=[{"name": "get_weather", "args": ["city"]},
               {"name": "list_cities"}],
    )
    for line in advertised_tool_lines(m):
        name = line.split(" ")[0].split("{")[0].strip()
        assert m.is_tool_allowed(name), f"advertised a blocked tool: {name}"


# ---------------------------------------------------------------------------
# the prompt itself
# ---------------------------------------------------------------------------


def _core_with(registry):
    from acc.cognitive_core import CognitiveCore

    class _Stub:
        _role_label = "as04_agent"
        _sub_collectives = None
        _bridge_enabled = False
        _peer_collectives: list[str] = []
        _skill_registry = None
        _mcp_registry = registry
    stub = _Stub()
    stub.build_system_prompt = CognitiveCore.build_system_prompt.__get__(stub)
    return stub


class _Registry:
    def __init__(self, *manifests: MCPManifest) -> None:
        self._by_id = {m.server_id: m for m in manifests}

    def manifest(self, server_id: str):
        return self._by_id.get(server_id)


def _role(**kw):
    from acc.config import RoleDefinitionConfig
    base = dict(purpose="Answer weather questions.", persona="concise",
                seed_context="", allowed_mcps=["midojo_weather"],
                default_mcps=["midojo_weather"])
    base.update(kw)
    return RoleDefinitionConfig(**base)


def test_the_prompt_names_the_tools_under_their_server():
    m = _manifest(
        allowed_tools=["get_weather"],
        tools=[{"name": "get_weather", "summary": "current weather for one city",
                "args": ["city"]}],
    )
    prompt = _core_with(_Registry(m)).build_system_prompt(_role())
    assert "- midojo_weather: Weather for end users." in prompt
    assert 'get_weather {"city": ...} -- current weather for one city' in prompt
    server_at = prompt.index("- midojo_weather")
    assert server_at < prompt.index("get_weather {"), "tools belong under their server"


def test_a_server_with_nothing_declared_leaves_the_prompt_as_it_was():
    prompt = _core_with(_Registry(_manifest())).build_system_prompt(_role())
    assert "- midojo_weather: Weather for end users." in prompt
    lines = [ln for ln in prompt.splitlines() if ln.startswith("      ")]
    assert lines == [], "nothing should be advertised for a silent manifest"


def test_a_server_the_registry_does_not_know_still_renders():
    prompt = _core_with(_Registry()).build_system_prompt(_role())
    assert "- midojo_weather" in prompt


def test_the_mcp_block_does_not_lean_on_the_skills_block():
    """The tool rendering belongs to the MCP block, not the skills one.

    Caught in review: the import that renders tool lines was first placed
    inside the ``default_skills`` branch.  It never actually broke, because
    every role is granted ``okf`` and so the skills block always runs -- which
    is precisely why it was worth moving: the MCP block would have depended on
    an unrelated branch continuing to be non-empty.
    """
    m = _manifest(allowed_tools=["get_weather"],
                  tools=[{"name": "get_weather", "args": ["city"]}])
    prompt = _core_with(_Registry(m)).build_system_prompt(_role())
    mcp_at = prompt.index("Available MCP servers")
    assert prompt.index('get_weather {"city": ...}') > mcp_at, (
        "the tools must render inside the MCP block"
    )
