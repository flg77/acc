"""Give an agent only the credentials its role actually needs.

Every agent container receives the whole environment file, so every agent holds
every credential in the deployment — including for providers its role never
talks to. For a runtime positioned on least-privilege execution that is the
weakest answer in the set: one compromised agent yields the operator's entire
credential inventory.

Enforcement here is at the **receiving** end, not the delivery end. That choice
is what makes it work everywhere: compose passes an ``env_file``, the operator
mounts a Secret, an edge box exports variables from a shell — but whatever
delivered the environment, the agent process drops what its role does not need
before anything else reads it. No deployment topology has to change for the
scoping to hold.

What a role needs is **derived**, not declared:

* every model in the role's chain contributes its ``api_key_env``;
* the working-memory password and the arbiter keys are collective-wide
  infrastructure, not per-role provider credentials, so they are never scoped
  away;
* anything the operator names in an allowlist, for the cases derivation cannot
  see (a skill that calls a third-party API with its own key).

That last point is the honest limit of this: derivation knows about model
bindings, and a capability with its own credential is invisible to it. So
scoping is **opt-in** and there is a command to show exactly what it would
remove before anyone turns it on — a security feature that silently breaks a
working deployment is one that gets switched off and never revisited.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger("acc.secret_scope")

#: Turns enforcement on. Off by default: see the module docstring — derivation
#: cannot see a capability's own credential, so this must be verified with
#: ``acc-cli secrets scope`` before being enabled.
ENABLE_VAR = "ACC_SCOPE_SECRETS"

#: Comma-separated names kept regardless of derivation, for what derivation
#: cannot see.
ALLOWLIST_VAR = "ACC_SECRET_ALLOWLIST"

#: Credentials that belong to the collective rather than to a role's provider
#: bindings. Scoping these away would break every agent, and they are not what
#: the blast-radius argument is about — that is about one agent holding another
#: provider's key.
INFRASTRUCTURE = frozenset(
    {
        "REDIS_PASSWORD",
        "ACC_REDIS_PASSWORD",
        "ACC_ARBITER_SIGNING_KEY",
        "ACC_ARBITER_VERIFY_KEY",
        "NATS_PASSWORD",
        "ACC_NATS_PASSWORD",
    }
)


@dataclass(frozen=True)
class RoleScope:
    """What one role needs, and why."""

    role: str
    required: frozenset[str] = frozenset()
    reasons: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "required": sorted(self.required),
            "reasons": dict(sorted(self.reasons.items())),
        }


def _allowlist(environ: dict[str, str] | None = None) -> set[str]:
    env = environ if environ is not None else os.environ
    raw = str(env.get(ALLOWLIST_VAR, "") or "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def credential_names() -> set[str]:
    """Every credential name ACC knows about.

    The union of what the model registry references and what the configuration
    schema marks secret. Anything outside this set is not treated as a
    credential at all — scoping must never remove an ordinary variable such as
    ``ACC_COLLECTIVE_ID`` just because a role does not "need" it.
    """
    names: set[str] = set()
    try:
        from acc.models import load_models  # noqa: PLC0415

        for entry in load_models():
            if entry.api_key_env:
                names.add(entry.api_key_env.strip())
    except Exception:  # pragma: no cover — a broken registry is reported elsewhere
        logger.debug("secret_scope: model registry unreadable", exc_info=True)
    try:
        from acc import configschema as schema  # noqa: PLC0415

        for key in schema.schema():
            if key.secret and key.path.startswith("env."):
                names.add(key.path.split(".", 1)[1])
    except Exception:  # pragma: no cover
        logger.debug("secret_scope: schema unavailable", exc_info=True)
    return {n for n in names if n}


def scope_for(role: str, *, environ: dict[str, str] | None = None) -> RoleScope:
    """The credentials *role* requires, with a reason for each.

    Reasons matter: this is reviewed by an operator deciding whether it is safe
    to enable, and "because the registry says so" is not reviewable.
    """
    required: set[str] = set()
    reasons: dict[str, str] = {}

    try:
        from acc.models import load_models, load_role_chains  # noqa: PLC0415

        registry = {m.model_id: m for m in load_models()}
        chain = load_role_chains().get(role, [])
        for position, model_id in enumerate(chain):
            entry = registry.get(model_id)
            if entry is None or not entry.api_key_env:
                continue
            name = entry.api_key_env.strip()
            required.add(name)
            where = "primary" if position == 0 else f"fallback #{position}"
            # A fallback's credential is required too: a chain whose secondary
            # has no key provides no failover, which is only discovered during
            # the outage it was meant to survive.
            reasons.setdefault(name, f"{where} model {model_id!r}")
    except Exception:  # pragma: no cover
        logger.debug("secret_scope: cannot derive from bindings", exc_info=True)

    for name in sorted(INFRASTRUCTURE):
        required.add(name)
        reasons.setdefault(name, "collective infrastructure")

    for name in sorted(_allowlist(environ)):
        required.add(name)
        reasons.setdefault(name, f"operator allowlist ({ALLOWLIST_VAR})")

    return RoleScope(role=role, required=frozenset(required), reasons=reasons)


def would_remove(role: str, environ: dict[str, str] | None = None) -> list[str]:
    """Credential names present in *environ* that *role* does not require."""
    env = dict(environ if environ is not None else os.environ)
    scope = scope_for(role, environ=env)
    known = credential_names()
    return sorted(
        name
        for name in known
        if name in env and str(env[name]).strip() and name not in scope.required
    )


def enabled(environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return str(env.get(ENABLE_VAR, "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def scrub(role: str, environ: dict[str, str] | None = None) -> list[str]:
    """Remove credentials *role* does not need. Returns the names removed.

    Mutates the mapping in place — for the real environment that is the point:
    anything read later, by any library, sees only what the role is entitled
    to. A no-op unless enforcement is switched on.

    Never logs a value; the removed **names** are logged because that is what
    an operator needs to confirm the scoping did what they reviewed.
    """
    env = environ if environ is not None else os.environ
    if not enabled(env):
        return []
    removed = would_remove(role, env)
    for name in removed:
        try:
            del env[name]
        except KeyError:  # pragma: no cover — concurrent mutation
            pass
    if removed:
        logger.info(
            "secret_scope: role %r does not use %d credential(s); removed: %s",
            role, len(removed), ", ".join(removed),
        )
    return removed


def report(roles: Iterable[str] | None = None) -> list[dict[str, object]]:
    """Per-role scope plus what enforcement would remove, for review."""
    if roles is None:
        try:
            from acc.models import load_role_chains  # noqa: PLC0415

            roles = sorted(load_role_chains())
        except Exception:  # pragma: no cover
            roles = []
    out: list[dict[str, object]] = []
    for role in roles:
        scope = scope_for(role)
        data = scope.as_dict()
        data["would_remove"] = would_remove(role)
        out.append(data)
    return out
