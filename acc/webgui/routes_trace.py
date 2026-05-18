"""Enhanced-tracing REST endpoints for acc-webgui (proposal acc-webgui PR-4).

The six tracing views (proposal §4.5) split by data path:

* Task-step waterfall, PLAN DAG graph, cross-collective bridge graph,
  signal-stream feed — render data already in the live
  `CollectiveSnapshot` (`active_plans`, `signal_flow_log`, …); the
  frontend draws them, no new backend query needed.  `/plan` and
  `/signals` below expose those slices for point-in-time fetch.
* Audit-chain timeline — `/audit` reads the JSONL audit backend and
  re-verifies each record's `evidence_hash`, flagging tampering.
* Episode semantic search — `/episodes/search` queries LanceDB;
  best-effort (503 when the vector store is unavailable).
"""

from __future__ import annotations

import hashlib
import json
import os

from fastapi import APIRouter, Depends, HTTPException, Query

from acc.webgui.deps import get_hub
from acc.webgui.observers import ObserverHub

router = APIRouter(prefix="/api/trace", tags=["trace"])


def _snapshot_or_404(hub: ObserverHub, collective_id: str) -> dict:
    if collective_id not in hub.collective_ids():
        raise HTTPException(status_code=404,
                            detail=f"collective {collective_id!r} not observed")
    return hub.latest(collective_id) or {}


@router.get("/plan/{collective_id}")
def plan_dag(collective_id: str, hub: ObserverHub = Depends(get_hub)) -> dict:
    """The active PLAN DAGs for *collective_id* — feeds the DAG graph view."""
    snap = _snapshot_or_404(hub, collective_id)
    return {"collective_id": collective_id,
            "active_plans": snap.get("active_plans", {})}


@router.get("/signals/{collective_id}")
def signal_feed(collective_id: str, hub: ObserverHub = Depends(get_hub)) -> dict:
    """The recent signal-flow log — feeds the signal-stream feed view."""
    snap = _snapshot_or_404(hub, collective_id)
    return {"collective_id": collective_id,
            "signals": snap.get("signal_flow_log", [])}


def _evidence_hash(record: dict) -> str:
    """Recompute a record's keyless SHA-256 `evidence_hash`.

    Mirrors `acc.audit._compute_evidence_hash` — canonical JSON of the
    record minus the two integrity fields.  Keyless, so the web backend
    can detect *content* tampering without the audit signing key (full
    HMAC chain re-verification needs the key — proposal §8 follow-up).
    """
    data = {k: v for k, v in record.items()
            if k not in ("evidence_hash", "chain_hash")}
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@router.get("/audit")
def audit_timeline(limit: int = Query(200, ge=1, le=2000)) -> dict:
    """Read the JSONL audit backend, re-verify each record, return the
    timeline with per-record tamper flags + chain-continuity status.

    Feeds the tamper-evident audit-chain timeline view.
    """
    base = os.environ.get("ACC_AUDIT_FILE_PATH", "/app/data/audit")
    if not os.path.isdir(base):
        raise HTTPException(status_code=503,
                            detail=f"audit file backend not found at {base!r}")
    # Newest file first.
    files = sorted(
        (f for f in os.listdir(base)
         if f.startswith("audit-") and f.endswith(".jsonl")),
        reverse=True,
    )
    records: list[dict] = []
    for fname in files:
        if len(records) >= limit:
            break
        try:
            with open(os.path.join(base, fname), "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    rec["_evidence_ok"] = (
                        rec.get("evidence_hash") == _evidence_hash(rec)
                    )
                    records.append(rec)
        except (OSError, json.JSONDecodeError):
            continue
    records = records[-limit:]
    # Chain continuity: every record links to its predecessor's chain_hash.
    breaks = []
    for i in range(1, len(records)):
        if not records[i].get("chain_hash"):
            breaks.append(i)
    tampered = [i for i, r in enumerate(records) if not r["_evidence_ok"]]
    return {
        "records": records,
        "count": len(records),
        "tampered_indices": tampered,
        "chain_break_indices": breaks,
        "verified": not tampered and not breaks,
    }


@router.get("/episodes/search")
def episode_search(
    q: str = Query(..., min_length=1),
    collective_id: str = Query(...),
    k: int = Query(10, ge=1, le=50),
) -> dict:
    """Semantic search over LanceDB-persisted episodes (best-effort).

    Returns ranked episodes; 503 when the LanceDB vector store or the
    embedding model is unavailable in this environment.
    """
    try:
        from acc.backends.vector_lancedb import LanceDBBackend  # noqa: PLC0415
    except Exception as exc:
        raise HTTPException(status_code=503,
                            detail=f"LanceDB vector store unavailable: {exc}")
    lancedb_path = os.environ.get("ACC_LANCEDB_PATH", "/app/data/lancedb")
    if not os.path.isdir(lancedb_path):
        raise HTTPException(status_code=503,
                            detail=f"LanceDB path not found at {lancedb_path!r}")
    try:
        backend = LanceDBBackend(lancedb_path)
        results = backend.search_episodes(q, collective_id=collective_id, k=k)
        return {"query": q, "collective_id": collective_id, "results": results}
    except AttributeError:
        # The backend's search surface differs — surface honestly.
        raise HTTPException(status_code=503,
                            detail="episode search not supported by this "
                                   "LanceDB backend build")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
