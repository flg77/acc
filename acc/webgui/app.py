"""acc-webgui FastAPI application factory + entry point.

`create_app()` builds the FastAPI app: it starts one `NATSObserver`
per observed collective (via `ObserverHub`), exposes the read REST +
WebSocket surface, and serves the compiled React SPA as static files.
`main()` is the `acc-webgui` console-script entry point.

Action endpoints (PR-3), tracing endpoints (PR-4), and auth (PR-5)
register onto this same app.
"""

from __future__ import annotations

import contextlib
import logging
import os

logger = logging.getLogger("acc.webgui")

_DEFAULT_NATS_URL = "nats://localhost:4222"
_DEFAULT_HOST = "127.0.0.1"  # localhost by default — never an open port
_DEFAULT_PORT = 8080


def _collective_ids() -> list[str]:
    """Resolve observed collectives from the environment (TUI semantics)."""
    raw = os.environ.get("ACC_COLLECTIVE_IDS", "").strip()
    if raw:
        return [c.strip() for c in raw.split(",") if c.strip()]
    single = os.environ.get("ACC_COLLECTIVE_ID", "").strip()
    return [single] if single else ["sol-01"]


def _static_dir() -> str | None:
    """Path to the compiled React assets, if present in the image.

    PR-6's Containerfile copies the Vite `dist/` output to
    ``acc/webgui/static``.  In a dev checkout the directory is absent
    and the API still runs (the React dev server proxies to it).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    static = os.path.join(here, "static")
    return static if os.path.isdir(static) else None


def create_app():
    """Build and return the acc-webgui FastAPI application."""
    from fastapi import Depends, FastAPI

    from acc.webgui.observers import ObserverHub
    from acc.webgui import (
        auth, routes_action, routes_auth, routes_read, routes_trace, ws,
    )

    nats_url = os.environ.get("ACC_NATS_URL", _DEFAULT_NATS_URL)
    collective_ids = _collective_ids()
    nkey_seed = os.environ.get("ACC_NKEY_SEED_PATH") if (
        os.environ.get("ACC_NKEY_ENABLED", "").strip().lower()
        in ("1", "true", "yes", "on")
    ) else None

    hub = ObserverHub(nats_url, collective_ids, nkey_seed_path=nkey_seed)

    @contextlib.asynccontextmanager
    async def lifespan(app):  # noqa: ANN001
        await hub.start()
        try:
            yield
        finally:
            await hub.stop()

    app = FastAPI(
        title="acc-webgui",
        description="Optional web frontend for ACC — feature parity with "
                    "acc-tui plus enhanced tracing.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.hub = hub
    app.state.auth_config = auth.resolve_auth_config()
    logger.info("webgui: auth mode = %s", app.state.auth_config.mode)

    # /health is intentionally open (liveness probe); the data, tracing,
    # and action surfaces are gated.  Read + tracing need the viewer
    # role; actions need the operator role (proposal §4.7).
    #
    # routes_auth (/api/login + /api/auth-info) is itself ungated — it
    # IS the front door — and must register before the SPA static mount.
    app.include_router(routes_auth.router)
    app.include_router(routes_read.router)  # gates its own data endpoints
    app.include_router(routes_trace.router, dependencies=[Depends(auth.require_viewer)])
    app.include_router(routes_action.router)  # each endpoint requires operator
    app.include_router(ws.router)

    static = _static_dir()
    if static is not None:
        from fastapi.staticfiles import StaticFiles
        # The SPA: serve index.html for any unmatched path (client-side
        # routing).  Mounted last so /api and /ws win.
        app.mount("/", StaticFiles(directory=static, html=True), name="spa")
        logger.info("webgui: serving React SPA from %s", static)
    else:
        logger.info("webgui: no static dir — API-only (use the Vite dev server)")

    return app


def main() -> None:  # pragma: no cover - process entry point
    """`acc-webgui` console-script entry point."""
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from acc.webgui import auth

    host = os.environ.get("ACC_WEBGUI_HOST", _DEFAULT_HOST)
    port = int(os.environ.get("ACC_WEBGUI_PORT", str(_DEFAULT_PORT)))
    # Refuse a non-loopback bind with no authentication configured.
    auth.enforce_bind_safety(host, auth.resolve_auth_config())
    uvicorn.run(create_app(), host=host, port=port, ws="websockets")


if __name__ == "__main__":  # pragma: no cover
    main()
