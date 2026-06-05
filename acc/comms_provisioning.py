"""Derive a role's NATS pub/sub permissions from its manifest (P4).

Under uniform packaging a role's bus identity must *follow the role* —
provisioned from its declared `allowed_actions` + `domain_id` rather than
the hand-maintained static matrix in ``acc/nats_permissions.yaml``. This
module is the pure derivation; wiring it into install/infuse (provision on
infuse, revoke on uninstall) is gated behind ``ACC_NKEY_DYNAMIC`` and stays
OFF by default — the static matrix remains authoritative until proven.

Subject globs mirror ``acc/nats_permissions.yaml`` (second token = the
collective id, written ``*`` so one matrix serves every collective).
"""

from __future__ import annotations

import os
from typing import Any

# Every role may speak these (the worker baseline).
_BASE_PUBLISH = (
    "acc.*.heartbeat",
    "acc.*.register",
    "acc.*.task.complete",
    "acc.*.task.progress",
    "acc.*.queue.*",
    "acc.*.backpressure.*",
    "acc.*.alert",
)
_BASE_SUBSCRIBE = (
    "acc.*.task.assign",
    "acc.*.task.cancel",
    "acc.*.role.update",
    "acc.*.config.reload",
)

# allowed_action -> additional publish subject(s) it unlocks.
_ACTION_PUBLISH = {
    "publish_eval_outcome": ("acc.*.eval.*",),
    "publish_knowledge_share": ("acc.*.knowledge.*",),
    "publish_episode_nominate": ("acc.*.episode.nominate",),
    "publish_alert_escalate": ("acc.*.alert",),
}

# Cross-collective bridge subjects — only for roles that may route/delegate.
_BRIDGE = (
    "acc.bridge.*.*.delegate",
    "acc.bridge.*.*.result",
    "acc.bridge.*.pending",
)

# Arbiter-only control subjects a packaged role must NEVER be granted by
# derivation (A-011/A-012/A-016 invariants). Used by tests + as a guard.
ARBITER_ONLY = (
    "acc.*.plan.*",
    "acc.*.centroid",
    "acc.*.role.assign",
)


def dynamic_provisioning_enabled() -> bool:
    """Whether to derive NATS perms at install/infuse (default: False)."""
    return os.environ.get("ACC_NKEY_DYNAMIC", "").lower() in ("1", "true", "yes")


def derive_role_permissions(role_def: Any) -> dict[str, list[str]]:
    """Map a role definition to ``{"publish": [...], "subscribe": [...]}``.

    ``role_def`` may be a dict (the ``role_definition`` block) or any object
    exposing ``allowed_actions`` / ``domain_id`` / ``can_route``.
    """
    if isinstance(role_def, dict):
        actions = set(role_def.get("allowed_actions") or [])
        domain = role_def.get("domain_id") or ""
        can_route = bool(role_def.get("can_route"))
    else:
        actions = set(getattr(role_def, "allowed_actions", []) or [])
        domain = getattr(role_def, "domain_id", "") or ""
        can_route = bool(getattr(role_def, "can_route", False))

    publish: set[str] = set(_BASE_PUBLISH)
    subscribe: set[str] = set(_BASE_SUBSCRIBE)

    for action in actions:
        for subj in _ACTION_PUBLISH.get(action, ()):
            publish.add(subj)

    if can_route:
        publish.update(_BRIDGE)
        subscribe.update(_BRIDGE)

    if domain:
        # Paracrine: receive domain-broadcast signals for this domain.
        subscribe.add(f"acc.*.domain.{domain}.>")

    # Hard floor: derivation never grants arbiter-only control subjects.
    publish -= set(ARBITER_ONLY)

    return {"publish": sorted(publish), "subscribe": sorted(subscribe)}
