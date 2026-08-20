"""Read across the whole collective instead of one container at a time.

"What happened to this task" is currently answered by opening a terminal per
agent and reading six container logs by eye, in the hope of spotting the same
task id in each. That is the question this module answers in one pass.

Two sources, deliberately kept distinct rather than blended:

* **container** — stdout/stderr from the runtime. Stack traces, library
  warnings, the raw truth about a crash.
* **tracelog** — ACC's own durable session record. Governance verdicts,
  Category evaluations, what an agent *decided*.

They answer different questions and an operator needs to know which one they
are reading, so every line carries its source. Merging them into an
undifferentiated stream would make a governance verdict look like a log line,
which is exactly the confusion to avoid.

**A missing source is reported, never fatal.** Half the collective being down is
precisely when someone runs this, and a command that fails because one
container is gone is useless at that moment.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

logger = logging.getLogger("acc.logs")

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_LEVEL_RANK = {name: i for i, name in enumerate(LEVELS)}

#: Matches the timestamp ACC's logging format emits, e.g.
#: ``2026-08-20 11:33:42,123 INFO acc.agent: ...``
_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:[.,](\d+))?")
_LEVEL_RE = re.compile(r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b")


@dataclass
class LogLine:
    """One line, from whichever source produced it."""

    ts: float | None
    source: str          # "container" | "tracelog"
    origin: str          # container name / session id
    level: str
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "time": (
                datetime.fromtimestamp(self.ts, tz=timezone.utc).isoformat()
                if self.ts
                else None
            ),
            "source": self.source,
            "origin": self.origin,
            "level": self.level,
            "text": self.text,
        }


@dataclass
class Query:
    """What to read, and what to keep.

    Attributes:
        role: only agents whose origin mentions this role.
        task: the highest-value filter — every line about one piece of work,
            across every agent that touched it.
        session: a specific session id.
        since_s: seconds back from now; None for everything available.
        level: minimum severity to keep.
        sources: which collectors to run.
    """

    role: str = ""
    task: str = ""
    session: str = ""
    since_s: float | None = None
    level: str = "DEBUG"
    sources: tuple[str, ...] = ("container", "tracelog")
    limit: int = 2000

    def keeps(self, line: LogLine) -> bool:
        if _LEVEL_RANK.get(line.level, 0) < _LEVEL_RANK.get(self.level, 0):
            return False
        if self.role and self.role not in line.origin:
            return False
        # The task filter matches the TEXT, not the origin: the whole point is
        # to follow one task across agents that are otherwise unrelated.
        if self.task and self.task not in line.text:
            return False
        if self.session and self.session not in (line.origin, line.text):
            return False
        return True


@dataclass
class Report:
    """Lines, plus an honest account of what could not be read."""

    lines: list[LogLine] = field(default_factory=list)
    unavailable: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "lines": [line.as_dict() for line in self.lines],
            "unavailable": dict(sorted(self.unavailable.items())),
            "count": len(self.lines),
        }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_line(raw: str, source: str, origin: str) -> LogLine:
    """Best-effort structure for a log line.

    A line that does not parse is still returned — with no timestamp and level
    INFO — because dropping unparseable lines loses exactly the output that
    matters most: tracebacks, which are multi-line and mostly unstructured.
    """
    text = raw.rstrip("\n")
    ts: float | None = None
    match = _TS_RE.search(text)
    if match:
        stamp = match.group(1).replace("T", " ")
        try:
            parsed = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
            ts = parsed.replace(tzinfo=timezone.utc).timestamp()
            if match.group(2):
                ts += float(f"0.{match.group(2)}")
        except ValueError:  # pragma: no cover — malformed stamp
            ts = None
    level_match = _LEVEL_RE.search(text)
    level = level_match.group(1) if level_match else "INFO"
    return LogLine(ts=ts, source=source, origin=origin, level=level, text=text)


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


def container_names(runtime: str = "podman") -> list[str]:
    """ACC containers currently known to the runtime."""
    if not shutil.which(runtime):
        raise FileNotFoundError(f"{runtime} is not on PATH")
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [runtime, "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip() or f"{runtime} ps failed")
    return [
        name.strip()
        for name in proc.stdout.splitlines()
        if name.strip().startswith("acc")
    ]


def collect_container(
    query: Query, *, runtime: str = "podman"
) -> tuple[list[LogLine], dict[str, str]]:
    """Read logs from every ACC container. Never raises."""
    lines: list[LogLine] = []
    unavailable: dict[str, str] = {}
    try:
        names = container_names(runtime)
    except Exception as exc:  # noqa: BLE001
        return lines, {"container": f"{type(exc).__name__}: {exc}"}

    if not names:
        return lines, {"container": "no ACC containers found"}

    for name in names:
        if query.role and query.role not in name:
            continue
        argv = [runtime, "logs"]
        if query.since_s:
            argv += ["--since", f"{int(query.since_s)}s"]
        argv.append(name)
        try:
            proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                argv, capture_output=True, text=True, timeout=30, check=False,
            )
        except Exception as exc:  # noqa: BLE001
            unavailable[name] = f"{type(exc).__name__}: {exc}"
            continue
        if proc.returncode != 0:
            unavailable[name] = (proc.stderr or "").strip()[:200] or "logs failed"
            continue
        # Podman puts container logs on both streams depending on the app.
        for stream in (proc.stdout, proc.stderr):
            for raw in stream.splitlines():
                if raw.strip():
                    lines.append(parse_line(raw, "container", name))
    return lines, unavailable


def collect_tracelog(query: Query) -> tuple[list[LogLine], dict[str, str]]:
    """Read ACC's durable session record. Never raises."""
    lines: list[LogLine] = []
    try:
        from acc import tracelog  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — module optional in some checkouts
        return lines, {"tracelog": f"unavailable: {exc}"}

    try:
        # list_sessions() returns ids; load_session() returns the recorded steps.
        session_ids = tracelog.list_sessions()
    except Exception as exc:  # noqa: BLE001
        return lines, {"tracelog": f"{type(exc).__name__}: {exc}"}

    for session_id in session_ids:
        session_id = str(session_id)
        if not session_id:
            continue
        if query.session and query.session != session_id:
            continue
        try:
            steps = tracelog.load_session(session_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("logs: session %s unreadable (%s)", session_id, exc)
            continue
        for step in steps or []:
            body = step if isinstance(step, dict) else {}
            text = json.dumps(body, default=str)
            ts = body.get("ts")
            lines.append(
                LogLine(
                    ts=float(ts) if isinstance(ts, (int, float)) else None,
                    source="tracelog",
                    origin=session_id,
                    level=str(body.get("level", "INFO")).upper(),
                    text=text,
                )
            )
    return lines, {}


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def merge(collected: Iterable[LogLine], query: Query) -> list[LogLine]:
    """Order across sources, applying the filters.

    Lines with no timestamp sort to the end rather than to 1970: an unparsed
    traceback belongs next to the error that produced it, not at the top of the
    report where it reads as the oldest thing that happened.
    """
    kept = [line for line in collected if query.keeps(line)]
    kept.sort(key=lambda line: (line.ts is None, line.ts or 0.0))
    if query.limit and len(kept) > query.limit:
        # Keep the most recent — the tail is what an incident needs.
        kept = kept[-query.limit :]
    return kept


def gather(query: Query, *, runtime: str = "podman") -> Report:
    """Read every requested source and merge. Never raises."""
    lines: list[LogLine] = []
    unavailable: dict[str, str] = {}

    if "container" in query.sources:
        got, missing = collect_container(query, runtime=runtime)
        lines.extend(got)
        unavailable.update(missing)
    if "tracelog" in query.sources:
        got, missing = collect_tracelog(query)
        lines.extend(got)
        unavailable.update(missing)

    return Report(lines=merge(lines, query), unavailable=unavailable)


def follow(
    query: Query, *, runtime: str = "podman", poll_s: float = 2.0
) -> Iterator[LogLine]:
    """Yield new lines as they appear.

    Polls and de-duplicates rather than holding one `logs -f` per container.
    Several concurrent followers is how interleaved output gets corrupted —
    two processes writing a partial line each — and an operator watching a
    collective would rather have a two-second delay than a mangled traceback.
    """
    import time  # noqa: PLC0415

    seen: set[tuple[float | None, str, str]] = set()
    while True:
        report = gather(query, runtime=runtime)
        for line in report.lines:
            key = (line.ts, line.origin, line.text)
            if key in seen:
                continue
            seen.add(key)
            yield line
        # Bound the memory of a long-running follow.
        if len(seen) > 50_000:
            seen = set(list(seen)[-10_000:])
        time.sleep(poll_s)


def parse_since(text: str) -> float | None:
    """Turn ``30m`` / ``2h`` / ``90s`` / ``1d`` into seconds."""
    raw = (text or "").strip().lower()
    if not raw:
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if raw[-1] in units:
        try:
            return float(raw[:-1]) * units[raw[-1]]
        except ValueError:
            return None
    try:
        return float(raw)
    except ValueError:
        return None
