"""Which directories the operator has trusted as workspaces -- the record.

`20260909-acc-install` IN-03 (proposal 055; operator decisions IN-00 §4b).
D-007 made one host directory the trusted workspace: bind-mounted at
``/workspace`` in every cell, carrying the ``.acc-workspace-trust`` sentinel
(:mod:`acc.workspace`) the filesystem skills check before any write.  What
did not exist is a **record of what the operator trusted and when**, so that
``acc`` started in a directory can ask once and remember.

The record lives in ``~/.config/acc/trust.yaml`` (``ACC_TRUST_PATH``
overrides), one row per directory::

    - path: /home/flg/git/foo
      scope: below            # dir  = this directory only; below = and everything under it
      decision: trusted       # or denied -- a "no" is remembered too, so nobody is asked twice
      since: 2026-09-09T10:12:00Z
      by: system:flg          # the principal that decided (identity.current())

:func:`lookup` answers for any path: the exact row wins, else the nearest
ancestor whose scope is ``below``.  ``below`` is the normal answer for a git
repository (it has subdirectories); :func:`repositories_below` counts the
repositories under a directory so the launcher can warn before someone
trusts a *super-repo* holding many of them.  Granting trust also writes the
sentinel the cells check (:func:`acc.workspace.mark_trusted`), so the record
and the mount agree.  Revoking removes the row; the sentinel is left for the
operator to delete (it is inside their directory).
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import yaml

from acc import paths

SCOPE_DIR = "dir"
SCOPE_BELOW = "below"
TRUSTED = "trusted"
DENIED = "denied"
#: A directory holding this many repositories below it draws a warning.
SUPER_REPO_THRESHOLD = 3
_SCAN_DEPTH = 3


@dataclass(frozen=True)
class TrustRecord:
    path: str
    scope: str = SCOPE_DIR
    decision: str = TRUSTED
    since: str = ""
    by: str = ""

    @property
    def trusted(self) -> bool:
        return self.decision == TRUSTED


def trust_path() -> Path:
    raw = os.environ.get("ACC_TRUST_PATH", "").strip()
    return Path(raw).expanduser() if raw else paths.user_config_dir() / "trust.yaml"


def _norm(p: Path | str) -> str:
    return str(Path(p).expanduser().resolve())


def records(path: Path | None = None) -> list[TrustRecord]:
    p = path or trust_path()
    if not p.is_file():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rows = raw.get("trusted") if isinstance(raw, dict) else raw
    out: list[TrustRecord] = []
    for r in rows or []:
        if isinstance(r, dict) and r.get("path"):
            out.append(TrustRecord(
                path=str(r["path"]), scope=str(r.get("scope") or SCOPE_DIR),
                decision=str(r.get("decision") or TRUSTED), since=str(r.get("since") or ""),
                by=str(r.get("by") or ""),
            ))
    return out


def _save(rows: Iterable[TrustRecord], path: Path | None = None) -> Path:
    p = path or trust_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"trusted": [asdict(r) for r in rows]}
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    os.replace(tmp, p)
    return p


def lookup(directory: Path | str, path: Path | None = None) -> tuple[TrustRecord | None, str]:
    """The record covering *directory* and how it matched: ``"exact"``,
    ``"below"`` (an ancestor trusted with scope below) or ``""``."""
    target = Path(_norm(directory))
    rows = records(path)
    for r in rows:
        if Path(_norm(r.path)) == target:
            return r, "exact"
    best: TrustRecord | None = None
    for r in rows:
        if r.scope != SCOPE_BELOW:
            continue
        root = Path(_norm(r.path))
        if root in target.parents and (best is None or len(root.parts) > len(Path(_norm(best.path)).parts)):
            best = r
    return (best, "below") if best else (None, "")


def is_trusted(directory: Path | str, path: Path | None = None) -> bool:
    rec, _ = lookup(directory, path)
    return bool(rec and rec.trusted)


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _who() -> str:
    try:
        from acc.identity import current  # noqa: PLC0415
        return current().attribution()
    except Exception:  # noqa: BLE001
        return ""


def trust(directory: Path | str, *, scope: str = SCOPE_DIR, by: str = "", path: Path | None = None,
          sentinel: bool = True) -> TrustRecord:
    """Record *directory* as trusted (replacing any row for it) and write the
    sentinel the cells check."""
    if scope not in (SCOPE_DIR, SCOPE_BELOW):
        raise ValueError(f"scope must be {SCOPE_DIR!r} or {SCOPE_BELOW!r}, not {scope!r}")
    target = _norm(directory)
    if not Path(target).is_dir():
        raise FileNotFoundError(f"not a directory: {target}")
    rec = TrustRecord(path=target, scope=scope, decision=TRUSTED, since=_stamp(), by=by or _who())
    rows = [r for r in records(path) if _norm(r.path) != target] + [rec]
    _save(rows, path)
    if sentinel:
        from acc.workspace import mark_trusted  # noqa: PLC0415
        try:
            mark_trusted(Path(target), note=f"acc trust {scope} by {rec.by or 'operator'}")
        except OSError:
            pass                                   # a read-only tree: the record still holds
    return rec


def deny(directory: Path | str, *, by: str = "", path: Path | None = None) -> TrustRecord:
    """Remember a "no" so the operator is not asked again for this directory."""
    target = _norm(directory)
    rec = TrustRecord(path=target, scope=SCOPE_DIR, decision=DENIED, since=_stamp(), by=by or _who())
    rows = [r for r in records(path) if _norm(r.path) != target] + [rec]
    _save(rows, path)
    return rec


def revoke(directory: Path | str, path: Path | None = None) -> bool:
    """Drop the row for *directory*; True when there was one."""
    target = _norm(directory)
    rows = records(path)
    kept = [r for r in rows if _norm(r.path) != target]
    if len(kept) == len(rows):
        return False
    _save(kept, path)
    return True


def repositories_below(directory: Path | str, *, depth: int = _SCAN_DEPTH, limit: int = 50) -> int:
    """How many git repositories sit strictly below *directory* (bounded)."""
    root = Path(_norm(directory))
    count = 0
    try:
        stack = [(root, 0)]
        while stack:
            d, level = stack.pop()
            if level >= depth:
                continue
            try:
                children = [c for c in d.iterdir() if c.is_dir() and not c.is_symlink()]
            except OSError:
                continue
            for c in children:
                if c.name == ".git":
                    if d != root:
                        count += 1
                        if count >= limit:
                            return count
                    continue
                if c.name.startswith(".") and c.name != ".git":
                    continue
                stack.append((c, level + 1))
    except OSError:
        pass
    return count


def describe(path: Path | None = None) -> str:
    rows = records(path)
    if not rows:
        return f"no trusted directories ({trust_path()})"
    lines = [f"{trust_path()}"]
    for r in rows:
        lines.append(f"  {r.decision:<8} {r.scope:<6} {r.path}  ({r.since} by {r.by or '-'})")
    return "\n".join(lines)
