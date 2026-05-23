"""Self-reflective memory consolidation (PR-MEM1).

A background routine periodically reviews an agent's recent episodes,
clusters the related ones, and uses the LLM to write a compact, durable
**memory note** per cluster — a distilled lesson ("PDFs >10MB reliably
exhaust the ingester") that improves future retrieval without re-reading
every raw episode.

Hot-path-safe by construction:

* **Writes** (clustering + LLM summarisation + LanceDB insert + Redis
  push) run OUT of band in the reflection loop (PR-MEM2) — never on the
  task path.
* **Durable** notes live in a SEPARATE small ``memory_notes`` LanceDB
  table (fast vector search over few curated rows).
* A **Redis per-role hot-cache** holds the top-N note summaries for an
  O(1) read on the prompt-build path (PR-MEM3).

Notes are excluded from their own clustering (no notes-of-notes).
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from acc.signals import redis_memory_notes_key

logger = logging.getLogger("acc.memory_reflection")

_MEMORY_NOTE_SIGNAL = "MEMORY_NOTE"


@dataclass
class MemoryNote:
    summary: str
    agent_id: str
    role_label: str
    source_count: int
    confidence: float = 0.0
    note_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)
    embedding: list[float] = field(default_factory=list)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _episode_excerpt(ep: dict, limit: int = 200) -> str:
    """Best-effort human excerpt from an episode's payload_json."""
    raw = ep.get("payload_json") or ""
    text = raw
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            text = str(obj.get("content") or obj.get("output") or obj.get("text") or raw)
    except (json.JSONDecodeError, TypeError):
        pass
    text = " ".join(str(text).split())
    return text[:limit]


def _cluster_episodes(
    episodes: list[dict], threshold: float,
) -> list[list[dict]]:
    """Greedy single-pass cosine clustering on episode embeddings.

    No external dependency.  Episodes without embeddings each form their
    own singleton (so they aren't force-merged).  Order-stable.
    """
    clusters: list[dict] = []  # {centroid, members}
    for ep in episodes:
        emb = ep.get("embedding") or []
        placed = False
        if emb:
            best_i, best_sim = -1, threshold
            for i, c in enumerate(clusters):
                if not c["centroid"]:
                    continue
                sim = _cosine(emb, c["centroid"])
                if sim >= best_sim:
                    best_i, best_sim = i, sim
            if best_i >= 0:
                clusters[best_i]["members"].append(ep)
                placed = True
        if not placed:
            clusters.append({"centroid": list(emb), "members": [ep]})
    return [c["members"] for c in clusters]


def _summary_prompt(members: list[dict]) -> str:
    lines = [f"- {_episode_excerpt(m)}" for m in members]
    return (
        "Summarise the RECURRING lesson across these past episodes into "
        "ONE durable memory note (1-2 sentences, specific + reusable). "
        "Return only the note text, no preamble.\n\n" + "\n".join(lines)
    )


async def consolidate(
    agent_id: str,
    role_label: str,
    episodes: list[dict],
    llm: Any,
    *,
    max_notes: int = 5,
    cluster_threshold: float = 0.6,
    min_cluster: int = 2,
) -> list[MemoryNote]:
    """Cluster *episodes* + LLM-summarise each cluster into a MemoryNote.

    Best-effort + pure (no I/O beyond the supplied ``llm``): a summary or
    embedding failure skips that note rather than raising.  Excludes
    prior MEMORY_NOTE episodes so reflection never feeds on itself.
    """
    source = [e for e in episodes if e.get("signal_type") != _MEMORY_NOTE_SIGNAL]
    if not source:
        return []
    clusters = [c for c in _cluster_episodes(source, cluster_threshold)
                if len(c) >= min_cluster]
    # Largest (most-recurring) clusters first; cap the count.
    clusters.sort(key=len, reverse=True)
    clusters = clusters[:max_notes]

    notes: list[MemoryNote] = []
    for members in clusters:
        try:
            resp = await llm.complete(
                "You distil an agent's experience into durable memory notes.",
                _summary_prompt(members),
            )
        except Exception as exc:
            logger.warning("memory_reflection: summary LLM call failed: %s", exc)
            continue
        summary = str(
            (resp.get("content") or resp.get("text") or "") if isinstance(resp, dict)
            else resp
        ).strip()
        if not summary:
            continue
        try:
            embedding = await llm.embed(summary)
        except Exception:
            embedding = []
        notes.append(MemoryNote(
            summary=summary,
            agent_id=agent_id,
            role_label=role_label,
            source_count=len(members),
            confidence=min(1.0, len(members) / (min_cluster * 2)),
            embedding=list(embedding or []),
        ))
    return notes


def persist_notes(notes: list[MemoryNote], vector: Any) -> int:
    """Insert notes into the ``memory_notes`` LanceDB table.  Best-effort;
    returns the count written (0 on any failure)."""
    if not notes or vector is None or not hasattr(vector, "insert"):
        return 0
    rows = [{
        "id": n.note_id,
        "agent_id": n.agent_id,
        "role_label": n.role_label,
        "ts": n.ts,
        "summary": n.summary,
        "source_count": int(n.source_count),
        "confidence": float(n.confidence),
        "embedding": n.embedding or [0.0] * 384,
    } for n in notes]
    try:
        vector.insert("memory_notes", rows)
        return len(rows)
    except Exception as exc:
        logger.warning("memory_reflection: persist failed: %s", exc)
        return 0


def write_hot_cache(
    redis_client: Any,
    collective_id: str,
    role_label: str,
    notes: list[MemoryNote],
    *,
    top_n: int = 3,
    ttl_s: int = 21600,
) -> bool:
    """Push the top-N note summaries to the Redis per-role hot-cache for
    O(1) prompt-build reads.  Best-effort; returns success."""
    if redis_client is None:
        return False
    key = redis_memory_notes_key(collective_id, role_label)
    payload = json.dumps([n.summary for n in notes[:top_n]])
    try:
        redis_client.set(key, payload)
        redis_client.expire(key, ttl_s)
        return True
    except Exception as exc:
        logger.warning("memory_reflection: hot-cache write failed: %s", exc)
        return False


def read_hot_cache(
    redis_client: Any, collective_id: str, role_label: str,
) -> list[str]:
    """Read the role's memory-note summaries from the Redis hot-cache.

    O(1); returns ``[]`` on miss or ANY error — the prompt build must
    never block or raise on memory."""
    if redis_client is None:
        return []
    key = redis_memory_notes_key(collective_id, role_label)
    try:
        raw = redis_client.get(key)
    except Exception:
        return []
    if raw is None:
        return []
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    try:
        notes = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(n) for n in notes] if isinstance(notes, list) else []
