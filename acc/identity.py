"""Who is asking — resolved from the substrate, never invented here.

ACC runs in places that already know who a user is, and they are better at it
than ACC would be:

* **OpenShift / Kubernetes** — real RBAC. A ServiceAccount, a user, groups, and
  a cluster that already decides what they may do.
* **Edge** — system authentication. The OS user the process runs as, and the
  groups it belongs to.
* **Web** — the oauth2-proxy / Keycloak session the web GUI already resolves.

So ACC does **not** define a fourth identity model. It resolves a
:class:`Principal` from whichever substrate it is running on and maps that onto
one of its own tiers. That is the same call made for egress: where the platform
already enforces something, ACC consumes it rather than building a second,
weaker version that has to be kept in step.

The part ACC does own is **what a principal may ask an agent to do** — the
substrate has no opinion on whether this person may spend a collective's token
budget, and no way to express it. That mapping lives here, keyed by an identity
the substrate vouched for.

External requesters (a chat account, an inbound webhook) are a separate case:
no substrate vouches for them, so they are **default deny** and must be admitted
by an explicit operator action.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from acc._atomic_write import atomic_write_text

logger = logging.getLogger("acc.identity")

ACCESS_PATH_VAR = "ACC_ACCESS_PATH"
DEFAULT_ACCESS_FILE = "access.yaml"


class Tier:
    """What a principal may do. Ordered; higher includes lower."""

    NONE = "none"
    VIEWER = "viewer"        # read state
    REQUESTER = "requester"  # may ask for work
    OPERATOR = "operator"    # may approve, configure, admit others


_RANK = {Tier.NONE: 0, Tier.VIEWER: 1, Tier.REQUESTER: 2, Tier.OPERATOR: 3}


def satisfies(have: str, need: str) -> bool:
    return _RANK.get(have, 0) >= _RANK.get(need, 0)


# ---------------------------------------------------------------------------
# Category ceiling (D-014; `20260823-attributed-memory` [1b])
# ---------------------------------------------------------------------------
#
# Tiers say *may you ask, may you approve*. They say nothing about *how far*
# the work you asked for may go, so a Cat-C-capable role reachable from a chat
# channel by anyone was not an expressible constraint. The ceiling is the
# missing axis: the highest risk an invocation or proposal may carry when this
# principal asked for it, on the same LOW < MEDIUM < HIGH < CRITICAL scale a
# role's ``max_*_risk_level`` uses, so the floor rule
#
#     effective = role grants ∩ principal ceiling
#
# is a min() on one scale. It defaults from the tier and an admission may only
# narrow it. Above the ceiling a call is refused outright -- never escalated,
# never asked -- because a human approving it would be approving what the
# admission forbade. The ceiling is a floor under human judgement, not a gate.

CEILING_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

TIER_CEILING = {
    Tier.NONE: "LOW",
    Tier.VIEWER: "LOW",
    Tier.REQUESTER: "MEDIUM",
    Tier.OPERATOR: "CRITICAL",
}

TASK_CEILING_KEY = "requester_ceiling"


def tier_ceiling(tier: str) -> str:
    """The ceiling a tier carries when the admission did not narrow it."""
    return TIER_CEILING.get(str(tier or "").lower(), "LOW")


def narrower(a: str, b: str) -> str:
    """The lower of two ceilings; an empty one does not count."""
    a, b = str(a or "").upper(), str(b or "").upper()
    if a not in CEILING_RANK:
        return b if b in CEILING_RANK else ""
    if b not in CEILING_RANK:
        return a
    return a if CEILING_RANK[a] <= CEILING_RANK[b] else b


def exceeds_ceiling(risk: str, ceiling: str) -> bool:
    """True when *risk* is above *ceiling*. No ceiling means nothing exceeds it.

    ``UNACCEPTABLE`` (an assistant proposal's own top level) is above every
    ceiling; an unknown risk string ranks as LOW, matching the role guard.
    """
    c = str(ceiling or "").upper()
    if c not in CEILING_RANK:
        return False
    r = str(risk or "").upper()
    if r == "UNACCEPTABLE":
        return True
    return CEILING_RANK.get(r, 0) > CEILING_RANK[c]


def ceiling_of(task_payload: dict[str, Any] | None) -> str:
    """The ceiling stamped on a task, or ``""`` for an unattributed one.

    Read the explicit ``requester_ceiling`` first, then fall back to the tier
    (a payload from a publisher older than the ceiling). An explicit value is
    never wider than its tier allows. An unattributed task -- the operator's
    own surface, the arbiter's reconciliation -- carries no ceiling; the role's
    grants are the only limit there, as before.
    """
    if not isinstance(task_payload, dict):
        return ""
    tier = str(task_payload.get("requester_tier") or "").strip().lower()
    explicit = str(task_payload.get(TASK_CEILING_KEY) or "").strip().upper()
    if explicit in CEILING_RANK:
        return narrower(explicit, tier_ceiling(tier)) if tier else explicit
    if tier:
        return tier_ceiling(tier)
    return ""


class AccessError(Exception):
    """A request was refused. The message is operator-facing."""


@dataclass(frozen=True)
class Principal:
    """Someone asking ACC to do something.

    Attributes:
        subject: the identity, as the substrate spells it.
        source: which substrate vouched for it — ``kubernetes``, ``system``,
            ``web``, or ``external`` for one nothing vouches for.
        tier: what they may do.
        scope: where the request arrived — ``direct``, a channel id, or "".
            A direct message and a shared channel are different contexts and
            may carry different permissions.
        groups: substrate groups, used to map onto a tier.
        ceiling: an admission's narrowing of the tier's category ceiling, or
            "" for the tier default (see :func:`tier_ceiling`).
    """

    subject: str
    source: str
    tier: str = Tier.NONE
    scope: str = ""
    groups: tuple[str, ...] = ()
    ceiling: str = ""

    @property
    def effective_ceiling(self) -> str:
        """The highest risk work asked for by this principal may carry."""
        return narrower(self.ceiling, tier_ceiling(self.tier)) or tier_ceiling(self.tier)

    @property
    def vouched(self) -> bool:
        """Did a substrate authenticate this, or is it a claim?"""
        return self.source in ("kubernetes", "system", "web", "webgui")

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "source": self.source,
            "tier": self.tier,
            "scope": self.scope,
            "groups": list(self.groups),
            "vouched": self.vouched,
            "ceiling": self.effective_ceiling,
        }

    def attribution(self) -> str:
        """One string for a task payload and the audit record."""
        scope = f"@{self.scope}" if self.scope else ""
        return f"{self.source}:{self.subject}{scope}"


# ---------------------------------------------------------------------------
# Substrate resolution
# ---------------------------------------------------------------------------


def _kubernetes_principal() -> Principal | None:
    """The ServiceAccount this pod runs as, when running in a cluster.

    Presence of the projected token directory is the signal; ACC does not need
    to read the token to know which SA it is — the namespace and name are
    mounted beside it.
    """
    root = Path(
        os.environ.get(
            "ACC_SERVICEACCOUNT_DIR", "/var/run/secrets/kubernetes.io/serviceaccount"
        )
    )
    namespace_file = root / "namespace"
    if not namespace_file.is_file():
        return None
    try:
        namespace = namespace_file.read_text(encoding="utf-8").strip()
    except OSError:  # pragma: no cover
        return None
    name = os.environ.get("ACC_SERVICEACCOUNT_NAME", "default")
    return Principal(
        subject=f"system:serviceaccount:{namespace}:{name}",
        source="kubernetes",
        tier=Tier.OPERATOR,
        groups=("system:serviceaccounts", f"system:serviceaccounts:{namespace}"),
    )


def _system_principal() -> Principal:
    """The OS user, for edge deployments.

    The edge substrate is system authentication: whoever reached this process
    already got past the host's own controls.
    """
    user = (
        os.environ.get("ACC_SYSTEM_USER")
        or os.environ.get("USER")
        or os.environ.get("USERNAME")
        or "unknown"
    )
    groups: tuple[str, ...] = ()
    try:  # POSIX only
        import grp  # noqa: PLC0415
        import pwd  # noqa: PLC0415

        entry = pwd.getpwnam(user)
        groups = tuple(
            g.gr_name for g in grp.getgrall() if user in g.gr_mem
        ) + (grp.getgrgid(entry.pw_gid).gr_name,)
    except Exception:  # pragma: no cover — non-POSIX or unknown user
        pass
    return Principal(
        subject=user, source="system", tier=Tier.OPERATOR, groups=groups
    )


def current(*, environ: dict[str, str] | None = None) -> Principal:
    """The principal this process is acting as.

    Kubernetes first, then system. A local process is trusted to the extent the
    host trusts the user running it — which is the edge's stated posture, not a
    weakening of it.
    """
    env = environ if environ is not None else os.environ
    if str(env.get("ACC_IDENTITY_SOURCE", "")).strip().lower() == "system":
        return _system_principal()
    return _kubernetes_principal() or _system_principal()


def from_web(user: str, role: str, *, scope: str = "") -> Principal:
    """Adapt the web GUI's authenticated session onto a principal.

    The web surface already resolves an identity through oauth2-proxy and
    Keycloak; this maps its two roles onto the shared tiers rather than giving
    the browser a separate notion of who someone is.
    """
    tier = Tier.OPERATOR if role == "operator" else Tier.VIEWER
    # ``webgui`` as the source: it is the string the Web GUI stamps on its
    # tasks (``webgui:<user>``) and the memory-scope policy keys on.
    return Principal(subject=user, source="webgui", tier=tier, scope=scope)


# ---------------------------------------------------------------------------
# External requesters
# ---------------------------------------------------------------------------


@dataclass
class Grant:
    """An external requester an operator has admitted."""

    subject: str
    channel: str
    tier: str = Tier.REQUESTER
    scope: str = ""
    admitted_by: str = ""
    admitted_at: float = 0.0
    note: str = ""
    ceiling: str = ""  # "" = the tier's default; only ever narrower

    @property
    def effective_ceiling(self) -> str:
        return narrower(self.ceiling, tier_ceiling(self.tier)) or tier_ceiling(self.tier)

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "channel": self.channel,
            "tier": self.tier,
            "scope": self.scope,
            "admitted_by": self.admitted_by,
            "admitted_at": self.admitted_at,
            "note": self.note,
            "ceiling": self.ceiling,
        }


def access_path(repo_root: Path | None = None) -> Path:
    raw = os.environ.get(ACCESS_PATH_VAR, "").strip()
    if raw:
        return Path(raw)
    root = repo_root or Path(__file__).resolve().parent.parent
    return root / DEFAULT_ACCESS_FILE


def load_grants(repo_root: Path | None = None) -> list[Grant]:
    """Admitted external requesters.

    An unreadable file yields NO grants, which denies everything. Failing open
    would let a corrupt file silently admit the world.
    """
    path = access_path(repo_root)
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.error("identity: access file unreadable (%s) — denying all", exc)
        return []
    out: list[Grant] = []
    for item in (raw.get("grants") or []) if isinstance(raw, dict) else []:
        if not isinstance(item, dict) or not item.get("subject"):
            continue
        tier = str(item.get("tier", Tier.REQUESTER))
        if tier not in _RANK:
            tier = Tier.REQUESTER
        # A hand-edited ceiling wider than the tier allows is narrowed to the
        # tier's default, loudly: the file cannot widen what admit() refuses.
        ceiling = str(item.get("ceiling", "") or "").upper()
        if ceiling and ceiling != narrower(ceiling, tier_ceiling(tier)):
            logger.warning(
                "identity: grant %r on %r declares ceiling %s above the %s "
                "tier's %s — using the tier's",
                item["subject"], item.get("channel", ""), ceiling, tier,
                tier_ceiling(tier),
            )
            ceiling = ""
        out.append(
            Grant(
                subject=str(item["subject"]),
                channel=str(item.get("channel", "")),
                tier=tier,
                scope=str(item.get("scope", "")),
                admitted_by=str(item.get("admitted_by", "")),
                admitted_at=float(item.get("admitted_at", 0) or 0),
                note=str(item.get("note", "")),
                ceiling=ceiling if ceiling in CEILING_RANK else "",
            )
        )
    return out


def save_grants(grants: Iterable[Grant], repo_root: Path | None = None) -> Path:
    path = access_path(repo_root)
    header = (
        "# External requesters admitted to ask this collective for work.\n"
        "# Default is DENY: an identity absent from this file cannot cause a\n"
        "# task to run, whatever channel it arrives on.\n"
    )
    body = yaml.safe_dump(
        {"grants": [g.as_dict() for g in grants]}, sort_keys=False, allow_unicode=True
    )
    atomic_write_text(path, header + body, mode=0o644, newline="")
    return path


def admit(
    subject: str,
    channel: str,
    *,
    tier: str = Tier.REQUESTER,
    scope: str = "",
    admitted_by: str = "",
    note: str = "",
    ceiling: str = "",
    repo_root: Path | None = None,
) -> Grant:
    """Admit an external requester. An explicit operator action, recorded.

    ``ceiling`` narrows the tier's category ceiling for this admission; it
    can never widen it (D-014).

    Raises:
        AccessError: already admitted, an unknown tier, or a ceiling above
            the tier's own.
    """
    if tier not in _RANK:
        raise AccessError(f"unknown tier {tier!r}; known: {', '.join(sorted(_RANK))}")
    ceiling = str(ceiling or "").upper()
    if ceiling and ceiling not in CEILING_RANK:
        raise AccessError(
            f"unknown ceiling {ceiling!r}; known: {', '.join(CEILING_RANK)}"
        )
    if ceiling and ceiling != narrower(ceiling, tier_ceiling(tier)):
        raise AccessError(
            f"ceiling {ceiling} is above the {tier} tier's {tier_ceiling(tier)}; "
            f"an admission can only narrow a ceiling, never widen it"
        )
    if tier == Tier.OPERATOR:
        # An external identity nothing vouches for must not become an operator
        # by an allowlist entry. Approval authority stays with the substrate.
        raise AccessError(
            "an external requester cannot be granted the operator tier — "
            "operator authority comes from the substrate (cluster RBAC or "
            "system auth), not from an allowlist"
        )
    grants = load_grants(repo_root)
    if any(g.subject == subject and g.channel == channel for g in grants):
        raise AccessError(f"{subject!r} is already admitted on {channel!r}")
    grant = Grant(
        subject=subject, channel=channel, tier=tier, scope=scope,
        admitted_by=admitted_by, admitted_at=time.time(), note=note,
        ceiling=ceiling,
    )
    grants.append(grant)
    save_grants(grants, repo_root)
    logger.info("identity: admitted %s on %s (%s)", subject, channel, tier)
    return grant


def revoke(subject: str, channel: str, repo_root: Path | None = None) -> bool:
    """Revoke access. Effective immediately — grants are read per request."""
    grants = load_grants(repo_root)
    remaining = [
        g for g in grants if not (g.subject == subject and g.channel == channel)
    ]
    if len(remaining) == len(grants):
        return False
    save_grants(remaining, repo_root)
    logger.info("identity: revoked %s on %s", subject, channel)
    return True


def resolve_external(
    subject: str, channel: str, *, scope: str = "", repo_root: Path | None = None
) -> Principal:
    """The principal for an external requester. Default deny.

    Grants are read per call rather than cached, so a revocation takes effect
    on the next request without a restart.
    """
    for grant in load_grants(repo_root):
        if grant.subject != subject or grant.channel != channel:
            continue
        # A grant scoped to one place does not carry to another: a direct
        # message and a shared channel are different contexts.
        if grant.scope and scope and grant.scope != scope:
            continue
        return Principal(
            subject=subject, source="external", tier=grant.tier,
            scope=scope or grant.scope, ceiling=grant.ceiling,
        )
    return Principal(subject=subject, source="external", tier=Tier.NONE, scope=scope)


def require(principal: Principal, need: str) -> None:
    """Raise unless *principal* meets *need*.

    Raises:
        AccessError: with a message naming what is missing, so a refusal is
            diagnosable rather than a silent drop.
    """
    if satisfies(principal.tier, need):
        return
    if principal.tier == Tier.NONE:
        raise AccessError(
            f"{principal.subject!r} is not admitted on this channel. An operator "
            f"must admit them explicitly before they can ask for work."
        )
    raise AccessError(
        f"{principal.subject!r} has tier {principal.tier!r}; {need!r} is required"
    )
