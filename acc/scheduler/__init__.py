"""ACC scheduler — file-based recurring task scheduler (proposal 005).

Surface:

* :class:`Schedule` dataclass — one declarative entry.
* :class:`ScheduleStore` — YAML-on-disk round-tripper.
* :func:`next_fire_time` — minimal cron-expression evaluator.

This module ships the in-process primitives; the operator-facing
CLI lives in :mod:`acc.cli.schedule_cmd`.  The daemon is a
``run-once`` invocation the operator wires into cron / systemd-
timer / Windows Task Scheduler.

Supported cron syntax (subset — keeps the dep surface small):

* ``* * * * *``           — every minute
* ``*/N * * * *``         — every N minutes (N in 1..59)
* ``M * * * *``           — at minute M of every hour (M in 0..59)
* ``0 H * * *``           — at H:00 every day (H in 0..23)
* ``M H * * *``           — at H:M every day

Anything else is rejected with ``ValueError``.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml


logger = logging.getLogger("acc.scheduler")


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


@dataclass
class Schedule:
    """One scheduled task entry.

    Persisted as YAML at ``schedules/<name>.yaml``.  Wire-format is
    the dict produced by :func:`dataclasses.asdict`.
    """

    name: str
    role: str
    task: str
    cron: str
    collective_id: str = "sol-01"
    target_agent_id: str = ""
    enabled: bool = True
    last_fired_ts: float = 0.0
    created_ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Schedule":
        allowed = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in allowed}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@dataclass
class ScheduleStore:
    """Load + save :class:`Schedule` entries under a directory.

    The store is filesystem-only — no Redis, no NATS, no central
    coordinator.  Each schedule is a separate ``<name>.yaml`` so
    the operator can ``git add`` / ``rm`` / edit them with normal
    tools and the file-watcher pattern from proposal 003 PR-3 would
    naturally apply.
    """

    root: Path

    def list(self) -> list[Schedule]:
        if not self.root.is_dir():
            return []
        out: list[Schedule] = []
        for path in sorted(self.root.glob("*.yaml")):
            try:
                with path.open("r", encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh) or {}
            except Exception:
                logger.exception("scheduler: failed to read %s", path)
                continue
            if not isinstance(raw, dict):
                continue
            try:
                out.append(Schedule.from_dict(raw))
            except Exception:
                logger.exception("scheduler: malformed schedule at %s", path)
        return out

    def get(self, name: str) -> Optional[Schedule]:
        path = self.root / f"{name}.yaml"
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return Schedule.from_dict(raw)

    def save(self, schedule: Schedule) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{schedule.name}.yaml"
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(
                schedule.to_dict(),
                fh,
                sort_keys=False,
                default_flow_style=False,
            )
        return path

    def remove(self, name: str) -> bool:
        path = self.root / f"{name}.yaml"
        if not path.is_file():
            return False
        path.unlink()
        return True


# ---------------------------------------------------------------------------
# Cron parser — minimal subset
# ---------------------------------------------------------------------------


def _parse_cron(expr: str) -> dict[str, Any]:
    """Parse a supported cron expression; raise ``ValueError`` on
    anything outside the supported subset (documented in the module
    docstring)."""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"cron expression must be 5 fields: minute hour dom month dow; got {expr!r}"
        )
    minute, hour, dom, month, dow = parts
    if dom != "*" or month != "*" or dow != "*":
        raise ValueError(
            "scheduler currently supports only `* * *` for day-of-month, "
            f"month, and day-of-week; got {expr!r}"
        )

    # Minute: '*', '*/N', or a single integer 0..59.
    if minute == "*":
        minute_kind = "any"
        minute_step = 1
        minute_value = 0
    elif minute.startswith("*/"):
        try:
            n = int(minute[2:])
        except ValueError:
            raise ValueError(f"bad minute step {minute!r}")
        if not 1 <= n <= 59:
            raise ValueError(f"minute step must be 1..59; got {n}")
        minute_kind = "step"
        minute_step = n
        minute_value = 0
    else:
        try:
            m = int(minute)
        except ValueError:
            raise ValueError(f"bad minute {minute!r}")
        if not 0 <= m <= 59:
            raise ValueError(f"minute must be 0..59; got {m}")
        minute_kind = "exact"
        minute_step = 1
        minute_value = m

    # Hour: '*' or single integer 0..23.
    if hour == "*":
        hour_kind = "any"
        hour_value = 0
    else:
        try:
            h = int(hour)
        except ValueError:
            raise ValueError(f"bad hour {hour!r}")
        if not 0 <= h <= 23:
            raise ValueError(f"hour must be 0..23; got {h}")
        hour_kind = "exact"
        hour_value = h

    return {
        "minute_kind": minute_kind,
        "minute_step": minute_step,
        "minute_value": minute_value,
        "hour_kind": hour_kind,
        "hour_value": hour_value,
    }


def next_fire_time(cron_expr: str, from_ts: float) -> float:
    """Return the next fire wall-clock timestamp ≥ ``from_ts``.

    Local timezone is used (same convention as `cron`).  Returns a
    float wall-clock seconds value (compatible with ``time.time()``).
    """
    parsed = _parse_cron(cron_expr)
    # Start scanning from the next minute boundary after from_ts so
    # a schedule firing at the exact wall-clock minute returns the
    # following slot rather than re-firing immediately.
    start = datetime.fromtimestamp(from_ts).replace(second=0, microsecond=0)
    start = start + timedelta(minutes=1)
    # Bounded scan: a year of minutes is generous for the supported
    # 5-field subset (which can't have gaps > 24 h).
    for offset in range(0, 366 * 24 * 60):
        candidate = start + timedelta(minutes=offset)
        if _matches(parsed, candidate):
            return candidate.timestamp()
    raise ValueError(
        f"no fire time within a year for {cron_expr!r} (unsupported pattern?)"
    )


def _matches(parsed: dict[str, Any], dt: datetime) -> bool:
    # Hour gate.
    if parsed["hour_kind"] == "exact" and dt.hour != parsed["hour_value"]:
        return False
    # Minute gate.
    mk = parsed["minute_kind"]
    if mk == "any":
        return True
    if mk == "exact":
        return dt.minute == parsed["minute_value"]
    if mk == "step":
        return dt.minute % parsed["minute_step"] == 0
    return False


# ---------------------------------------------------------------------------
# Due-schedule selection
# ---------------------------------------------------------------------------


def due_schedules(
    schedules: list[Schedule], now_ts: float,
) -> list[Schedule]:
    """Return the subset of *schedules* whose next fire time is
    ≤ ``now_ts`` and whose ``enabled`` flag is True.

    "Due" is computed as: ``next_fire_time(last_fired_ts or
    created_ts, …) <= now_ts``.  A never-fired schedule with
    ``created_ts == 0`` is treated as if created at ``now_ts -
    60`` so the operator's first run-once invocation after adding
    a schedule fires it on the first matching slot (no perpetual
    delay).
    """
    out: list[Schedule] = []
    for sched in schedules:
        if not sched.enabled:
            continue
        base_ts = sched.last_fired_ts or sched.created_ts or (now_ts - 60.0)
        try:
            nft = next_fire_time(sched.cron, base_ts)
        except Exception:
            logger.exception(
                "scheduler: bad cron %r on schedule %r — skipping",
                sched.cron, sched.name,
            )
            continue
        if nft <= now_ts:
            out.append(sched)
    return out
