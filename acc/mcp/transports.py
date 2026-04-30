"""MCP transport implementations — HTTP + stdio.

The :class:`acc.mcp.client.MCPClient` is transport-agnostic: it
constructs the JSON-RPC envelope, delegates the round-trip to a
:class:`Transport`, and validates the response.  Two concrete
transports ship today:

* :class:`HTTPTransport` — JSON-RPC 2.0 over HTTP POST via httpx
  (production path; same behaviour as the inline implementation that
  shipped in PR 4.2).
* :class:`StdioTransport` — newline-delimited JSON-RPC 2.0 over the
  stdin/stdout pipes of a subprocess MCP server (the convention used
  by Anthropic's reference Python SDK + most local MCP servers).

The stdio transport unblocks the most common deployment shape: an MCP
server packaged as a CLI binary the operator wants to invoke locally
without running an HTTP service.

Per-transport concurrency note:

* HTTP transports are naturally request-multiplexed — two concurrent
  ``send_rpc`` calls on the same httpx client are independent
  request/response pairs.
* Stdio uses ONE pipe pair shared by every RPC, so concurrent calls
  must be serialised.  We hold an :class:`asyncio.Lock` around each
  call.  ``MCPClient`` is documented as single-task per instance so
  contention is rare in practice; the lock is defence-in-depth.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Protocol

import httpx

from acc.mcp.errors import MCPConnectionError, MCPProtocolError, MCPTransportError
from acc.mcp.manifest import MCPManifest

logger = logging.getLogger("acc.mcp.transports")


# Read cap per stdio response — protects against an MCP server emitting
# unbounded output (e.g. a misbehaving tool dumping a 1 GiB log).  The
# JSON-RPC spec doesn't bound payload size, but in practice well-behaved
# servers stay well under 1 MiB.  Tunable via ``ACC_MCP_STDIO_MAX_BYTES``.
_STDIO_LINE_LIMIT = int(os.environ.get("ACC_MCP_STDIO_MAX_BYTES", str(2**20)))


class Transport(Protocol):
    """Common shape every transport implements.

    The ``MCPClient`` calls these and nothing else; adding a new
    transport (websockets, gRPC) means writing one of these and
    extending :func:`build_transport` to dispatch on
    ``manifest.transport``.
    """

    async def send_rpc(self, envelope: dict) -> dict:
        """Send one JSON-RPC envelope and return the decoded response.

        The implementation is responsible for:

        * Wire framing (HTTP body vs newline-delimited stdio bytes).
        * Translating transport-level failures into
          :class:`MCPTransportError`.
        * Returning the parsed-but-not-validated dict; the caller
          checks the JSON-RPC envelope shape.
        """
        ...

    async def close(self) -> None:
        """Release the underlying connection / subprocess.  Idempotent."""
        ...


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class HTTPTransport:
    """JSON-RPC 2.0 over HTTP POST via httpx.

    Identical wire behaviour to the inline implementation that shipped
    in PR 4.2 — moved into its own class so the dispatch layer is
    pluggable.

    Args:
        manifest: Source of ``url``, ``timeout_s``, ``api_key_env``,
            and ``server_id`` (for error messages).
    """

    def __init__(self, manifest: MCPManifest) -> None:
        self._manifest = manifest
        headers = {"Content-Type": "application/json"}
        if manifest.api_key_env:
            api_key = os.environ.get(manifest.api_key_env, "")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            else:
                logger.warning(
                    "mcp: api_key_env=%r set on server_id=%r but env var is "
                    "empty — sending request unauthenticated",
                    manifest.api_key_env, manifest.server_id,
                )
        self._client = httpx.AsyncClient(
            base_url=manifest.url,
            timeout=manifest.timeout_s,
            headers=headers,
        )

    async def send_rpc(self, envelope: dict) -> dict:
        try:
            response = await self._client.post("", json=envelope)
        except httpx.TimeoutException as exc:
            raise MCPTransportError(
                f"server_id={self._manifest.server_id!r}: timeout "
                f"calling {envelope.get('method', '?')}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MCPTransportError(
                f"server_id={self._manifest.server_id!r}: transport error "
                f"calling {envelope.get('method', '?')}: {exc}"
            ) from exc

        if response.status_code >= 500:
            raise MCPTransportError(
                f"server_id={self._manifest.server_id!r}: HTTP "
                f"{response.status_code} from {envelope.get('method', '?')}"
            )

        try:
            body = response.json()
        except Exception as exc:
            raise MCPProtocolError(
                f"server_id={self._manifest.server_id!r}: non-JSON response "
                f"(HTTP {response.status_code})"
            ) from exc

        if not isinstance(body, dict):
            raise MCPProtocolError(
                f"server_id={self._manifest.server_id!r}: response is not a JSON object"
            )
        return body

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception:  # pragma: no cover — defensive
            logger.exception(
                "mcp: HTTP aclose failed for %r", self._manifest.server_id,
            )


# ---------------------------------------------------------------------------
# Stdio
# ---------------------------------------------------------------------------


class StdioTransport:
    """Newline-delimited JSON-RPC 2.0 over a subprocess's stdin/stdout.

    Wire format (matches Anthropic's reference Python SDK + the
    majority of community MCP servers):

    * Each request is ``json.dumps(envelope) + "\\n"`` written to the
      child's stdin.
    * Each response is ONE line on the child's stdout, terminated by
      ``"\\n"``, decodable as a JSON-RPC 2.0 response object.
    * Stderr is NOT parsed — we drain it into the local logger at
      DEBUG so the OS pipe buffer can't fill and stall the child.

    Concurrency: stdio shares a single pipe pair across every RPC, so
    we serialise calls with an ``asyncio.Lock``.  The cost is at most
    one suspend per call when no contention exists.

    Lifecycle:

        transport = StdioTransport(manifest)
        await transport.start()             # spawn subprocess
        result = await transport.send_rpc(envelope)
        await transport.close()             # terminate child + drain
    """

    def __init__(self, manifest: MCPManifest) -> None:
        self._manifest = manifest
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._stderr_drain: asyncio.Task | None = None

    async def start(self) -> None:
        """Spawn the subprocess described by ``manifest.command`` + env.

        Must be called once before :meth:`send_rpc`.  ``MCPClient``
        invokes this from its ``initialize`` method.
        """
        if self._proc is not None:
            return
        if not self._manifest.command:
            raise MCPConnectionError(
                f"server_id={self._manifest.server_id!r}: stdio transport "
                "requires non-empty 'command' list"
            )

        env = dict(os.environ)
        env.update(self._manifest.env or {})

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._manifest.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise MCPConnectionError(
                f"server_id={self._manifest.server_id!r}: command not found: "
                f"{self._manifest.command[0]!r}"
            ) from exc
        except Exception as exc:
            raise MCPConnectionError(
                f"server_id={self._manifest.server_id!r}: failed to spawn "
                f"subprocess: {exc}"
            ) from exc

        # Drain stderr concurrently so it doesn't block when the pipe
        # buffer fills.  Captured at DEBUG so operators can pull traces
        # via ACC_LOG_LEVEL=DEBUG when an MCP server misbehaves.
        self._stderr_drain = asyncio.create_task(
            self._consume_stderr(),
            name=f"mcp-stderr-{self._manifest.server_id}",
        )
        logger.info(
            "mcp_stdio: spawned server_id=%r pid=%d cmd=%s",
            self._manifest.server_id, self._proc.pid,
            " ".join(self._manifest.command),
        )

    async def send_rpc(self, envelope: dict) -> dict:
        """Write one envelope, read one response.  Serialised by lock."""
        if self._proc is None:
            raise MCPConnectionError(
                f"server_id={self._manifest.server_id!r}: stdio transport "
                "not started — call .start() first"
            )
        if self._proc.returncode is not None:
            raise MCPTransportError(
                f"server_id={self._manifest.server_id!r}: subprocess exited "
                f"(returncode={self._proc.returncode}) before RPC"
            )

        request_bytes = (json.dumps(envelope) + "\n").encode("utf-8")

        async with self._lock:
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write(request_bytes)
                await self._proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise MCPTransportError(
                    f"server_id={self._manifest.server_id!r}: stdin write "
                    f"failed: {exc}"
                ) from exc

            try:
                line = await asyncio.wait_for(
                    self._proc.stdout.readline(),  # type: ignore[union-attr]
                    timeout=self._manifest.timeout_s,
                )
            except asyncio.TimeoutError as exc:
                raise MCPTransportError(
                    f"server_id={self._manifest.server_id!r}: timeout reading "
                    f"response for {envelope.get('method', '?')}"
                ) from exc
            except Exception as exc:
                raise MCPTransportError(
                    f"server_id={self._manifest.server_id!r}: stdout read "
                    f"failed: {exc}"
                ) from exc

        if not line:
            # readline returns b"" on EOF — child closed stdout, almost
            # certainly because it crashed.  Surface the returncode +
            # last bit of stderr to the caller.
            rc = self._proc.returncode
            raise MCPTransportError(
                f"server_id={self._manifest.server_id!r}: subprocess closed "
                f"stdout (returncode={rc}); last stderr in DEBUG log"
            )

        if len(line) > _STDIO_LINE_LIMIT:
            raise MCPTransportError(
                f"server_id={self._manifest.server_id!r}: response line "
                f"exceeded ACC_MCP_STDIO_MAX_BYTES ({_STDIO_LINE_LIMIT})"
            )

        try:
            body = json.loads(line.decode("utf-8"))
        except Exception as exc:
            raise MCPProtocolError(
                f"server_id={self._manifest.server_id!r}: non-JSON line "
                f"on stdout: {line[:200]!r}"
            ) from exc

        if not isinstance(body, dict):
            raise MCPProtocolError(
                f"server_id={self._manifest.server_id!r}: stdout response "
                f"is not a JSON object"
            )
        return body

    async def close(self) -> None:
        """Terminate the subprocess + drain its stderr task.  Idempotent."""
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None  # mark closed up-front so concurrent calls bail

        # Try a graceful EOF on stdin first — well-behaved MCP servers
        # exit on stdin close.  Fall through to terminate() if the
        # process is still alive after a short wait.
        try:
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.close()
        except Exception:
            logger.debug(
                "mcp_stdio: stdin close raised for %r", self._manifest.server_id,
            )

        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            logger.warning(
                "mcp_stdio: server_id=%r did not exit on stdin close — "
                "sending SIGTERM", self._manifest.server_id,
            )
            try:
                proc.terminate()
            except ProcessLookupError:
                pass  # already gone
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "mcp_stdio: server_id=%r ignored SIGTERM — sending SIGKILL",
                    self._manifest.server_id,
                )
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
        finally:
            if self._stderr_drain is not None and not self._stderr_drain.done():
                self._stderr_drain.cancel()
                try:
                    await self._stderr_drain
                except (asyncio.CancelledError, Exception):
                    pass
            self._stderr_drain = None
            logger.info(
                "mcp_stdio: closed server_id=%r returncode=%s",
                self._manifest.server_id, proc.returncode,
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _consume_stderr(self) -> None:
        """Drain stderr forever, logging each line at DEBUG.

        Exits when stderr closes (EOF returns b"").  Cancellation
        during ``close()`` is handled by the caller.
        """
        if self._proc is None or self._proc.stderr is None:
            return
        sid = self._manifest.server_id
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    return  # EOF — child closed stderr
                logger.debug(
                    "mcp_stdio[%s] stderr: %s", sid, line.rstrip().decode(
                        "utf-8", errors="replace",
                    ),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "mcp_stdio: stderr drain failed for %r", sid,
            )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def build_transport(manifest: MCPManifest) -> Transport:
    """Construct the right Transport for *manifest.transport*.

    Adding a new transport: implement the Protocol, dispatch here.
    The :class:`MCPClient` knows nothing about the concrete classes
    beyond the Protocol surface.
    """
    if manifest.transport == "http":
        return HTTPTransport(manifest)
    if manifest.transport == "stdio":
        return StdioTransport(manifest)
    raise NotImplementedError(
        f"unknown MCP transport {manifest.transport!r} "
        f"(server_id={manifest.server_id!r})"
    )
