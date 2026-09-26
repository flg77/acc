"""The credential broker on the call path (lane F3).

:class:`~acc.credentials.broker.CredentialBroker` was complete and tested, and
nothing called it: an ``auth: oauth`` MCP manifest built its transport without a
``bearer_resolver`` and sent every request unauthenticated. This module is the
missing wiring.

* :func:`serving` marks, for the duration of one capability call, **whose task**
  is being served. A ContextVar, not an attribute: an agent runs tasks
  concurrently, and a token minted for one person must never ride another's call.
* :func:`bearer_resolver` is what the transport calls per request. It mints for
  the *person* behind the requester (:func:`acc.attribution.person_of` -- the same
  human asking from two surfaces is one token owner), and refuses with the command
  that fixes it when that person has not connected.

Tokens live in a :class:`~acc.credentials.broker.SealedFileStore` under
``ACC_CRED_DIR`` (default ``~/.config/acc/oauth``), sealed with ``ACC_CRED_KEY``,
which is read from the secret source like any other credential.
"""

from __future__ import annotations

import contextlib
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from acc import secret_source
from acc.credentials.broker import (
    CredentialBroker,
    NotConnectedError,
    ProviderConfig,
    SealedFileStore,
)

CRED_DIR_VAR = "ACC_CRED_DIR"
CRED_KEY_NAME = "ACC_CRED_KEY"
REDIRECT_VAR = "ACC_OAUTH_REDIRECT_URI"
DEFAULT_REDIRECT = "http://localhost:8765/oauth/callback"

_SERVING: ContextVar[str] = ContextVar("acc_serving_requester", default="")


@contextlib.contextmanager
def serving(requester: str) -> Iterator[None]:
    """Mark *requester* as the one whose task the enclosed calls serve."""
    token = _SERVING.set(str(requester or ""))
    try:
        yield
    finally:
        _SERVING.reset(token)


def current_person() -> str:
    """The person behind the requester being served, or ``""``."""
    from acc.attribution import UNATTRIBUTED, person_of  # noqa: PLC0415

    who = person_of(_SERVING.get())
    return "" if who in ("", UNATTRIBUTED) else who


def cred_dir() -> Path:
    raw = os.environ.get(CRED_DIR_VAR, "").strip()
    if raw:
        return Path(raw).expanduser()
    from acc.paths import user_config_dir  # noqa: PLC0415

    return user_config_dir() / "oauth"


def token_store() -> SealedFileStore:
    """The sealed store. Raises ``RuntimeError`` naming ``ACC_CRED_KEY`` when no
    source holds the key -- an unkeyed store must not be created silently."""
    return SealedFileStore(cred_dir(), key=secret_source.get(CRED_KEY_NAME) or None)


def broker_for(provider: str) -> CredentialBroker:
    return CredentialBroker(
        token_store(),
        {provider: ProviderConfig.from_env(provider)},
        redirect_uri=os.environ.get(REDIRECT_VAR, "").strip() or DEFAULT_REDIRECT,
    )


def _pending_path(provider: str, person: str) -> Path:
    import hashlib  # noqa: PLC0415

    digest = hashlib.sha256(f"{provider}\x00{person}".encode()).hexdigest()[:32]
    return cred_dir() / f"{digest}.pending"


def save_pending(provider: str, person: str, verifier: str, state: str) -> None:
    """Keep a started consent's PKCE verifier until ``complete`` -- sealed with the
    store's key, because a verifier plus the returned code is a token."""
    import json  # noqa: PLC0415

    store = token_store()  # refuses without ACC_CRED_KEY, before anything is written
    blob = store._fernet.encrypt(json.dumps({"verifier": verifier, "state": state}).encode())
    _pending_path(provider, person).write_bytes(blob)


def take_pending(provider: str, person: str) -> dict[str, str] | None:
    """The started consent for *person*, removed as it is read (single use)."""
    import json  # noqa: PLC0415

    path = _pending_path(provider, person)
    if not path.is_file():
        return None
    data = json.loads(token_store()._fernet.decrypt(path.read_bytes()).decode())
    path.unlink(missing_ok=True)
    return data


def bearer_resolver(provider: str) -> Any:
    """``async () -> str`` minting *provider*'s bearer for the person being served.

    Raises :class:`~acc.mcp.errors.MCPTransportError` -- which the dispatcher
    records as the call's error -- when there is no person, no key for the store,
    or no connection. Never an unauthenticated request.
    """
    async def _resolve() -> str:
        from acc.mcp.errors import MCPTransportError  # noqa: PLC0415

        who = current_person()
        if not who:
            raise MCPTransportError(
                f"{provider} needs a person's own consent, and this call serves "
                f"no attributed requester -- refusing"
            )
        try:
            return await broker_for(provider).mint(provider, who)
        except NotConnectedError:
            raise MCPTransportError(
                f"{provider} is not connected for {who}: run "
                f"`acc-cli oauth connect {provider} --for {who}` and consent in "
                f"{provider}'s own screen"
            ) from None
        except RuntimeError as exc:  # the store's missing-key refusal
            raise MCPTransportError(f"{provider}: {exc}") from None

    return _resolve
