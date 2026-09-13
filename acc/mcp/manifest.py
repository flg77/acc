"""Pydantic schema for ``mcps/<id>/mcp.yaml`` manifests.

Each manifest describes how to reach exactly one MCP server, and which
governance hooks apply when one of its tools is invoked.  Discovery is
intentionally identical to skills: filesystem-first, deep-merge with
``mcps/_base/mcp.yaml`` defaults, lowercase snake_case server_id.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MCPTransport = Literal["http", "streamable-http", "stdio"]
"""Supported transports.

* ``http`` — JSON-RPC 2.0 over HTTP POST.  Production path.  Requires
  ``url``.
* ``streamable-http`` — the MCP HTTP transport (`20260913-mcp-streamable-http`,
  MC-01).  A session id issued on ``initialize`` and echoed on every later
  request, and responses that may arrive as JSON or as an SSE stream.  This is
  what `fastmcp` and most of the current ecosystem serve; plain ``http`` is
  answered by such a server with ``-32600 Missing session ID``.  Requires
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


class MCPToolSpec(BaseModel):
    """One tool a manifest chooses to describe (`20260912-mcp-tools-in-the-prompt`).

    The agent is told the marker syntax and the server id; without this it is
    never told the tool *names*, so it guesses them and A-018 refuses the guess
    (measured against midojo, AS-04: `get_current` for a server whose tool is
    `get_weather`).  Declaring a tool here is documentation the operator owns —
    it never widens what may be called, because the validator requires every
    declared tool to be permitted by ``allowed_tools`` / ``denied_tools``.

    Attributes:
        name: The tool name as the server exposes it.
        summary: One line, rendered after the signature.  Keep it short —
            this text sits in every prompt for every role that gets the server.
        args: Argument names, in the order the server documents them.  Names
            only: the manifest is not a schema, and a wrong type is a failure
            the model can see, while a wrong name is one it cannot.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    summary: str = ""
    args: list[str] = Field(default_factory=list)


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

    # Auth mode (integrations pillar 2 — office suites). "" / "api_key" use the
    # legacy static bearer from ``api_key_env``; "oauth" resolves a SHORT-LIVED,
    # per-operator bearer at call time via acc.credentials.CredentialBroker
    # (MCP OAuth 2.1 delegated auth). ``oauth_provider`` names the provider
    # config (e.g. "google", "microsoft"). See ACC-PR/Proposals/PR-PROPOSAL-B.
    auth: str = ""
    oauth_provider: str = ""

    # Stdio transport fields (reserved)
    command: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    # Tool allow/deny lists — applied at the registry boundary so the
    # caller never sees a hidden tool even if the server lists it.
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)

    # `20260912-mcp-tools-in-the-prompt` (MC-02) -- the tools this server
    # offers, as the agent should be told about them.  Optional: with no
    # entries the prompt falls back to the names in ``allowed_tools``, and with
    # neither it says only that the server exists, which is what every manifest
    # did before.
    tools: list[MCPToolSpec] = Field(default_factory=list)

    # Governance
    requires_actions: list[str] = Field(default_factory=list)
    domain_id: str = ""
    risk_level: MCPRiskLevel = "LOW"
    # Gate categories (`20260902-assistant-autonomy-prompt-pane-approvals` 1.2),
    # server-wide.  None = undeclared -> the name table in
    # acc.operating_modes.gate_categories decides per tool
    # ("google_workspace.gmail_send" -> acts_on_behalf), which is why a
    # read-first server such as google_workspace declares nothing here.
    system_access: bool | None = None
    acts_on_behalf: bool | None = None
    # `20260911-question-envelope` -- tools that delete or overwrite data, per
    # tool (a server is rarely destructive as a whole).  A tool not listed
    # falls back to its name ("files.delete_file").
    destructive_tools: list[str] = Field(default_factory=list)

    # `20260913-preview-in-the-panel` (UX-03 Phase 2) -- arguments that turn a
    # call on this server into a dry run, merged at gate time so the decision
    # panel can show what it WOULD do.  Empty (the default) means no preview
    # and nothing runs.  Declared, never inferred.
    preview_args: dict[str, Any] = Field(default_factory=dict)

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
    def _tools_are_permitted(self) -> "MCPManifest":
        """A manifest may not advertise a tool its own lists refuse.

        Advertising what A-018 would block teaches the model a call that always
        fails -- the exact failure this field exists to end, arriving from the
        other side.
        """
        seen: set[str] = set()
        for spec in self.tools:
            if spec.name in seen:
                raise ValueError(
                    f"server_id={self.server_id!r}: tool {spec.name!r} declared twice"
                )
            seen.add(spec.name)
            if not self.is_tool_allowed(spec.name):
                raise ValueError(
                    f"server_id={self.server_id!r}: tool {spec.name!r} is declared "
                    f"in 'tools' but refused by allowed_tools/denied_tools -- "
                    f"advertising it would teach a call that A-018 blocks"
                )
        return self

    @model_validator(mode="after")
    def _transport_consistency(self) -> "MCPManifest":
        """Each transport requires a different field set; fail fast on
        inconsistencies rather than at first-call time."""
        if self.transport in ("http", "streamable-http"):
            if not self.url:
                raise ValueError(
                    f"server_id={self.server_id!r}: transport={self.transport} requires 'url'"
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
MAX_ADVERTISED_TOOLS: int = 12
"""How many of a server's tools the prompt names before it stops.

Every line here sits in the prompt of every role that gets the server, on
every turn.  Ten real names beat guessing forty, and a server with more tools
than this should be saying which ones matter by ordering its ``tools`` list.
"""


def advertised_tool_lines(
    manifest: "MCPManifest",
    limit: int = MAX_ADVERTISED_TOOLS,
) -> list[str]:
    """Render what the agent should be told about *manifest*'s tools.

    Three sources, best first:

    * the manifest's ``tools`` -- name, argument names and a one-line summary;
    * failing that, the names in ``allowed_tools``, which is what A-018
      enforces anyway and is already written down in most manifests;
    * failing both, nothing: the server exists and the agent is told only that,
      which is exactly what every manifest conveyed before MC-02.

    Returns bare lines without indentation; the caller places them.
    """
    lines: list[str] = []
    if manifest.tools:
        shown = manifest.tools[:limit]
        for spec in shown:
            args = ", ".join(f'"{a}": ...' for a in spec.args)
            signature = f"{spec.name} {{{args}}}"
            lines.append(
                f"{signature} -- {spec.summary}" if spec.summary else signature
            )
        remaining = len(manifest.tools) - len(shown)
    elif manifest.allowed_tools:
        shown_names = manifest.allowed_tools[:limit]
        lines.extend(shown_names)
        remaining = len(manifest.allowed_tools) - len(shown_names)
    else:
        return []
    if remaining > 0:
        lines.append(f"... and {remaining} more")
    return lines

