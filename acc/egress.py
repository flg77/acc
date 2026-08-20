"""Brokered egress: a destination policy, and credentials the agent never holds.

The scope question comes first, and it decides the design. On a cluster, network
policy and an egress proxy already enforce *where* traffic may go, and doing it
again in ACC would be a second, weaker firewall — one that an agent with a socket
can simply bypass.

What the substrate cannot do is the other half: **inject a credential at the
boundary, chosen by ACC's role model.** A NetworkPolicy has no idea which role is
calling or which key that role is entitled to. So this module owns the half ACC
uniquely can, and treats destination policy as a *legibility and defence-in-depth*
layer rather than a claim to be the enforcement boundary.

That distinction is stated rather than implied, because a security feature that
overstates what it enforces is worse than one that does less and says so:

* **Where enforcement really lives** is the substrate (NetworkPolicy, egress
  proxy, the OpenShell sandbox's own controls). ACC's check catches the honest
  mistake and makes it diagnosable.
* **What ACC genuinely enforces** is that the credential is not in the agent's
  environment. It cannot be exfiltrated from a process that never had it.

Default deny, opt-in, and a refusal is recorded and legible — a refused
destination that presents as a mysterious timeout costs more to diagnose than
the policy saves.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import yaml

logger = logging.getLogger("acc.egress")

POLICY_PATH_VAR = "ACC_EGRESS_POLICY"
DEFAULT_POLICY_FILE = "egress-policy.yaml"
ENABLE_VAR = "ACC_EGRESS_BROKER"


class EgressError(Exception):
    """A request was refused. The message is operator-facing."""


class EgressDenied(EgressError):
    """The destination is not permitted for this role."""


@dataclass(frozen=True)
class Destination:
    """One permitted destination for a role.

    Attributes:
        host: hostname or glob (``api.example.com``, ``*.example.com``).
        scheme: permitted scheme; ``https`` by default because a policy that
            silently permits plaintext is not the one anyone thought they wrote.
        credential_env: the variable whose value is injected at the boundary.
            The agent never sees it.
        header: the header the credential is placed in.
        prefix: value prefix, e.g. ``Bearer ``.
    """

    host: str
    scheme: str = "https"
    credential_env: str = ""
    header: str = "Authorization"
    prefix: str = "Bearer "

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        if not parsed.hostname:
            return False
        if self.scheme and parsed.scheme != self.scheme:
            return False
        return fnmatch.fnmatch(parsed.hostname.lower(), self.host.lower())


@dataclass
class Policy:
    """Destination policy, per role. Absent role means deny everything."""

    roles: dict[str, list[Destination]] = field(default_factory=dict)

    def destinations(self, role: str) -> list[Destination]:
        return list(self.roles.get(role, []))

    def find(self, role: str, url: str) -> Destination | None:
        for destination in self.destinations(role):
            if destination.matches(url):
                return destination
        return None


@dataclass(frozen=True)
class Decision:
    """Whether a request may proceed, and why not when it may not."""

    allowed: bool
    role: str
    url: str
    reason: str = ""
    destination: Destination | None = None
    at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "role": self.role,
            "url": self.url,
            "reason": self.reason,
            "host": urlparse(self.url).hostname or "",
            "at": self.at,
        }


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def policy_path(repo_root: Path | None = None) -> Path:
    raw = os.environ.get(POLICY_PATH_VAR, "").strip()
    if raw:
        return Path(raw)
    root = repo_root or Path(__file__).resolve().parent.parent
    return root / DEFAULT_POLICY_FILE


def load_policy(repo_root: Path | None = None) -> Policy:
    """Read the destination policy.

    An unreadable policy yields an EMPTY one, which denies everything. Failing
    open here would mean a corrupt file silently removes the control — the
    opposite of what a policy file is for.
    """
    path = policy_path(repo_root)
    if not path.is_file():
        return Policy()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.error(
            "egress: policy unreadable (%s) — denying all brokered egress", exc
        )
        return Policy()

    roles: dict[str, list[Destination]] = {}
    for role, entries in (raw.get("roles") or {}).items():
        destinations: list[Destination] = []
        for item in entries or []:
            if isinstance(item, str):
                destinations.append(Destination(host=item))
            elif isinstance(item, dict) and item.get("host"):
                destinations.append(
                    Destination(
                        host=str(item["host"]),
                        scheme=str(item.get("scheme", "https")),
                        credential_env=str(item.get("credential_env", "")),
                        header=str(item.get("header", "Authorization")),
                        prefix=str(item.get("prefix", "Bearer ")),
                    )
                )
        roles[str(role)] = destinations
    return Policy(roles=roles)


def enabled(environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return str(env.get(ENABLE_VAR, "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

#: Every decision made this process, for the durable record and for `doctor`.
_JOURNAL: list[Decision] = []


def journal() -> list[Decision]:
    return list(_JOURNAL)


def clear_journal() -> None:
    _JOURNAL.clear()


def check(
    role: str, url: str, *, repo_root: Path | None = None
) -> Decision:
    """Decide whether *role* may reach *url*. Records the decision either way.

    A refusal is recorded and legible. A refused destination presenting as a
    mysterious timeout costs far more to diagnose than the policy saves.
    """
    policy = load_policy(repo_root)
    parsed = urlparse(url)
    now = time.time()

    if not parsed.hostname:
        decision = Decision(False, role, url, "not an absolute URL", None, now)
    elif role not in policy.roles:
        decision = Decision(
            False, role, url,
            f"role {role!r} has no egress policy — default deny", None, now,
        )
    else:
        destination = policy.find(role, url)
        if destination is None:
            permitted = ", ".join(d.host for d in policy.destinations(role)) or "(none)"
            decision = Decision(
                False, role, url,
                f"{parsed.hostname} is not permitted for {role!r}; allowed: {permitted}",
                None, now,
            )
        else:
            decision = Decision(True, role, url, "", destination, now)

    _JOURNAL.append(decision)
    if not decision.allowed:
        logger.warning("egress: DENIED %s -> %s (%s)", role, url, decision.reason)
    return decision


def headers_for(
    decision: Decision, *, environ: dict[str, str] | None = None
) -> dict[str, str]:
    """The headers to add at the boundary, including any injected credential.

    Reads the credential HERE, in the broker, from the environment the broker
    runs in. The value is never returned to a caller that is not making the
    request, and never written anywhere.

    Raises:
        EgressError: the destination needs a credential that is not present.
            Better than sending an unauthenticated request and reporting the
            provider's 401 as an ACC failure.
    """
    if not decision.allowed or decision.destination is None:
        raise EgressDenied(decision.reason or "destination not permitted")

    destination = decision.destination
    if not destination.credential_env:
        return {}

    env = environ if environ is not None else os.environ
    value = str(env.get(destination.credential_env, "")).strip()
    if not value:
        raise EgressError(
            f"{destination.host} needs {destination.credential_env}, which is not "
            f"set where the broker runs. The agent does not hold this credential "
            f"by design — provision it for the broker."
        )
    return {destination.header: f"{destination.prefix}{value}"}


def request_via_broker(
    role: str,
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    timeout_s: float = 30.0,
    repo_root: Path | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    """Make a brokered request. The caller never sees the credential.

    Raises:
        EgressDenied: the destination is not permitted for this role.
        EgressError: a required credential is absent, or the request failed.
    """
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    decision = check(role, url, repo_root=repo_root)
    if not decision.allowed:
        raise EgressDenied(decision.reason)

    headers = headers_for(decision, environ=environ)
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:  # noqa: BLE001
        raise EgressError(f"{url}: {type(exc).__name__}: {exc}") from exc


def credentials_withheld_from_agent(
    role: str, *, repo_root: Path | None = None
) -> list[str]:
    """Credential names the broker holds for *role* that the agent must not.

    Consumed by the credential-scoping layer: a name here is one the agent
    should NOT have, precisely because the broker injects it instead.
    """
    policy = load_policy(repo_root)
    return sorted(
        {
            d.credential_env
            for d in policy.destinations(role)
            if d.credential_env
        }
    )
