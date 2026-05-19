"""Authentication endpoints for acc-webgui — login + auth-mode probe.

`POST /api/login` is the `htpasswd`-mode login: it verifies a
username/password against the configured Apache htpasswd file and
mints a short-lived signed session JWT (see `acc.webgui.auth`).
`GET /api/auth-info` is an unauthenticated probe so the SPA can render
the correct login gate before it holds any credential.

Both routes are registered before the SPA static mount and are
themselves ungated — they ARE the front door.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from acc.webgui import auth

logger = logging.getLogger("acc.webgui.routes_auth")

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


@router.get("/api/auth-info", tags=["meta"])
def auth_info(request: Request) -> dict:
    """Report the active auth mode so the SPA picks the right gate.

    Unauthenticated by design — the mode is not a secret and the
    frontend needs it *before* it can authenticate.
    """
    return {"mode": request.app.state.auth_config.mode}


@router.post("/api/login", tags=["auth"])
def login(body: LoginRequest, request: Request) -> dict:
    """`htpasswd`-mode login → a signed session token.

    404 in any other mode (not a probe surface); 401 on bad creds —
    no unknown-user vs. wrong-password distinction.
    """
    cfg: auth.AuthConfig = request.app.state.auth_config
    if cfg.mode != auth.MODE_HTPASSWD:
        raise HTTPException(status_code=404, detail="not found")
    if not cfg.htpasswd_path:
        logger.error("webgui: htpasswd mode but ACC_WEBGUI_HTPASSWD_PATH unset")
        raise HTTPException(status_code=500, detail="server misconfigured")
    if not auth.verify_htpasswd(body.username, body.password, cfg.htpasswd_path):
        raise HTTPException(status_code=401, detail="invalid credentials")
    role = auth.role_for(body.username, cfg)
    token = auth.mint_session_jwt(body.username, role, cfg)
    logger.info("webgui: login ok user=%r role=%s", body.username, role)
    return {"token": token, "user": body.username, "role": role}
