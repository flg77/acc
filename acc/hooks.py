"""Operator hooks on lifecycle events.

An operator wants to know when something happens — a task completed, a role
changed, an alert escalated — without polling for it or reading logs. A hook is
a command that runs when a matching event crosses the bus, with the event on
stdin.

**Hooks observe. They do not gate.**

That is the significant design decision here, and it is deliberate. A hook that
could veto an action would be far more useful and far more dangerous, and ACC
already has a blocking mechanism built for exactly that purpose: the oversight
queue, which has the approval record, the atomic claim and the audit trail to
match. Adding a second gating path with none of that would mean two answers to
"who allowed this", and the weaker one would be the one nobody audits.

So a hook cannot stop, delay or alter anything. It runs alongside, and its
failure is its own problem:

* it runs **out of band** — the agent never waits for it;
* it is **timed out** — a hanging hook cannot accumulate;
* it is **allowlisted** — a definition naming a command not on the allowlist
  does not run, so a writable hook file is not arbitrary code execution;
* every run is **recorded**, failures included, because a hook that quietly
  stopped firing is worse than one that never existed.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from acc._atomic_write import atomic_write_text

logger = logging.getLogger("acc.hooks")

#: Where hook definitions live. Per-host, like the rest of ACC's configuration.
HOOKS_PATH_VAR = "ACC_HOOKS_PATH"
DEFAULT_HOOKS_FILE = "hooks.yaml"

#: Commands a hook may invoke. A hook definition naming anything else is
#: refused. Without this, write access to hooks.yaml would be arbitrary code
#: execution against whatever runs the dispatcher.
ALLOWLIST_VAR = "ACC_HOOK_ALLOWLIST"

#: A hook that has not finished by now is killed. Deliberately short: hooks are
#: notifications, and anything long-running should be started BY a hook rather
#: than run as one.
DEFAULT_TIMEOUT_S = 10.0

#: Consecutive failures after which a hook is disabled. A hook failing forever
#: is noise that trains the operator to ignore the record it writes.
FAILURE_LIMIT = 5


class HookError(Exception):
    """A hook definition was refused. The message is operator-facing."""


@dataclass
class Hook:
    """One registered hook."""

    name: str
    event: str
    command: str
    filter: str = ""            # substring match against the JSON payload
    timeout_s: float = DEFAULT_TIMEOUT_S
    enabled: bool = True
    failures: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "event": self.event,
            "command": self.command,
            "filter": self.filter,
            "timeout_s": self.timeout_s,
            "enabled": self.enabled,
            "failures": self.failures,
        }

    def matches(self, event: str, payload: dict[str, Any]) -> bool:
        """Does this hook fire for *event*?

        ``event`` may be ``*`` to match everything. The filter is a plain
        substring test against the serialised payload — deliberately not an
        expression language: a filter that can compute is a filter that can be
        slow or wrong, and this runs on every matching event.
        """
        if not self.enabled:
            return False
        if self.event != "*" and self.event != event:
            return False
        if not self.filter:
            return True
        try:
            return self.filter in json.dumps(payload, default=str)
        except Exception:  # pragma: no cover — unserialisable payload
            return False


@dataclass
class HookRun:
    """The record of one hook execution."""

    name: str
    event: str
    ok: bool
    returncode: int | None
    duration_s: float
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "event": self.event,
            "ok": self.ok,
            "returncode": self.returncode,
            "duration_s": round(self.duration_s, 3),
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def hooks_path(repo_root: Path | None = None) -> Path:
    raw = os.environ.get(HOOKS_PATH_VAR, "").strip()
    if raw:
        return Path(raw)
    root = repo_root or Path(__file__).resolve().parent.parent
    return root / DEFAULT_HOOKS_FILE


def allowlist(environ: dict[str, str] | None = None) -> set[str]:
    """Commands hooks may invoke, by executable name or absolute path."""
    env = environ if environ is not None else os.environ
    raw = str(env.get(ALLOWLIST_VAR, "") or "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def load(repo_root: Path | None = None) -> list[Hook]:
    """Read hook definitions. A malformed file yields none, loudly."""
    path = hooks_path(repo_root)
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.error("hooks: cannot read %s (%s) — no hooks will fire", path, exc)
        return []
    entries = raw.get("hooks") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return []
    out: list[Hook] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                Hook(
                    name=str(item["name"]),
                    event=str(item.get("event", "*")),
                    command=str(item["command"]),
                    filter=str(item.get("filter", "") or ""),
                    timeout_s=float(item.get("timeout_s", DEFAULT_TIMEOUT_S)),
                    enabled=bool(item.get("enabled", True)),
                    failures=int(item.get("failures", 0)),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("hooks: skipping malformed definition %r (%s)", item, exc)
    return out


def save(hooks: Iterable[Hook], repo_root: Path | None = None) -> Path:
    path = hooks_path(repo_root)
    body = yaml.safe_dump(
        {"hooks": [h.as_dict() for h in hooks]}, sort_keys=False, allow_unicode=True
    )
    header = (
        "# ACC operator hooks.\n"
        "# Hooks OBSERVE lifecycle events; they cannot block, delay or alter\n"
        "# anything. Gating belongs to the oversight queue, which has the\n"
        "# approval record and audit trail.\n"
        f"# Commands must appear in {ALLOWLIST_VAR} or they will not run.\n"
    )
    atomic_write_text(path, header + body, mode=0o644, newline="")
    return path


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def split_command(command: str) -> list[str]:
    """Split a hook command line into argv.

    Neither shlex mode is right on its own. ``posix=True`` strips quotes
    correctly but treats a backslash as an escape, which destroys Windows
    paths; ``posix=False`` preserves backslashes but leaves the quote
    characters inside the tokens, so a quoted argument reaches the program
    still wrapped in quotes. Split without POSIX escaping, then strip the
    matched quotes that mode leaves behind.
    """
    try:
        parts = shlex.split(command, posix=False)
    except ValueError as exc:
        raise HookError(f"cannot parse command {command!r}: {exc}") from exc
    out: list[str] = []
    for part in parts:
        if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'":
            part = part[1:-1]
        out.append(part)
    return out


def executable_of(command: str) -> str:
    """The program a command line invokes."""
    parts = split_command(command)
    if not parts:
        raise HookError("empty command")
    return parts[0]


def check_allowed(command: str, environ: dict[str, str] | None = None) -> None:
    """Raise unless *command* invokes an allowlisted executable.

    Raises:
        HookError: naming both the executable and the variable to add it to.
    """
    exe = executable_of(command)
    allowed = allowlist(environ)
    if not allowed:
        raise HookError(
            f"no hook allowlist configured; set {ALLOWLIST_VAR} before "
            f"registering hooks (nothing runs while it is empty)"
        )
    if exe in allowed or Path(exe).name in allowed:
        return
    raise HookError(
        f"{exe!r} is not in {ALLOWLIST_VAR}. Add it deliberately — a hook file "
        f"that can name any command is arbitrary code execution."
    )


def add(
    name: str,
    event: str,
    command: str,
    *,
    filter: str = "",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    repo_root: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Hook:
    """Register a hook. Refuses a duplicate name or a non-allowlisted command."""
    check_allowed(command, environ)
    existing = load(repo_root)
    if any(h.name == name for h in existing):
        raise HookError(f"a hook named {name!r} already exists")
    hook = Hook(name=name, event=event, command=command, filter=filter, timeout_s=timeout_s)
    existing.append(hook)
    save(existing, repo_root)
    return hook


def remove(name: str, repo_root: Path | None = None) -> bool:
    """Remove a hook. Takes effect on the next event — no restart."""
    existing = load(repo_root)
    remaining = [h for h in existing if h.name != name]
    if len(remaining) == len(existing):
        return False
    save(remaining, repo_root)
    return True


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def run_hook(
    hook: Hook,
    event: str,
    payload: dict[str, Any],
    *,
    environ: dict[str, str] | None = None,
) -> HookRun:
    """Run one hook with the payload on stdin. Never raises.

    The timeout is the load-bearing part: without it a hook that hangs holds a
    process forever, and enough of them exhaust the host. A killed hook is
    recorded as a failure like any other.
    """
    started = time.monotonic()
    try:
        check_allowed(hook.command, environ)
    except HookError as exc:
        return HookRun(hook.name, event, False, None, 0.0, f"refused: {exc}")

    try:
        proc = subprocess.run(  # noqa: S603 — allowlisted, and that is the control
            split_command(hook.command),
            input=json.dumps({"event": event, "payload": payload}, default=str),
            capture_output=True,
            text=True,
            timeout=hook.timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return HookRun(
            hook.name, event, False, None, time.monotonic() - started,
            f"timed out after {hook.timeout_s:g}s",
        )
    except (OSError, ValueError) as exc:
        return HookRun(
            hook.name, event, False, None, time.monotonic() - started,
            f"{type(exc).__name__}: {exc}",
        )

    duration = time.monotonic() - started
    ok = proc.returncode == 0
    detail = "" if ok else (proc.stderr or proc.stdout or "").strip()[:400]
    return HookRun(hook.name, event, ok, proc.returncode, duration, detail)


@dataclass
class Dispatcher:
    """Runs matching hooks for an event, out of band.

    Holds the failure counters, because a hook failing forever is noise that
    trains the operator to ignore the record it writes.
    """

    repo_root: Path | None = None
    runs: list[HookRun] = field(default_factory=list)
    _failures: dict[str, int] = field(default_factory=dict)

    def dispatch(self, event: str, payload: dict[str, Any]) -> list[HookRun]:
        """Run every hook matching *event*. Never raises, never blocks a caller."""
        results: list[HookRun] = []
        for hook in load(self.repo_root):
            if self._failures.get(hook.name, 0) >= FAILURE_LIMIT:
                continue
            if not hook.matches(event, payload):
                continue
            run = run_hook(hook, event, payload)
            results.append(run)
            self.runs.append(run)
            if run.ok:
                self._failures.pop(hook.name, None)
            else:
                count = self._failures.get(hook.name, 0) + 1
                self._failures[hook.name] = count
                logger.warning(
                    "hooks: %r failed on %s (%s) [%d/%d]",
                    hook.name, event, run.detail, count, FAILURE_LIMIT,
                )
                if count >= FAILURE_LIMIT:
                    logger.error(
                        "hooks: %r disabled after %d consecutive failures; "
                        "`acc-cli hooks test %s` to diagnose",
                        hook.name, count, hook.name,
                    )
        return results

    def disabled(self) -> list[str]:
        return sorted(n for n, c in self._failures.items() if c >= FAILURE_LIMIT)
