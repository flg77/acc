"""Memory that belongs to the role, not the container (pure).

`20261003-assistant-orchestrated-infusion` Phase 5.  A dormant pool worker
boots with ``ACC_LANCEDB_PATH=/app/data/lancedb/worker-N``; when the arbiter
promotes it, everything the role learns lands in *that worker's* store.  Stop
the worker (phase 2's ``PROPOSE_LIFECYCLE:stop``) and re-infuse the role on
another one, and the role starts from nothing.

On promotion the worker now opens ``<base>/roles/<role>[--<cluster>]`` on the
same volume every pool worker already mounts, so the role's episodes and notes
follow the role across stop, start and re-infusion.  Two replicas of one role
share the path; concurrent appends from separate processes were verified on
LanceDB 0.39 (see the change's tasks §5).

What does NOT move: memory *scope*.  Records keep their requester scope and
ceiling (``acc/memory_scope.py``), and reads filter on them exactly as before,
so a shared role store does not widen who can read what.
"""

from __future__ import annotations

import os
import re

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")

#: Clusters that mean "no particular cluster" and so share the role's store.
_DEFAULT_CLUSTERS = frozenset({"", "default"})


def _safe(name: str) -> str:
    cleaned = _SAFE.sub("_", str(name or "").strip()).strip("._")
    return cleaned or "_"


def role_memory_enabled() -> bool:
    """On unless ``ACC_ROLE_MEMORY=0``."""
    return os.environ.get("ACC_ROLE_MEMORY", "1").strip().lower() not in ("0", "false", "no", "off")


def role_memory_path(worker_path: str, role: str, cluster_id: str = "") -> str:
    """The role's store beside the worker's: ``/app/data/lancedb/worker-3`` +
    ``devops_engineer`` → ``/app/data/lancedb/roles/devops_engineer``."""
    base = os.path.dirname(str(worker_path).rstrip("/")) or "."
    leaf = _safe(role)
    cluster = str(cluster_id or "").strip()
    if cluster not in _DEFAULT_CLUSTERS:
        leaf = f"{leaf}--{_safe(cluster)}"
    return os.path.join(base, "roles", leaf)
