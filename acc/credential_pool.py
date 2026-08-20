"""Several credentials for one provider, rotated on throttle.

A single API key is a single point of failure with a rate limit attached. When
it throttles, every role bound to that provider stops — even though the account
next to it has quota to spare.

This is the credential-level sibling of the model failover chain, and the same
distinction decides everything: **a throttle is transient, an auth fault is
not.** Rotating past a 401 is the dangerous case, because it looks like it
worked — the pool quietly runs on three of four keys, and nobody learns the
fourth was revoked until renewal.

Three rules follow from that:

* **Names only.** The pool holds environment variable *names* and health state.
  Nothing here reads, prints, stores or copies a credential value. A pool that
  handled values would be a new place for them to leak.
* **Cooldown, not exclusion.** A throttled key is rested and retried. Removing
  it would turn a busy hour into a permanent capacity reduction.
* **Observable.** ``status`` shows what is healthy, what is resting and what is
  broken, because a pool that silently masks a dead key is how an operator
  finds out at renewal that only one of four ever worked.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from acc._atomic_write import atomic_write_text

logger = logging.getLogger("acc.credential_pool")

POOLS_PATH_VAR = "ACC_CREDENTIAL_POOLS_PATH"
DEFAULT_POOLS_FILE = "credential-pools.yaml"

#: Where cooldown state lives (open question 2). A file beside the pool
#: definition, not Redis: cooldown is per-host because rate limits are per
#: process-group in practice, and a pool that needs working memory to function
#: would make credentials depend on a service that itself needs credentials.
STATE_FILE = ".credential-pool-state.json"

DEFAULT_COOLDOWN_S = 300.0


class CredentialPoolError(Exception):
    """A pool operation was refused. The message is operator-facing."""


class Health:
    HEALTHY = "healthy"
    COOLING = "cooling"
    FAULTED = "faulted"


@dataclass
class Entry:
    """One credential in a pool — by name, never by value."""

    env_var: str
    health: str = Health.HEALTHY
    until: float = 0.0          # cooldown expiry (monotonic-independent wall clock)
    reason: str = ""
    uses: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "env_var": self.env_var,
            "health": self.health,
            "until": self.until,
            "reason": self.reason,
            "uses": self.uses,
        }


@dataclass
class Pool:
    """The credentials available for one provider."""

    provider: str
    entries: list[Entry] = field(default_factory=list)
    cooldown_s: float = DEFAULT_COOLDOWN_S

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "cooldown_s": self.cooldown_s,
            "entries": [e.as_dict() for e in self.entries],
        }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(status_code: int | None, message: str = "") -> str:
    """Is this a throttle, an auth fault, or neither?

    The distinction the whole module turns on. A 429 rests a key; a 401 must be
    *reported*, because rotating past it hides a revoked credential behind
    apparently normal operation.
    """
    text = (message or "").lower()
    if status_code == 429 or "rate limit" in text or "too many requests" in text:
        return "throttle"
    if status_code in (401, 403) or any(
        marker in text for marker in ("unauthor", "forbidden", "invalid api key", "revoked")
    ):
        return "auth"
    if status_code is not None and 500 <= status_code < 600:
        # A provider-side failure is not the credential's fault; resting the key
        # would blame it for an outage it did not cause.
        return "server"
    return "other"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def pools_path(repo_root: Path | None = None) -> Path:
    raw = os.environ.get(POOLS_PATH_VAR, "").strip()
    if raw:
        return Path(raw)
    root = repo_root or Path(__file__).resolve().parent.parent
    return root / DEFAULT_POOLS_FILE


def state_path(repo_root: Path | None = None) -> Path:
    return pools_path(repo_root).with_name(STATE_FILE)


def load_pools(repo_root: Path | None = None) -> dict[str, Pool]:
    """Read pool definitions and merge in persisted health state."""
    path = pools_path(repo_root)
    pools: dict[str, Pool] = {}
    if not path.is_file():
        return pools
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.error("credential pools: cannot read %s (%s)", path, exc)
        return pools

    for provider, body in (raw.get("pools") or {}).items():
        if isinstance(body, list):
            names, cooldown = body, DEFAULT_COOLDOWN_S
        elif isinstance(body, dict):
            names = body.get("env_vars") or []
            cooldown = float(body.get("cooldown_s", DEFAULT_COOLDOWN_S))
        else:
            continue
        pools[str(provider)] = Pool(
            provider=str(provider),
            entries=[Entry(env_var=str(n)) for n in names if str(n).strip()],
            cooldown_s=cooldown,
        )

    _merge_state(pools, repo_root)
    return pools


def _merge_state(pools: dict[str, Pool], repo_root: Path | None) -> None:
    path = state_path(repo_root)
    if not path.is_file():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    now = time.time()
    for provider, entries in (state or {}).items():
        pool = pools.get(provider)
        if pool is None:
            continue
        by_name = {e.env_var: e for e in pool.entries}
        for name, saved in (entries or {}).items():
            entry = by_name.get(name)
            if entry is None:
                continue
            entry.uses = int(saved.get("uses", 0) or 0)
            entry.reason = str(saved.get("reason", "") or "")
            health = str(saved.get("health", Health.HEALTHY))
            until = float(saved.get("until", 0) or 0)
            # An expired cooldown is simply over — a key resting yesterday is
            # healthy today, and re-reading stale state must not keep it down.
            if health == Health.COOLING and until <= now:
                entry.health, entry.until, entry.reason = Health.HEALTHY, 0.0, ""
            else:
                entry.health, entry.until = health, until


def save_state(pools: dict[str, Pool], repo_root: Path | None = None) -> None:
    """Persist health only. Definitions stay in the operator's file."""
    state = {
        provider: {
            e.env_var: {
                "health": e.health,
                "until": e.until,
                "reason": e.reason,
                "uses": e.uses,
            }
            for e in pool.entries
        }
        for provider, pool in pools.items()
    }
    atomic_write_text(
        state_path(repo_root),
        json.dumps(state, indent=2, sort_keys=True),
        mode=0o600,   # health state names credentials; keep it owner-only
        newline="",
    )


