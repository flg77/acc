"""Where ACC is running: a checkout, or a pod the operator made (proposal 056).

One helper answers "checkout or cluster pod" so every checkout-only
affordance (authoring a role on disk, staging a package install, adding a
workspace catalog) can say so instead of degrading silently.

A cluster pod is one the operator built: it sets ``ACC_CORPUS_NAME`` on
every UI and agent container (``operator/internal/reconcilers/ui/``), and
``ACC_DEPLOY_MODE`` on the TUI.  ``ACC_DEPLOY_MODE`` alone also counts when
it names a cluster profile — a hand-rolled k8s deployment without the
operator still has no checkout.  A lighthouse-style podman checkout runs
with ``deploy_mode: edge`` and no corpus name, and is a checkout.
"""

from __future__ import annotations

import os

_CLUSTER_MODES = frozenset({"k8s", "rhoai", "operator"})

_cached: bool | None = None


def is_cluster() -> bool:
    """True when this process runs in a cluster pod rather than a checkout.

    Read once and cached — the env does not change under a running process.
    """
    global _cached
    if _cached is None:
        mode = os.environ.get("ACC_DEPLOY_MODE", "").strip().lower()
        _cached = mode in _CLUSTER_MODES or bool(
            os.environ.get("ACC_CORPUS_NAME", "").strip()
        )
    return _cached


def _reset() -> None:
    """Forget the cached answer (tests change the env between cases)."""
    global _cached
    _cached = None
