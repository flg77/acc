"""Local, operator-set star ratings for Marketplace packages.

Net-new + intentionally LOCAL: ratings live in ``~/.acc/ratings.yaml`` (override
with ``ACC_RATINGS_PATH``), keyed by package name → 1..5 stars.  No server, no
aggregation — a personal "I rate this pack" marker the Marketplace shows and
lets the operator set with the number keys.  Best-effort: a missing / corrupt
file reads as no ratings and never raises on the render path.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger("acc.pkg.ratings")


def ratings_path() -> Path:
    raw = os.environ.get("ACC_RATINGS_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".acc" / "ratings.yaml"


def load_ratings(path: Path | None = None) -> dict[str, int]:
    """``{package_name: stars}`` for valid 1..5 entries; ``{}`` on any error."""
    p = path or ratings_path()
    try:
        raw = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    out: dict[str, int] = {}
    for key, val in (raw.get("ratings", {}) or {}).items():
        try:
            n = int(val)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 5:
            out[str(key)] = n
    return out


def get_rating(name: str, path: Path | None = None) -> int:
    """Stars for *name* (0 = unrated)."""
    return load_ratings(path).get(name, 0)


def set_rating(name: str, stars: int, path: Path | None = None) -> Path:
    """Set (1..5) or clear (0) a rating for *name*, then save atomically."""
    p = Path(path or ratings_path())
    data = load_ratings(p)
    stars = int(stars)
    if stars == 0:
        data.pop(name, None)
    elif 1 <= stars <= 5:
        data[name] = stars
    else:
        raise ValueError("stars must be 0 (clear) or 1..5")
    p.parent.mkdir(parents=True, exist_ok=True)
    from acc._atomic_write import atomic_write_text  # noqa: PLC0415

    text = yaml.safe_dump({"ratings": dict(sorted(data.items()))}, allow_unicode=True)
    atomic_write_text(p, text, mode=0o644)
    return p


def stars_glyph(n: int) -> str:
    """``★★★☆☆`` for 3, ``—`` for unrated."""
    n = max(0, min(5, int(n or 0)))
    return ("★" * n + "☆" * (5 - n)) if n else "—"
