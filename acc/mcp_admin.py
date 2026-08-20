"""Host-level administration of MCP servers, composed with role governance.

Two things an operator needs and currently cannot do: find out *why* an MCP
server is not working, and turn one off without editing a governed role.

The second is where the care goes. Roles already declare which MCP servers they
may use, and that declaration is governed — it is countersigned and audited. A
host-level toggle that could grant a role something it never declared would be a
second, ungoverned permission path, and the ungoverned one is the one nobody
audits.

So the host layer can only ever **subtract**:

    effective = manifest ∩ host ∩ role

An operator override restricts. It never grants. That is asserted directly in
the tests rather than left as an intention, because it is the property that
makes this safe to ship.

The other half is that "unavailable" has to say *which side said no*. A tool
missing because the host disabled it and a tool missing because the role never
declared it look identical from the agent's side and need completely different
fixes — one is a local decision, the other needs a governed role change.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from acc._atomic_write import atomic_write_text

logger = logging.getLogger("acc.mcp_admin")

OVERRIDES_PATH_VAR = "ACC_MCP_OVERRIDES_PATH"
DEFAULT_OVERRIDES_FILE = "mcp-overrides.yaml"


class MCPAdminError(Exception):
    """An administrative action was refused. The message is operator-facing."""


# ---------------------------------------------------------------------------
# Host overrides
# ---------------------------------------------------------------------------


@dataclass
class HostOverrides:
    """What this host has turned off, and what it has added.

    Attributes:
        disabled_servers: server ids switched off host-wide.
        disabled_tools: ``{server_id: [tool, ...]}`` switched off host-wide.
        added: operator-added server definitions, keyed by id.
    """

    disabled_servers: set[str] = field(default_factory=set)
    disabled_tools: dict[str, set[str]] = field(default_factory=dict)
    added: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "disabled_servers": sorted(self.disabled_servers),
            "disabled_tools": {
                k: sorted(v) for k, v in sorted(self.disabled_tools.items()) if v
            },
            "added": self.added,
        }


def overrides_path(repo_root: Path | None = None) -> Path:
    raw = os.environ.get(OVERRIDES_PATH_VAR, "").strip()
    if raw:
        return Path(raw)
    root = repo_root or Path(__file__).resolve().parent.parent
    return root / DEFAULT_OVERRIDES_FILE


def load_overrides(repo_root: Path | None = None) -> HostOverrides:
    """Read host overrides. A malformed file overrides nothing, loudly.

    Failing open is deliberate here: these only ever subtract, so an unreadable
    file falling back to "no overrides" restores the governed baseline rather
    than granting anything.
    """
    path = overrides_path(repo_root)
    if not path.is_file():
        return HostOverrides()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.error("mcp: cannot read %s (%s) — no overrides applied", path, exc)
        return HostOverrides()
    if not isinstance(raw, dict):
        return HostOverrides()

    disabled_tools: dict[str, set[str]] = {}
    for server, tools in (raw.get("disabled_tools") or {}).items():
        if isinstance(tools, list):
            disabled_tools[str(server)] = {str(t) for t in tools if str(t).strip()}

    added = raw.get("added") or {}
    return HostOverrides(
        disabled_servers={
            str(s) for s in (raw.get("disabled_servers") or []) if str(s).strip()
        },
        disabled_tools=disabled_tools,
        added=added if isinstance(added, dict) else {},
    )


def save_overrides(ov: HostOverrides, repo_root: Path | None = None) -> Path:
    path = overrides_path(repo_root)
    header = (
        "# Host-level MCP overrides.\n"
        "# These only ever SUBTRACT. A host cannot grant a role an MCP server or\n"
        "# tool its governed role definition does not declare -- that would be a\n"
        "# second, ungoverned permission path.\n"
    )
    body = yaml.safe_dump(ov.as_dict(), sort_keys=False, allow_unicode=True)
    atomic_write_text(path, header + body, mode=0o644, newline="")
    return path


# ---------------------------------------------------------------------------
# Effective permissions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """Whether something is usable, and which layer decided."""

    allowed: bool
    denied_by: str = ""   # "host" | "role" | "manifest" | ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "denied_by": self.denied_by, "reason": self.reason}


def server_decision(
    server_id: str,
    *,
    role_allowed: Iterable[str] | None,
    overrides: HostOverrides,
    known: Iterable[str] | None = None,
) -> Decision:
    """May *server_id* be used by a role declaring *role_allowed*?

    ``role_allowed=None`` means "not asking about a role" — a host-level view.
    """
    if known is not None and server_id not in set(known):
        return Decision(False, "manifest", "no such server in the registry")
    if server_id in overrides.disabled_servers:
        return Decision(False, "host", f"disabled on this host ({DEFAULT_OVERRIDES_FILE})")
    if role_allowed is not None and server_id not in set(role_allowed):
        return Decision(False, "role", "not in the role's allowed_mcps")
    return Decision(True)


def tool_decision(
    server_id: str,
    tool: str,
    *,
    manifest: Any,
    overrides: HostOverrides,
) -> Decision:
    """May *tool* on *server_id* be called?

    The manifest's own allow/deny lists are checked first because they are the
    package's declaration; the host layer then subtracts from whatever survives.
    """
    allowed_tools = set(getattr(manifest, "allowed_tools", None) or [])
    denied_tools = set(getattr(manifest, "denied_tools", None) or [])

    if allowed_tools and tool not in allowed_tools:
        return Decision(False, "manifest", "not in the manifest's allowed_tools")
    if tool in denied_tools:
        return Decision(False, "manifest", "in the manifest's denied_tools")
    if tool in overrides.disabled_tools.get(server_id, set()):
        return Decision(False, "host", f"disabled on this host ({DEFAULT_OVERRIDES_FILE})")
    return Decision(True)


def effective_tools(
    server_id: str,
    advertised: Iterable[str],
    *,
    manifest: Any,
    overrides: HostOverrides,
) -> dict[str, Decision]:
    """Every advertised tool with its decision, so both sides are inspectable."""
    return {
        tool: tool_decision(server_id, tool, manifest=manifest, overrides=overrides)
        for tool in sorted(set(advertised))
    }


# ---------------------------------------------------------------------------
# Administration
# ---------------------------------------------------------------------------


def disable_server(server_id: str, repo_root: Path | None = None) -> None:
    ov = load_overrides(repo_root)
    ov.disabled_servers.add(server_id)
    save_overrides(ov, repo_root)


def enable_server(server_id: str, repo_root: Path | None = None) -> None:
    """Re-enable a server. Restores the governed baseline; grants nothing."""
    ov = load_overrides(repo_root)
    ov.disabled_servers.discard(server_id)
    save_overrides(ov, repo_root)


def disable_tool(server_id: str, tool: str, repo_root: Path | None = None) -> None:
    ov = load_overrides(repo_root)
    ov.disabled_tools.setdefault(server_id, set()).add(tool)
    save_overrides(ov, repo_root)


def enable_tool(server_id: str, tool: str, repo_root: Path | None = None) -> None:
    """Remove a host-level tool block.

    This does NOT grant the tool: if the manifest or the role denies it, it
    stays denied. Removing a subtraction cannot add.
    """
    ov = load_overrides(repo_root)
    tools = ov.disabled_tools.get(server_id)
    if tools:
        tools.discard(tool)
        if not tools:
            ov.disabled_tools.pop(server_id, None)
    save_overrides(ov, repo_root)


def add_server(
    server_id: str,
    *,
    url: str = "",
    transport: str = "http",
    api_key_env: str = "",
    purpose: str = "",
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Register an operator-added server without hand-editing files.

    Raises:
        MCPAdminError: on a duplicate id or a missing required field.
    """
    if not server_id.strip():
        raise MCPAdminError("server id is required")
    ov = load_overrides(repo_root)
    if server_id in ov.added:
        raise MCPAdminError(f"{server_id!r} is already an operator-added server")
    if transport == "http" and not url.strip():
        raise MCPAdminError("http transport requires --url")

    definition = {
        "server_id": server_id,
        "transport": transport,
        "url": url,
        "purpose": purpose or f"Operator-added MCP server {server_id}",
    }
    if api_key_env:
        definition["api_key_env"] = api_key_env
    ov.added[server_id] = definition
    save_overrides(ov, repo_root)
    return definition


