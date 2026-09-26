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

from acc import secret_source
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

    def __init__(self, manifest: MCPManifest, *, bearer_resolver=None) -> None:
        self._manifest = manifest
        # ``bearer_resolver`` (async () -> str) injects a fresh per-request OAuth
        # bearer for ``auth: oauth`` manifests (office suites). None → legacy
        # static ``api_key_env`` behaviour, fully backward-compatible.
        self._bearer_resolver = bearer_resolver
        headers = {"Content-Type": "application/json"}
        if (manifest.auth != "oauth" and manifest.api_key_env
                and not secret_source.get(manifest.api_key_env)):
            logger.warning(
                "mcp: api_key_env=%r set on server_id=%r but no source holds "
                "it — requests go unauthenticated until one does",
                manifest.api_key_env, manifest.server_id,
            )
        self._client = httpx.AsyncClient(
            base_url=manifest.url,
            timeout=manifest.timeout_s,
            headers=headers,
        )

    async def send_rpc(self, envelope: dict) -> dict:
        # Resolved per request: an OAuth bearer refreshes, a rotated static key
        # is used on the next call.
        req_headers: dict | None = await _auth_headers(
            self._manifest, self._bearer_resolver,
        ) or None
        try:
            response = await self._client.post("", json=envelope, headers=req_headers)
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
# Streamable HTTP (`20260913-mcp-streamable-http`, MC-01)
# ---------------------------------------------------------------------------

PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"
SESSION_ID_HEADER = "Mcp-Session-Id"
PROTOCOL_VERSION = "2024-11-05"


class StreamableHTTPTransport:
    """The MCP HTTP transport: a session id, and responses that may stream.

    :class:`HTTPTransport` posts a bare JSON-RPC envelope and expects a JSON
    body.  That is JSON-RPC over HTTP, and it is not what MCP servers speak: a
    ``fastmcp`` server answers it with ``-32600 Missing session ID``.  This
    transport adds the three things the MCP HTTP binding actually requires --

    * the ``Mcp-Session-Id`` the server issues on ``initialize``, echoed on
      every later request;
    * the ``notifications/initialized`` the client owes the server once the
      handshake succeeds (sent here, because it is an obligation of *this*
      transport and not something :class:`~acc.mcp.client.MCPClient` should
      have to know);
    * responses decoded from either a JSON body or a ``text/event-stream``.

    Errors keep the shapes the dispatch layer and the audit line already use.
    """

    def __init__(self, manifest: MCPManifest, *, bearer_resolver=None) -> None:
        self._manifest = manifest
        self._bearer_resolver = bearer_resolver
        self._session_id = ""
        headers = {
            "Content-Type": "application/json",
            # Both are legal answers to one POST; say so, or a server may
            # refuse the request outright.
            "Accept": "application/json, text/event-stream",
        }
        if (manifest.auth != "oauth" and manifest.api_key_env
                and not secret_source.get(manifest.api_key_env)):
            logger.warning(
                "mcp: api_key_env=%r set on server_id=%r but no source holds "
                "it -- requests go unauthenticated until one does",
                manifest.api_key_env, manifest.server_id,
            )
        self._client = httpx.AsyncClient(
            timeout=manifest.timeout_s,
            headers=headers,
            # A server mounted at /mcp answers /mcp/ with a 307; httpx does
            # not follow by default, and the handshake died on the redirect
            # body.  We post the exact URL below so this is a fallback, not
            # the mechanism.  httpx drops Authorization on a cross-host
            # redirect, so following one cannot leak the bearer.
            follow_redirects=True,
        )

    @property
    def session_id(self) -> str:
        """The session the server issued, or empty before the handshake."""
        return self._session_id

    async def send_rpc(self, envelope: dict) -> dict:
        body = await self._post(envelope)
        if envelope.get("method") == "initialize" and "result" in body:
            # The server is ready for work only once it has been told the
            # handshake completed.  Best-effort: a server that does not care
            # must not cost us the session.
            try:
                await self._post(
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    notification=True,
                )
            except (MCPTransportError, MCPProtocolError):
                logger.warning(
                    "mcp: server_id=%r did not accept notifications/initialized "
                    "-- continuing",
                    self._manifest.server_id,
                )
        return body

    async def _post(self, envelope: dict, *, notification: bool = False) -> dict:
        req_headers: dict = await _auth_headers(self._manifest, self._bearer_resolver)
        if self._session_id:
            req_headers[SESSION_ID_HEADER] = self._session_id
            req_headers[PROTOCOL_VERSION_HEADER] = PROTOCOL_VERSION
        method = envelope.get("method", "?")
        try:
            response = await self._client.post(
                self._manifest.url, json=envelope, headers=req_headers,
            )
        except httpx.TimeoutException as exc:
            raise MCPTransportError(
                f"server_id={self._manifest.server_id!r}: timeout "
                f"calling {method}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MCPTransportError(
                f"server_id={self._manifest.server_id!r}: transport error "
                f"calling {method}: {exc}"
            ) from exc

        issued = response.headers.get(SESSION_ID_HEADER)
        if issued:
            self._session_id = issued

        if response.status_code >= 500:
            raise MCPTransportError(
                f"server_id={self._manifest.server_id!r}: HTTP "
                f"{response.status_code} from {method}"
            )
        if notification:
            # A notification is answered 202 with no body; nothing to decode.
            return {}
        return self._decode(response)

    def _decode(self, response) -> dict:
        """Read the JSON-RPC response out of a JSON body or an SSE stream."""
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/event-stream"):
            payload = self._last_sse_payload(response.text)
            if payload is None:
                raise MCPProtocolError(
                    f"server_id={self._manifest.server_id!r}: event stream "
                    f"carried no JSON-RPC message"
                )
            return payload
        try:
            body = response.json()
        except Exception as exc:
            raise MCPProtocolError(
                f"server_id={self._manifest.server_id!r}: non-JSON response "
                f"(HTTP {response.status_code})"
            ) from exc
        if not isinstance(body, dict):
            raise MCPProtocolError(
                f"server_id={self._manifest.server_id!r}: response is not a "
                f"JSON object"
            )
        return body

    def _last_sse_payload(self, text: str):
        """The last ``data:`` frame that decodes to a JSON object.

        A stream may carry progress notifications before the answer; the
        response to our request is the one that ends it.
        """
        found = None
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            chunk = line[len("data:"):].strip()
            if not chunk:
                continue
            try:
                candidate = json.loads(chunk)
            except ValueError:
                continue
            if isinstance(candidate, dict):
                found = candidate
        return found

    async def close(self) -> None:
        # The spec ends a session with a DELETE; a server that does not
        # implement it is not a problem worth surfacing.
        if self._session_id:
            try:
                await self._client.delete(
                    self._manifest.url,
                    headers={SESSION_ID_HEADER: self._session_id},
                )
            except Exception:
                logger.debug(
                    "mcp: session DELETE failed for %r", self._manifest.server_id,
                )
        try:
            await self._client.aclose()
        except Exception:  # pragma: no cover -- defensive
            logger.exception(
                "mcp: streamable-http aclose failed for %r",
                self._manifest.server_id,
            )