def save_pools(pools: dict[str, Pool], repo_root: Path | None = None) -> Path:
    body = yaml.safe_dump(
        {
            "pools": {
                provider: {
                    "env_vars": [e.env_var for e in pool.entries],
                    "cooldown_s": pool.cooldown_s,
                }
                for provider, pool in sorted(pools.items())
            }
        },
        sort_keys=False,
    )
    header = (
        "# Credential pools: several credentials for one provider, rotated on\n"
        "# throttle. These are environment variable NAMES -- no credential value\n"
        "# is ever stored here or read by ACC's pool logic.\n"
    )
    path = pools_path(repo_root)
    atomic_write_text(path, header + body, mode=0o644, newline="")
    return path


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def available(pool: Pool, *, environ: dict[str, str] | None = None) -> list[Entry]:
    """Entries that are healthy and actually present in the environment."""
    env = environ if environ is not None else os.environ
    now = time.time()
    out: list[Entry] = []
    for entry in pool.entries:
        if entry.health == Health.COOLING and entry.until > now:
            continue
        if entry.health == Health.FAULTED:
            continue
        if not str(env.get(entry.env_var, "")).strip():
            continue
        out.append(entry)
    return out


def select(
    pool: Pool, *, environ: dict[str, str] | None = None
) -> Entry | None:
    """The next credential to use — least-used first, for even wear.

    Returns the variable NAME to read, not a value. The caller reads the
    environment itself; nothing here touches the secret.
    """
    candidates = available(pool, environ=environ)
    if not candidates:
        return None
    return min(candidates, key=lambda e: e.uses)


def record_success(pool: Pool, entry: Entry) -> None:
    entry.uses += 1
    if entry.health == Health.COOLING:
        entry.health, entry.until, entry.reason = Health.HEALTHY, 0.0, ""


def record_failure(
    pool: Pool, entry: Entry, *, status_code: int | None, message: str = ""
) -> str:
    """Apply the outcome of a failed call. Returns the classification."""
    kind = classify(status_code, message)
    if kind == "throttle":
        entry.health = Health.COOLING
        entry.until = time.time() + pool.cooldown_s
        entry.reason = f"throttled ({status_code or 'rate limit'})"
    elif kind == "auth":
        # NOT rotated past silently: a revoked key that keeps being skipped is
        # discovered at renewal, when it is far more expensive to find out.
        entry.health = Health.FAULTED
        entry.until = 0.0
        entry.reason = f"authentication rejected ({status_code or 'auth'})"
        logger.error(
            "credential pool %s: %s was rejected by the provider — this is a "
            "configuration fault, not a transient failure",
            pool.provider, entry.env_var,
        )
    # server / other: the credential is not at fault; leave its health alone.
    return kind


# ---------------------------------------------------------------------------
# Administration
# ---------------------------------------------------------------------------


def add(provider: str, env_var: str, repo_root: Path | None = None) -> Pool:
    pools = load_pools(repo_root)
    pool = pools.setdefault(provider, Pool(provider=provider))
    if any(e.env_var == env_var for e in pool.entries):
        raise CredentialPoolError(f"{env_var} is already in the {provider!r} pool")
    pool.entries.append(Entry(env_var=env_var))
    save_pools(pools, repo_root)
    return pool


def remove(provider: str, env_var: str, repo_root: Path | None = None) -> bool:
    pools = load_pools(repo_root)
    pool = pools.get(provider)
    if pool is None:
        return False
    before = len(pool.entries)
    pool.entries = [e for e in pool.entries if e.env_var != env_var]
    if len(pool.entries) == before:
        return False
    save_pools(pools, repo_root)
    return True


def reset(provider: str, repo_root: Path | None = None) -> int:
    """Clear cooldowns and faults for a provider. Returns entries cleared."""
    pools = load_pools(repo_root)
    pool = pools.get(provider)
    if pool is None:
        raise CredentialPoolError(f"no pool for {provider!r}")
    cleared = 0
    for entry in pool.entries:
        if entry.health != Health.HEALTHY:
            entry.health, entry.until, entry.reason = Health.HEALTHY, 0.0, ""
            cleared += 1
    save_state(pools, repo_root)
    return cleared


def status(
    provider: str | None = None,
    *,
    repo_root: Path | None = None,
    environ: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Health per credential, by name. Never a value.

    ``present`` is reported separately from ``health`` because "configured but
    the variable is empty" and "rested after a throttle" are different problems
    with different fixes.
    """
    env = environ if environ is not None else os.environ
    now = time.time()
    out: list[dict[str, Any]] = []
    for name, pool in sorted(load_pools(repo_root).items()):
        if provider and name != provider:
            continue
        for entry in pool.entries:
            remaining = max(0.0, entry.until - now) if entry.health == Health.COOLING else 0.0
            out.append(
                {
                    "provider": name,
                    "env_var": entry.env_var,
                    "present": bool(str(env.get(entry.env_var, "")).strip()),
                    "health": entry.health,
                    "reason": entry.reason,
                    "cooldown_remaining_s": round(remaining, 1),
                    "uses": entry.uses,
                }
            )
    return out