def remove_server(server_id: str, repo_root: Path | None = None) -> bool:
    """Remove an operator-added server. Bundled/package servers are untouched.

    A packaged server is signed content with its own trust rules; deleting it
    from a host override file would be a way to quietly diverge from what the
    package declares.
    """
    ov = load_overrides(repo_root)
    if server_id not in ov.added:
        return False
    ov.added.pop(server_id)
    save_overrides(ov, repo_root)
    return True


def source_of(server_id: str, *, registry_ids: Iterable[str], overrides: HostOverrides) -> str:
    """Where a server came from: bundled/package registry, or operator-added."""
    if server_id in overrides.added:
        return "operator"
    return "registry" if server_id in set(registry_ids) else "unknown"


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------


@dataclass
class TestResult:
    """The outcome of probing one server.

    ``stage`` is the point of failure, and it is the whole value of the
    command: unreachable, rejected-our-credentials and answered-but-could-not-
    list-tools need three different fixes and look identical from an agent.
    """

    server_id: str
    ok: bool
    stage: str          # "connect" | "auth" | "tools" | "ok"
    detail: str = ""
    tools: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "ok": self.ok,
            "stage": self.stage,
            "detail": self.detail,
            "tools": self.tools,
        }


def classify_failure(exc: BaseException) -> tuple[str, str]:
    """Map an MCP exception onto (stage, detail).

    Authentication is separated from a generic protocol error because "your
    credential was rejected" and "the server misbehaved" send an operator to
    completely different places.
    """
    from acc.mcp.errors import (  # noqa: PLC0415
        MCPConnectionError,
        MCPProtocolError,
        MCPTransportError,
    )

    text = str(exc)
    lowered = text.lower()
    if any(marker in lowered for marker in ("401", "403", "unauthor", "forbidden", "api key")):
        return "auth", text
    if isinstance(exc, MCPConnectionError):
        return "connect", text
    if isinstance(exc, (MCPProtocolError, MCPTransportError)):
        return "tools", text
    return "connect", f"{type(exc).__name__}: {text}"


async def test_server(manifest: Any) -> TestResult:
    """Connect, list tools, and report precisely where it failed."""
    from acc.mcp.client import MCPClient  # noqa: PLC0415

    server_id = str(getattr(manifest, "server_id", "") or "?")
    client = MCPClient(manifest)
    try:
        await client.initialize()
    except Exception as exc:  # noqa: BLE001 — classified, never raised
        stage, detail = classify_failure(exc)
        return TestResult(server_id, False, stage, detail)

    try:
        tools = await client.list_tools(refresh=True)
    except Exception as exc:  # noqa: BLE001
        stage, detail = classify_failure(exc)
        return TestResult(server_id, False, "tools" if stage != "auth" else "auth", detail)
    finally:
        try:
            await client.close()
        except Exception:  # pragma: no cover
            pass

    names = [str(t.get("name", "")) for t in tools if isinstance(t, dict)]
    return TestResult(server_id, True, "ok", "", [n for n in names if n])
