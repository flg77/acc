"""acc-catalog FastAPI server — the writable catalog endpoint (thread 12).

A thin HTTP shell over :class:`acc.catalog.store.CatalogStore`.  All the
trust-critical logic (cosign verify before listing, promotion, indexing) lives
in the store; this module only:

* authenticates write requests with a bearer token,
* maps the ``acc-pkg publish`` PUT protocol onto ``store.stage``,
* serves ``index.json`` and the package artefacts for the resolver.

Run::

    acc-catalog        # console script → uvicorn

Configuration (env):

==============================  ===========================================
``ACC_CATALOG_ROOT``            data dir (default ``/var/lib/acc-catalog``)
``ACC_CATALOG_TIER``            tier tag for the index (default ``community``)
``ACC_CATALOG_PUBKEY``          cosign **public** key PEM → keypair verify
``ACC_CATALOG_SIGNER_ISSUER``   audit label for keypair mode (default
                                ``lab-keypair``); OIDC issuer for keyless
``ACC_CATALOG_SIGNER_SUBJECT``  keyless subject regex (default ``.*``)
``ACC_CATALOG_EC_POLICY``       EC policy YAML (default: none → empty policy)
``ACC_CATALOG_TOKEN``           bearer token required for uploads (unset ⇒
                                uploads disabled, reads still served)
``ACC_CATALOG_HOST``            bind host (default ``0.0.0.0`` — in-cluster,
                                fronted by Traefik + the bearer token)
``ACC_CATALOG_PORT``            bind port (default ``8080``)
==============================  ===========================================
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

# FastAPI is imported at module level (not lazily) so its type-based parameter
# resolution works under `from __future__ import annotations`: a lazily-imported
# `Request` annotation is unresolvable in the module globalns and FastAPI then
# mis-binds `request` as a query param (HTTP 422). This module is only imported
# when the `catalog` extra is installed (the acc-catalog console script / the
# image); acc.catalog.__init__ imports only the framework-agnostic store, so the
# unit tests never pull fastapi in.
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from acc.catalog.store import CatalogStore, RejectedUpload
from acc.pkg.catalog import RequiredSigner

logger = logging.getLogger("acc.catalog.server")

_DEFAULT_ROOT = "/var/lib/acc-catalog"
_DEFAULT_HOST = "0.0.0.0"  # noqa: S104 — server is gated by Traefik + bearer token
_DEFAULT_PORT = 8080


def _store_from_env() -> CatalogStore:
    root = Path(os.environ.get("ACC_CATALOG_ROOT", _DEFAULT_ROOT))
    tier = os.environ.get("ACC_CATALOG_TIER", "community").strip() or "community"
    pubkey = os.environ.get("ACC_CATALOG_PUBKEY", "").strip()
    issuer = os.environ.get("ACC_CATALOG_SIGNER_ISSUER", "lab-keypair").strip()
    subject = os.environ.get("ACC_CATALOG_SIGNER_SUBJECT", ".*").strip() or ".*"
    ec_policy = os.environ.get("ACC_CATALOG_EC_POLICY", "").strip()

    signer = RequiredSigner(
        issuer=issuer,
        subject_pattern=subject,
        key_path=pubkey,  # non-empty ⇒ keypair mode
    )
    return CatalogStore(
        root,
        required_signer=signer,
        tier=tier,
        ec_policy_path=Path(ec_policy) if ec_policy else None,
    )


def create_app(store: CatalogStore | None = None) -> FastAPI:
    """Build the acc-catalog FastAPI app."""
    store = store or _store_from_env()
    token = os.environ.get("ACC_CATALOG_TOKEN", "").strip()

    app = FastAPI(
        title="acc-catalog",
        summary="Writable, cosign-verifying ACC package catalog (marketplace P0)",
    )

    def _require_token(authorization: str | None) -> None:
        if not token:
            raise HTTPException(
                status_code=503,
                detail="uploads disabled: no ACC_CATALOG_TOKEN configured",
            )
        expected = f"Bearer {token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        return "ok"

    @app.get("/index.json")
    def index() -> JSONResponse:
        return JSONResponse(store.index())

    @app.get("/packages/{scope}/{filename}")
    def artefact(scope: str, filename: str) -> FileResponse:
        try:
            path = store.artefact_path(scope, filename)
        except RejectedUpload as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(path, media_type="application/octet-stream")

    @app.put("/upload/{filename}")
    async def upload(
        filename: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        _require_token(authorization)
        data = await request.body()
        try:
            result = store.stage(filename, data)
        except RejectedUpload as exc:
            # The signing-floor rejection path: log for audit, refuse the upload.
            logger.warning("rejected upload %s: %s %s", filename, exc, exc.detail)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # 201 when the upload completed a verifiable set and was promoted; 200
        # when it was merely staged (awaiting its .sig). NOTE: keep these within
        # the {200,201,204} set acc/pkg/publish.py:_http_put accepts.
        status = 201 if result.get("promoted") else 200
        return JSONResponse(result, status_code=status)

    return app


def main() -> None:  # pragma: no cover — process entry point
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    import uvicorn

    host = os.environ.get("ACC_CATALOG_HOST", _DEFAULT_HOST)
    port = int(os.environ.get("ACC_CATALOG_PORT", str(_DEFAULT_PORT)))
    logger.info("acc-catalog starting on %s:%d", host, port)
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    main()
