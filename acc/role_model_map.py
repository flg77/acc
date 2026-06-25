"""Role → Model mapping engine (operator goal 2026-06-22; locked design).

The canonical per-role model assignment for a ``CollectiveSpec``: which model_id
(from ``models.yaml``) each role's agents run on.  Persists as ``AgentSpec.model``
in ``collective.yaml`` — the locked decision (Configuration pane is the canonical
surface; this is its engine).  Pure + side-effect-free over an in-memory spec, so
it's unit-tested and reused by both the Configuration table and the Assistant.

Default policy = the locked split (PR-MM3 pattern): the strongest model for the
control + review roles, cheaper workers for everything else.
"""
from __future__ import annotations

from typing import Any, Optional

# The control + review roles that get the strongest model by default
# (operator decision 2026-06-22 §7.4).  Deliberately NOT all 7 control roles:
# ingester/observer are substrate and fine on a cheap model.
STRONG_ROLES: frozenset[str] = frozenset(
    {"assistant", "reviewer", "orchestrator", "compliance_officer", "arbiter"}
)
_UNSET = (None, "", "(default)", "(mixed)")


def _norm(model_id: Optional[str]) -> Optional[str]:
    return None if model_id in _UNSET else model_id


def role_model_rows(spec: Any, models: Optional[list[Any]] = None) -> list[dict]:
    """One row per distinct role in the collective: ``{role, model_id, label, n_agents}``.

    ``model_id`` is the role's assigned model, ``(default)`` when unset, or
    ``(mixed)`` when its agents disagree.  ``label`` resolves from the models
    registry when provided.
    """
    by_role: dict[str, list] = {}
    for a in spec.agents:
        by_role.setdefault(a.role, []).append(a)
    labels = {m.model_id: getattr(m, "label", "") for m in (models or [])}
    rows: list[dict] = []
    for role in sorted(by_role):
        mids = {getattr(a, "model", None) or None for a in by_role[role]}
        if len(mids) == 1:
            mid = next(iter(mids))
            shown = mid or "(default)"
        else:
            mid, shown = None, "(mixed)"
        rows.append({"role": role, "model_id": shown,
                     "label": labels.get(mid, ""), "n_agents": len(by_role[role])})
    return rows


def assign_role_model(spec: Any, role: str, model_id: Optional[str]) -> int:
    """Set ``.model`` on every agent whose ``role`` matches.

    ``None`` / ``""`` / ``"(default)"`` clears the override (role falls back to
    the collective/global default model).  Returns the number of agents changed.
    """
    mid = _norm(model_id)
    n = 0
    for a in spec.agents:
        if a.role == role:
            a.model = mid
            n += 1
    return n


def seed_split_defaults(spec: Any, strong: str, worker: str,
                        strong_roles: frozenset[str] = STRONG_ROLES) -> dict[str, str]:
    """Apply the default split: ``strong`` model to control/review roles, ``worker``
    to the rest.  Mutates ``spec`` in place; returns the applied ``{role: model_id}``."""
    applied: dict[str, str] = {}
    for a in spec.agents:
        mid = strong if a.role in strong_roles else worker
        a.model = mid
        applied[a.role] = mid
    return applied
