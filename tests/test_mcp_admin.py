"""Host-level MCP administration, composed with governed role declarations.

One property matters more than every feature here: **a host override can only
subtract.** Roles declare which MCP servers they may use and that declaration is
governed — countersigned and audited. If a host toggle could grant a role
something it never declared, ACC would have a second permission path with none
of that, and the ungoverned one is the one nobody audits.

Every way an override could accidentally grant is tested, including the subtle
one: *removing* a host block must not add a tool the manifest or the role
already denies.

The second property is that "unavailable" says which layer said no. A tool the
host disabled and a tool the role never declared look identical from an agent
and need completely different fixes — a local decision versus a governed role
change.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from acc import mcp_admin as A


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(A.OVERRIDES_PATH_VAR, str(tmp_path / "mcp-overrides.yaml"))
    return tmp_path


def manifest(server_id="files", allowed=None, denied=None):
    return SimpleNamespace(
        server_id=server_id,
        allowed_tools=list(allowed or []),
        denied_tools=list(denied or []),
    )


# --------------------------------------------------------------------------
# The property that makes this safe
# --------------------------------------------------------------------------


class TestHostCanOnlySubtract:
    def test_a_host_cannot_grant_a_server_the_role_never_declared(self, store):
        """The core invariant.

        There is no host action that puts a server into a role's allowed set;
        enabling only ever removes a host-level block.
        """
        A.enable_server("secrets-server")
        decision = A.server_decision(
            "secrets-server",
            role_allowed=["files"],          # the role declares only `files`
            overrides=A.load_overrides(),
            known=["files", "secrets-server"],
        )
        assert decision.allowed is False
        assert decision.denied_by == "role"

    def test_removing_a_host_block_does_not_grant_a_manifest_denied_tool(self, store):
        """The subtle one.

        `enable_tool` removes a subtraction. If the manifest denies the tool it
        must stay denied — otherwise "enable" would be a grant by another name.
        """
        A.disable_tool("files", "delete")
        A.enable_tool("files", "delete")
        decision = A.tool_decision(
            "files", "delete",
            manifest=manifest(denied=["delete"]),
            overrides=A.load_overrides(),
        )
        assert decision.allowed is False
        assert decision.denied_by == "manifest"

    def test_enabling_a_tool_outside_the_manifest_allowlist_stays_denied(self, store):
        A.enable_tool("files", "exfiltrate")
        decision = A.tool_decision(
            "files", "exfiltrate",
            manifest=manifest(allowed=["read", "write"]),
            overrides=A.load_overrides(),
        )
        assert decision.allowed is False
        assert decision.denied_by == "manifest"

    def test_adding_a_server_does_not_make_it_usable_by_any_role(self, store):
        """Operator-added still needs a governed role declaration."""
        A.add_server("new-server", url="https://example.invalid")
        decision = A.server_decision(
            "new-server",
            role_allowed=["files"],
            overrides=A.load_overrides(),
            known=["files", "new-server"],
        )
        assert decision.allowed is False
        assert decision.denied_by == "role"

    def test_a_disabled_server_is_blocked_even_when_the_role_allows_it(self, store):
        A.disable_server("files")
        decision = A.server_decision(
            "files", role_allowed=["files"], overrides=A.load_overrides(), known=["files"]
        )
        assert decision.allowed is False
        assert decision.denied_by == "host"


# --------------------------------------------------------------------------
# Which side said no
# --------------------------------------------------------------------------


class TestAttribution:
    def test_host_and_role_denials_are_distinguishable(self, store):
        A.disable_server("host-blocked")
        ov = A.load_overrides()
        known = ["host-blocked", "role-blocked"]

        host = A.server_decision(
            "host-blocked", role_allowed=known, overrides=ov, known=known
        )
        role = A.server_decision(
            "role-blocked", role_allowed=["something-else"], overrides=ov, known=known
        )
        assert (host.denied_by, role.denied_by) == ("host", "role")

    def test_an_unknown_server_is_attributed_to_the_manifest_layer(self, store):
        decision = A.server_decision(
            "ghost", role_allowed=None, overrides=A.load_overrides(), known=["files"]
        )
        assert decision.denied_by == "manifest"

    def test_every_denial_carries_a_reason(self, store):
        A.disable_server("files")
        decision = A.server_decision(
            "files", role_allowed=["files"], overrides=A.load_overrides(), known=["files"]
        )
        assert decision.reason

    def test_effective_tools_reports_both_sides(self, store):
        A.disable_tool("files", "write")
        decisions = A.effective_tools(
            "files", ["read", "write", "delete"],
            manifest=manifest(denied=["delete"]),
            overrides=A.load_overrides(),
        )
        assert decisions["read"].allowed is True
        assert decisions["write"].denied_by == "host"
        assert decisions["delete"].denied_by == "manifest"


# --------------------------------------------------------------------------
# Diagnosis
# --------------------------------------------------------------------------


class TestFailureClassification:
    def test_auth_is_separated_from_a_generic_protocol_error(self):
        """'Your credential was rejected' and 'the server misbehaved' send an
        operator to completely different places."""
        from acc.mcp.errors import MCPProtocolError

        stage, _ = A.classify_failure(MCPProtocolError("HTTP 401 Unauthorized"))
        assert stage == "auth"

    def test_connection_failures_are_connect(self):
        from acc.mcp.errors import MCPConnectionError

        assert A.classify_failure(MCPConnectionError("refused"))[0] == "connect"

    def test_transport_failures_surface_at_the_tools_stage(self):
        from acc.mcp.errors import MCPTransportError

        assert A.classify_failure(MCPTransportError("HTTP 503"))[0] == "tools"

    def test_an_unexpected_error_is_still_classified(self):
        stage, detail = A.classify_failure(ValueError("something else"))
        assert stage == "connect"
        assert "ValueError" in detail

    @pytest.mark.parametrize(
        "message", ["403 Forbidden", "invalid api key", "unauthorized"]
    )
    def test_auth_markers(self, message):
        from acc.mcp.errors import MCPConnectionError

        assert A.classify_failure(MCPConnectionError(message))[0] == "auth"


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


class TestOverrideStorage:
    def test_overrides_round_trip(self, store):
        A.disable_server("a")
        A.disable_tool("b", "danger")
        A.add_server("c", url="https://example.invalid")

        ov = A.load_overrides()
        assert ov.disabled_servers == {"a"}
        assert ov.disabled_tools == {"b": {"danger"}}
        assert "c" in ov.added

    def test_a_malformed_file_overrides_nothing(self, store, tmp_path):
        """Failing open is right HERE because overrides only subtract.

        An unreadable file falling back to 'no overrides' restores the governed
        baseline; it cannot grant anything.
        """
        (tmp_path / "mcp-overrides.yaml").write_text("not: [valid", encoding="utf-8")
        ov = A.load_overrides()
        assert ov.disabled_servers == set()
        assert ov.added == {}

    def test_a_duplicate_added_server_is_refused(self, store):
        A.add_server("dup", url="https://example.invalid")
        with pytest.raises(A.MCPAdminError, match="already"):
            A.add_server("dup", url="https://example.invalid")

    def test_http_transport_requires_a_url(self, store):
        with pytest.raises(A.MCPAdminError, match="--url"):
            A.add_server("no-url", transport="http")

    def test_removing_a_packaged_server_is_refused(self, store):
        """Packaged servers are signed content with their own trust rules.

        Deleting one from a host file would be a way to quietly diverge from
        what the package declares; disabling is the supported route.
        """
        assert A.remove_server("bundled-thing") is False

    def test_removing_an_operator_added_server_works(self, store):
        A.add_server("mine", url="https://example.invalid")
        assert A.remove_server("mine") is True
        assert "mine" not in A.load_overrides().added

    def test_source_attribution(self, store):
        A.add_server("mine", url="https://example.invalid")
        ov = A.load_overrides()
        assert A.source_of("mine", registry_ids=["files"], overrides=ov) == "operator"
        assert A.source_of("files", registry_ids=["files"], overrides=ov) == "registry"

    def test_the_file_states_that_overrides_only_subtract(self, store):
        A.disable_server("a")
        text = A.overrides_path().read_text(encoding="utf-8")
        assert "subtract" in text.lower()
