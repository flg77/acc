"""Tests for OpenSpec `20260603-capability-pool` Phase 1.3 — six new
stdio MCP manifests load + pass schema validation.  No live HTTP / no
subprocess spawn.
"""

from __future__ import annotations

import pytest

from acc.mcp.registry import MCPRegistry


@pytest.fixture(scope="module")
def reg() -> MCPRegistry:
    r = MCPRegistry()
    r.load_from("mcps")
    return r


PHASE_1_MCPS = [
    ("arxiv", "stdio", "LOW", "research"),
    ("wikipedia", "stdio", "LOW", "research"),
    ("semantic_scholar", "stdio", "LOW", "research"),
    ("github_api", "stdio", "MEDIUM", "software_engineering"),
    ("web_archive", "stdio", "LOW", "research"),
    ("rss_fetch", "stdio", "LOW", "research"),
]


class TestNewMcpsRegistered:
    @pytest.mark.parametrize("server_id,transport,risk,domain", PHASE_1_MCPS)
    def test_manifest_present(
        self, reg: MCPRegistry, server_id: str, transport: str,
        risk: str, domain: str,
    ) -> None:
        m = reg.manifests().get(server_id)
        assert m is not None, f"missing MCP: {server_id}"
        assert m.transport == transport
        assert m.risk_level == risk
        assert m.domain_id == domain

    def test_github_api_locked_to_readshape(self, reg: MCPRegistry) -> None:
        m = reg.manifests()["github_api"]
        # Read-shape tools allowed; write-shape tools denied.
        assert "get_file_contents" in m.allowed_tools
        assert "create_or_update_file" in m.denied_tools
        # MEDIUM-risk MCPs require explicit role action.
        assert "use_external_api" in m.requires_actions

    def test_stdio_command_set(self, reg: MCPRegistry) -> None:
        for server_id, *_ in PHASE_1_MCPS:
            m = reg.manifests()[server_id]
            assert m.command, f"{server_id}: stdio command must be set"
            # First arg is the launcher (uvx / npx).
            assert m.command[0] in {"uvx", "npx"}, m.command[0]


class TestExistingMcpsUntouched:
    def test_echo_server_still_present(self, reg: MCPRegistry) -> None:
        assert reg.manifests().get("echo_server") is not None

    def test_web_browser_harness_still_high_risk(self, reg: MCPRegistry) -> None:
        m = reg.manifests().get("web_browser_harness")
        assert m is not None
        assert m.risk_level == "HIGH"
