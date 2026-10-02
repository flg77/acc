"""Writing a credential into the secret source -- the web GUI's Credentials page.

``20260926-secrets-from-kubernetes-and-a-live-broker`` Phase 3 (UX-07). Phase 1
taught the agents to read credentials from a mounted directory at call time;
this is the other end: a person types a value once, masked, and it lands where
the agents read -- never on the bus, in a transcript, a journal or a log.

Two targets, one per deployment shape, each an explicit opt-in:

* **directory** (``ACC_SECRET_WRITE_DIR``) -- the edge. The production compose
  mounts one named volume read-write into the web GUI and read-only into every
  agent at ``/var/run/acc/secrets``; a file written here is read on the agent's
  next call.
* **secret** (``ACC_SECRET_WRITE_SECRET``) -- a cluster. The operator sets it to
  the Secret ``AgentCorpus.spec.secretMount`` names, and lets the UI
  ServiceAccount ``patch`` that one Secret (``resourceNames``-scoped) and nothing
  else. The kubelet refreshes the agents' mount within about a minute.

Write-only by design: nothing here reads a value back, and the cluster grant is
``patch`` without ``get``, so the web GUI cannot read the Secret it writes. Every
error names the credential and the target, never the value.
"""

from __future__ import annotations

import base64
import json
import os
import re
import ssl
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DIR_VAR = "ACC_SECRET_WRITE_DIR"
SECRET_VAR = "ACC_SECRET_WRITE_SECRET"

DIRECTORY = "directory"
SECRET = "secret"

#: A Secret holds at most 1 MiB in all; a single credential is far smaller.
MAX_BYTES = 64 * 1024

# An environment-variable name: what every reader of the source asks for, and
# a valid Secret key. Anything else could walk the filesystem.
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,252}$")


class SecretWriteError(Exception):
    """A write was refused or failed. The message never carries the value."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Target:
    """Where a write from this process goes -- or why nothing can be written."""

    kind: str = ""
    where: str = ""
    reason: str = ""

    @property
    def writable(self) -> bool:
        return bool(self.kind)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "where": self.where,
                "writable": self.writable, "reason": self.reason}


def _env(environ: dict[str, str] | None) -> dict[str, str] | os._Environ:
    return environ if environ is not None else os.environ


def target(environ: dict[str, str] | None = None) -> Target:
    from acc.deploy import environment  # noqa: PLC0415

    env = _env(environ)
    here = environment()
    secret = str(env.get(SECRET_VAR, "") or "").strip()
    if secret:
        where = f"Secret {secret}" + (f" in namespace {here.namespace}" if here.namespace else "")
        return Target(SECRET, where)
    directory = str(env.get(DIR_VAR, "") or "").strip()
    if directory:
        return Target(DIRECTORY, directory)
    if here.cluster:
        return Target(reason=(
            "this corpus mounts no Secret into its agents -- set spec.secretMount on "
            "the AgentCorpus; the operator then lets this page write that one Secret"
        ))
    return Target(reason=(
        f"no secrets volume is mounted into the web GUI ({DIR_VAR} is unset) -- "
        "the production compose mounts acc-secrets from v0.26.0"
    ))


def check(name: str, value: object) -> str:
    """Refuse a malformed name or value; return the value as text."""
    if not _NAME.match(name or ""):
        raise SecretWriteError(
            f"{name!r} is not a credential name -- letters, digits and _, "
            "not starting with a digit (the environment-variable name the agents read)"
        )
    if not isinstance(value, str) or not value:
        raise SecretWriteError(f"no value for {name}")
    if "\x00" in value:
        raise SecretWriteError(f"the value for {name} contains a NUL byte")
    if len(value.encode("utf-8")) > MAX_BYTES:
        raise SecretWriteError(f"the value for {name} is over {MAX_BYTES // 1024} KiB")
    return value


def write(name: str, value: object, *, environ: dict[str, str] | None = None) -> Target:
    """Write *value* as credential *name* into this process's target."""
    text = check(name, value)
    where = target(environ)
    if where.kind == DIRECTORY:
        _write_file(Path(str(_env(environ).get(DIR_VAR)).strip()), name, text)
    elif where.kind == SECRET:
        _patch_secret(str(_env(environ).get(SECRET_VAR)).strip(), name, text)
    else:
        raise SecretWriteError(where.reason, status=409)
    return where


def _write_file(root: Path, name: str, value: str) -> None:
    # Written beside the target and renamed over it, so an agent reading at call
    # time sees the old value or the new one, never half of one. The temporary
    # name starts with a dot: secret_source.names() skips those.
    try:
        fd, tmp = tempfile.mkstemp(dir=root, prefix=f".{name}.", suffix=".tmp")
    except OSError as exc:
        raise SecretWriteError(
            f"cannot write into {root} ({type(exc).__name__}) -- is the secrets "
            "volume mounted read-write into the web GUI?", status=500,
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        # Owner and group: the agents run as the same user on the edge.
        os.chmod(tmp, 0o640)
        os.replace(tmp, root / name)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise SecretWriteError(f"writing {name} into {root} failed ({type(exc).__name__})",
                               status=500) from exc


def _patch_secret(secret: str, name: str, value: str) -> None:
    from acc.deploy import environment  # noqa: PLC0415
    from acc.deployment import ClusterReadError, _api_base, _sa_dir  # noqa: PLC0415

    namespace = environment().namespace
    if not namespace:
        raise SecretWriteError("this pod does not know its namespace (no ServiceAccount mount)",
                               status=500)
    try:
        base = _api_base()
        token = (_sa_dir() / "token").read_text(encoding="utf-8").strip()
    except (ClusterReadError, OSError) as exc:
        raise SecretWriteError(f"cannot reach the Kubernetes API from here ({exc})",
                               status=500) from exc
    # A merge patch touches one key and leaves the others as they are.
    body = json.dumps({"data": {name: base64.b64encode(value.encode("utf-8")).decode("ascii")}})
    request = urllib.request.Request(
        f"{base}/api/v1/namespaces/{namespace}/secrets/{secret}",
        data=body.encode("utf-8"), method="PATCH",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/merge-patch+json",
                 "Accept": "application/json"},
    )
    context = None
    if base.startswith("https://"):
        ca = _sa_dir() / "ca.crt"
        context = ssl.create_default_context(cafile=str(ca)) if ca.is_file() else ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=8.0, context=context):  # noqa: S310
            return
    except urllib.error.HTTPError as exc:
        # The response body is not read: an API error may quote the request.
        if exc.code in (401, 403):
            raise SecretWriteError(
                f"this pod's ServiceAccount may not patch Secret {secret} in {namespace} "
                f"(HTTP {exc.code}) -- the ACC operator grants that from 0.2.28, for the "
                "Secret spec.secretMount names", status=403,
            ) from exc
        if exc.code == 404:
            raise SecretWriteError(
                f"Secret {secret} does not exist in {namespace} -- create it first; the "
                "agents mount it as required and do not start without it", status=409,
            ) from exc
        raise SecretWriteError(f"the cluster refused {name} into Secret {secret} (HTTP {exc.code})",
                               status=502) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise SecretWriteError(f"cannot reach the Kubernetes API ({type(exc).__name__})",
                               status=502) from exc
