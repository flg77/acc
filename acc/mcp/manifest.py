"""Pydantic schema for ``mcps/<id>/mcp.yaml`` manifests.

Each manifest describes how to reach exactly one MCP server, and which
governance hooks apply when one of its tools is invoked.  Discovery is
intentionally identical to skills: filesystem-first, deep-merge with
``mcps/_base/mcp.yaml`` defaults, lowercase snake_case server_id.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MCPTransport = Literal["http", "stdio"]
"""Supported transports.

* ``http`` — JSON-RPC 2.0 over HTTP POST.  Production path.  Requires
  ``url``.
* ``stdio`` — subprocess pipe with newline-delimited JSON-RPC.
  Reserved for a future PR; the manifest validator accepts the
  enum value but the client raises ``NotImplementedError`` until the
  stdio transport lands.
"""


MCPRiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
"""Same EU AI Act–aligned levels as :data:`acc.skills.SkillRiskLevel`.

Cat-A rule A-018 (Phase 4.3) blocks tool-call invocations whose
declared ``risk_level`` exceeds the role's tolerance.  ``CRITICAL``
additionally enqueues the call into the human oversight queue.
"""


class MCPManifest(BaseModel):
    """Validated representation of one ``mcp.yaml``.

    Attributes:
        server_id: Stable identifier — must match the parent directory
            and be lowercase snake_case.  Used as the key in
            :attr:`acc.config.RoleDefinitionConfig.allowed_mcps`.
        version: SemVer of the local *manifest* (not the upstream
            server).  Bump when the governance contract changes — e.g.
            tightening ``allowed_tools`` or raising ``risk_level``.
        purpose: One-sentence human-readable description.

        transport: ``http`` (default) or ``stdio``.

        url: Required for ``http`` transport.  Full base URL of the
            MCP server's JSON-RPC endpoint, e.g.
            ``http://acc-mcp-echo:8080/rpc``.  Trailing slash is
            preserved as-is — the client does not normalise.
        timeout_s: HTTP request timeout (seconds).  Applied per-call,
            not per-session.

        command: Required for ``stdio`` transport (when implemented).
            Shell-tokenised list, e.g. ``["python", "-m", "my_mcp"]``.
        env: Extra environment variables for the spawned subprocess.
            Stdio only; ignored for HTTP.

        api_key_env: Name of the environment variable holding a bearer
            token; sent as ``Authorization: Bearer <value>``.  Empty
            string ⇒ unauthenticated request.

        allowed_tools: Whitelist of tool names this manifest exposes.
            Empty list ⇒ allow every tool the server advertises.  This
            is the operator-side sandbox: even if the MCP server
            offers ``shell.exec``, omitting it from ``allowed_tools``
            keeps it unreachable from any role.
        denied_tools: Blacklist applied AFTER ``allowed_tools``.
            Useful when ``allowed_tools`` is ``[]`` (allow all) but a
            handful of tools are known unsafe.

        requires_actions: Action labels the calling role must include
            in its ``allowed_actions`` list.  Cat-A A-018 raises
            on the first missing entry.
        domain_id: Optional biological tag.
        risk_level: EU AI Act class.

        description: Long-form Markdown surfaced in the TUI Ecosystem
            screen detail panel (Phase 4.4).
        tags: Free-form filter labels.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    # Identity
    server_id: str = Field(min_length=1)
    version: str = "0.1.0"
    purpose: str = Field(min_length=1)

    # Transport
    transport: MCPTransport = "http"

    # HTTP transport fields
    url: str = ""
    timeout_s: int = Field(default=30, ge=1, le=600)
    api_key_env: str = ""

    # Stdio transport fields (reserved)
    command: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    # Tool allow/deny lists — applied at the registry boundary so the
    # caller never sees a hidden tool even if the server lists it.
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)

    # Governance
    requires_actions: list[str] = Field(default_factory=list)
    domain_id: str = ""
    risk_level: MCPRiskLevel = "LOW"

    # Operator-facing metadata
    description: str = ""
    tags: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("server_id")
    @classmethod
    def _id_is_snake_case(cls, value: str) -> str:
        """Same rule as skills/role ids — keeps wire/file/Python identifiers
        round-trippable without quoting."""
        if not value:
            raise ValueError("server_id must be non-empty")
        if not all(c.islower() or c.isdigit() or c == "_" for c in value):
            raise ValueError(
                f"server_id {value!r} must be lowercase snake_case "
                "(letters, digits, and underscores only)"
            )
        return value

    @model_validator(mode="after")
    def _transport_consistency(self) -> "MCPManifest":
        """Each transport requires a different field set; fail fast on
        inconsistencies rather than at first-call time."""
        if self.transport == "http":
            if not self.url:
                raise ValueError(
                    f"server_id={self.server_id!r}: transport=http requires 'url'"
                )
        elif self.transport == "stdio":
            if not self.command:
                raise ValueError(
                    f"server_id={self.server_id!r}: transport=stdio requires "
                    "non-empty 'command' list"
                )
        return self

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Apply the manifest's allowed/denied lists to *tool_name*.

        Empty ``allowed_tools`` is treated as "allow all" so a manifest
        that only sets ``denied_tools`` still works.  ``denied_tools``
        is always applied last and wins over ``allowed_tools``.
        """
        if self.denied_tools and tool_name in self.denied_tools:
            return False
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return False
        return True