async def _auth_headers(manifest: Any, bearer_resolver: Any) -> dict[str, str]:
    """The ``Authorization`` header for one request, resolved now.

    ``auth: oauth`` mints through the credential broker for the requester of the
    task being served; with no broker the call is **refused** -- until F3 it went
    out unauthenticated and said nothing. A static ``api_key_env`` is read from
    the secret source per request, so a rotated credential is used on the next
    call without a restart.
    """
    if manifest.auth == "oauth":
        if bearer_resolver is None:
            raise MCPTransportError(
                f"server_id={manifest.server_id!r} declares auth: oauth, and no "
                f"credential broker is configured for this agent -- refusing "
                f"rather than sending the request unauthenticated"
            )
        token = await bearer_resolver()
        return {"Authorization": f"Bearer {token}"} if token else {}
    if manifest.api_key_env:
        key = secret_source.get(manifest.api_key_env)
        if key:
            return {"Authorization": f"Bearer {key}"}
    return {}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def build_transport(manifest: MCPManifest) -> Transport:
    """Construct the right Transport for *manifest.transport*.

    Adding a new transport: implement the Protocol, dispatch here.
    The :class:`MCPClient` knows nothing about the concrete classes
    beyond the Protocol surface.

    An ``auth: oauth`` manifest gets the live credential broker's resolver
    here, so every construction path is covered (F3): it mints per request for
    the person whose task the call serves.
    """
    resolver = None
    if manifest.auth == "oauth":
        from acc.credentials.live import bearer_resolver  # noqa: PLC0415

        resolver = bearer_resolver(manifest.oauth_provider)
    if manifest.transport == "http":
        return HTTPTransport(manifest, bearer_resolver=resolver)
    if manifest.transport == "streamable-http":
        return StreamableHTTPTransport(manifest, bearer_resolver=resolver)
    if manifest.transport == "stdio":
        return StdioTransport(manifest)
    raise NotImplementedError(
        f"unknown MCP transport {manifest.transport!r} "
        f"(server_id={manifest.server_id!r})"
    )
