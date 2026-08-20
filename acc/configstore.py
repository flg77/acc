"""Typed, comment-preserving access to ACC's configuration files.

Everything here is built on one constraint: **writes must not reformat the
file**.  ACC's configuration templates are heavily commented and hand-tuned,
and a round-trip that re-emits the document — even a faithful one — produces a
diff nobody wants to review and, in practice, is simply not adopted.

So this module reads with :mod:`yaml` but *writes by editing lines*.  A small
scanner maps each dotted key to the line that declares it; a write replaces
only the value on that line, keeping indentation and any trailing comment
exactly as they were.  The result is what the change asked for: a ``set``
followed by ``git diff`` shows one changed line.

The other job here is refusing bad writes.  Two rules matter most:

* ``.env`` is described by the schema but never written.  Secret material
  stays with the operator.
* A value that would create an **unresolvable reference** is refused at write
  time — setting ``role_models.<role>`` to a model id that ``models.yaml``
  does not define is the failure that otherwise appears only when that role
  next runs a task, if anyone is watching.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from acc import configschema as cs
from acc._atomic_write import atomic_write_text

# --------------------------------------------------------------------------
# Line scanning
# --------------------------------------------------------------------------

_KEY_RE = re.compile(r"^(?P<indent>[ ]*)(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)[ ]*:(?P<rest>.*)$")
_LIST_RE = re.compile(r"^[ ]*-[ ]")

#: Written out rather than inlined so the escapes survive every layer that
#: rewrites this source (shells, patch scripts) without turning into a real
#: newline.
LF = chr(10)
CRLF = chr(13) + chr(10)


@dataclass(frozen=True)
class Location:
    """Where a key is declared in a file.

    Attributes:
        line: 0-based index of the declaring line.
        indent: leading-space count of that line.
        inline: True when the value sits on the same line (a scalar);
            False when the key opens a nested block.
        end: 0-based index one past the key's last line, including any
            nested block — the slice to delete on ``unset``.
    """

    line: int
    indent: int
    inline: bool
    end: int


def scan(text: str) -> dict[str, Location]:
    """Map every dotted key in *text* to its :class:`Location`.

    Only block mappings are indexed.  List items are skipped: their contents
    are operator data under a dynamic container (``models``, ``agents``), not
    schema keys, and indexing into them would invite writes this module has no
    safe way to perform.
    """
    lines = text.splitlines()
    stack: list[tuple[int, str]] = []
    found: dict[str, Location] = {}
    order: list[tuple[str, int, int]] = []  # (dotted, line, indent)

    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or _LIST_RE.match(raw):
            i += 1
            continue
        m = _KEY_RE.match(raw)
        if not m:
            i += 1
            continue

        indent = len(m.group("indent"))
        key = m.group("key")
        rest = m.group("rest")

        while stack and stack[-1][0] >= indent:
            stack.pop()
        dotted = ".".join([s[1] for s in stack] + [key])
        value_part = _strip_comment(rest).strip()
        found[dotted] = Location(line=i, indent=indent, inline=bool(value_part), end=i + 1)
        order.append((dotted, i, indent))
        stack.append((indent, key))

        # A block scalar (`key: |`) owns every more-indented line that
        # follows.  Skipping them keeps a `key:`-looking line inside prose
        # from being mistaken for structure.
        if value_part in ("|", ">", "|-", ">-", "|+", ">+"):
            i += 1
            while i < len(lines) and (not lines[i].strip() or _leading(lines[i]) > indent):
                i += 1
            continue
        i += 1

    # Second pass: how far does each key's block extend?  Needed so `unset`
    # deletes a nested block whole.  The end is the last *more-indented* line,
    # not the start of the next key: stopping at the next key would also
    # swallow the blank line that separates the two blocks, turning a
    # one-key removal into a two-line diff.
    for dotted, line, indent in order:
        last = line
        j = line + 1
        while j < len(lines):
            if not lines[j].strip():
                j += 1
                continue
            if _leading(lines[j]) <= indent:
                break
            last = j
            j += 1
        loc = found[dotted]
        found[dotted] = Location(
            line=loc.line, indent=loc.indent, inline=loc.inline, end=last + 1
        )
    return found


def _leading(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _strip_comment(rest: str) -> str:
    """Return *rest* with any trailing ``#`` comment removed (quote-aware)."""
    out, quote = [], ""
    for ch in rest:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out)


def _comment_of(rest: str) -> str:
    """The trailing comment of *rest*, with the whitespace that aligned it.

    The gap matters: these files line their comments up in columns, and
    re-emitting ``value# comment`` instead of ``value   # comment`` turns a
    one-line edit into a visible formatting change.
    """
    body = _strip_comment(rest)
    comment = rest[len(body):]
    if not comment:
        return ""
    return body[len(body.rstrip()):] + comment


def duplicate_top_level_keys(text: str) -> list[str]:
    """Top-level keys declared more than once.

    Two ``role_models:`` blocks in one file is valid YAML and the last one
    silently wins — the failure that forced the ``acc-profiles`` tooling to
    fence its edits between markers and strip anything unmanaged.  Nothing
    else reports it, so this does.
    """
    seen: dict[str, int] = {}
    in_block = False
    block_indent = 0
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if in_block:
            if _leading(raw) > block_indent:
                continue
            in_block = False
        m = _KEY_RE.match(raw)
        if not m or len(m.group("indent")) != 0:
            continue
        if _strip_comment(m.group("rest")).strip() in ("|", ">", "|-", ">-", "|+", ">+"):
            in_block, block_indent = True, 0
        seen[m.group("key")] = seen.get(m.group("key"), 0) + 1
    return sorted(k for k, n in seen.items() if n > 1)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Resolved:
    """A resolved key lookup."""

    path: str
    value: Any
    file: str
    file_path: Path
    present: bool
    secret: bool
    key: cs.Key | None


def _read_raw(path: Path) -> tuple[str, str]:
    """Return ``(text, newline)`` without translating line endings.

    :meth:`Path.read_text` normalises CRLF to LF and the writer re-expands
    LF using ``os.linesep``.  On Windows that round-trip rewrites every line
    of an LF file — precisely the whole-file diff this module exists to
    avoid.  Read bytes and carry the file's own convention to the write.
    """
    if not path.is_file():
        return "", LF
    data = path.read_bytes().decode("utf-8")
    return data, (CRLF if CRLF in data else LF)


def read_text(file_id: str, *, repo_root: Path | None = None) -> str:
    """File contents, line endings normalised to LF (read-only use)."""
    text, _ = _read_raw(cs.resolve_path(file_id, repo_root=repo_root))
    return text.replace(CRLF, LF)


def read(file_id: str, *, repo_root: Path | None = None) -> dict[str, Any]:
    """Parse one configuration file into a dict (empty when absent)."""
    if file_id == "env":
        return _read_env(cs.resolve_path("env", repo_root=repo_root))
    text = read_text(file_id, repo_root=repo_root)
    if not text.strip():
        return {}
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def _read_env(path: Path) -> dict[str, Any]:
    """Parse ``.env`` into ``{name: present}``.

    Values are deliberately **not** returned.  Nothing in this module has a
    reason to hold secret material, and a surface that cannot obtain a value
    cannot leak one.
    """
    out: dict[str, Any] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        if name:
            out[name] = True
    return out


def merged(*, repo_root: Path | None = None) -> dict[str, Any]:
    """A merged view across every file.

    Top-level keys are disjoint across the four YAML surfaces, so the merge
    cannot collide; ``.env`` is namespaced under ``env`` because its keys are
    flat names that would otherwise sit beside structured configuration.
    """
    out: dict[str, Any] = {}
    for spec in cs.FILES:
        data = read(spec.id, repo_root=repo_root)
        if spec.id == "env":
            out["env"] = data
        else:
            out.update(data)
    return out


def _dig(data: dict[str, Any], dotted: str) -> tuple[Any, bool]:
    """Return ``(value, present)`` for a dotted path within *data*."""
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def get(dotted: str, *, repo_root: Path | None = None) -> Resolved:
    """Resolve one key: its value, and which file owns it."""
    key = cs.find(dotted)
    file_id = key.file if key else _owning_file_by_prefix(dotted, repo_root=repo_root)
    data = merged(repo_root=repo_root)
    value, present = _dig(data, dotted)
    if not present and key is not None and not key.required:
        value = key.default
    return Resolved(
        path=dotted,
        value=value,
        file=file_id or "",
        file_path=cs.resolve_path(file_id, repo_root=repo_root) if file_id else Path(),
        present=present,
        secret=bool(key and key.secret),
        key=key,
    )


def _owning_file_by_prefix(dotted: str, *, repo_root: Path | None = None) -> str | None:
    """Fall back to whichever file actually declares the key's root.

    Used for keys the schema does not know — an unknown key still lives
    somewhere, and reporting where is more useful than reporting nothing.
    """
    root = dotted.split(".", 1)[0]
    for spec in cs.FILES:
        if root in read(spec.id, repo_root=repo_root):
            return spec.id
    return None


# --------------------------------------------------------------------------
# Value formatting + coercion
# --------------------------------------------------------------------------


class ConfigError(Exception):
    """A write was refused.  The message is operator-facing."""


def coerce(raw: str, key: cs.Key | None) -> Any:
    """Turn a CLI string into a typed value, validated against *key*."""
    if key is None:
        return yaml.safe_load(raw)

    if key.choices:
        if raw not in key.choices:
            raise ConfigError(
                f"{key.path}: {raw!r} is not one of {', '.join(key.choices)}"
            )
        return raw

    kind = key.type
    try:
        if kind == "bool":
            low = raw.strip().lower()
            if low in ("true", "yes", "on", "1"):
                return True
            if low in ("false", "no", "off", "0"):
                return False
            raise ValueError(raw)
        if kind == "int":
            return int(raw)
        if kind == "float":
            return float(raw)
        if kind.startswith("list"):
            parsed = yaml.safe_load(raw)
            if isinstance(parsed, list):
                return parsed
            return [p.strip() for p in raw.split(",") if p.strip()]
        if kind == "map":
            parsed = yaml.safe_load(raw)
            if not isinstance(parsed, dict):
                raise ValueError(raw)
            return parsed
    except (ValueError, yaml.YAMLError) as exc:
        raise ConfigError(f"{key.path}: {raw!r} is not a valid {kind}") from exc
    return raw


def _fmt(value: Any) -> str:
    """Render *value* as an inline YAML scalar."""
    text = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True).strip()
    if text.endswith("\n..."):
        text = text[: -len("\n...")].strip()
    return text


# --------------------------------------------------------------------------
# Reference validation
# --------------------------------------------------------------------------


def _known_model_ids(*, repo_root: Path | None = None) -> set[str]:
    data = read("models", repo_root=repo_root)
    entries = data.get("models") or []
    # The registry field is ``model_id`` (see acc.models.ModelEntry) — NOT
    # ``id``.  Reading the wrong field yields an empty set, and an empty set
    # makes validate_reference() short-circuit, so every binding would pass.
    return {
        str(e.get("model_id"))
        for e in entries
        if isinstance(e, dict) and e.get("model_id")
    }


def validate_reference(dotted: str, value: Any, *, repo_root: Path | None = None) -> None:
    """Refuse a value that points at something that does not exist.

    Currently the ``role_models`` → ``models`` edge, which is where this bites
    in practice: a role bound to an unknown model id resolves to nothing at
    agent boot and the role silently falls back to the global default.

    Raises:
        ConfigError: when the reference cannot be resolved.
    """
    if not dotted.startswith("role_models."):
        return
    role = dotted.split(".", 1)[1]
    known = _known_model_ids(repo_root=repo_root)
    if not known:
        # No registry to check against — refusing here would block a legitimate
        # first-run ordering (bind roles, then add models).  Say nothing.
        return
    if str(value) not in known:
        raise ConfigError(
            f"role {role!r} would be bound to unknown model id {value!r}.\n"
            f"models.yaml defines: {', '.join(sorted(known))}\n"
            f"Add the model first, or pick one of the above."
        )


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


@dataclass
class Change:
    """The result of a write."""

    path: str
    file: str
    file_path: Path
    before: Any = None
    after: Any = None
    action: str = "set"
    line: int = -1
    diff: list[str] = field(default_factory=list)


def _writable_or_raise(key: cs.Key | None, dotted: str) -> cs.ConfigFile:
    if key is None:
        raise ConfigError(
            f"unknown key {dotted!r}. `acc-cli config check` lists known keys; "
            f"unknown keys already present in a file are preserved, but this "
            f"command will not create one."
        )
    spec = cs.file_by_id(key.file)
    if not spec.writable:
        raise ConfigError(
            f"{dotted}: {spec.filename} is not writable through this command.\n"
            f"It holds secret material; edit it directly so credentials never "
            f"pass through ACC tooling."
        )
    if key.secret:
        raise ConfigError(
            f"{dotted} is secret-bearing; set it in the environment instead so "
            f"the value is not written into a config file."
        )
    return spec


def set_key(
    dotted: str,
    raw_value: str,
    *,
    repo_root: Path | None = None,
    dry_run: bool = False,
) -> Change:
    """Set one key, preserving comments and formatting.

    Raises:
        ConfigError: the key is unknown, the file is not writable, the value
            does not fit the schema, or the value would create an
            unresolvable reference.
    """
    key = cs.find(dotted)
    spec = _writable_or_raise(key, dotted)
    assert key is not None  # _writable_or_raise refuses None
    value = coerce(raw_value, None if key.dynamic else key)
    return _write(dotted, value, spec, repo_root=repo_root, dry_run=dry_run)


def set_value(
    dotted: str,
    value: Any,
    *,
    repo_root: Path | None = None,
    dry_run: bool = False,
) -> Change:
    """Set an already-typed value (the programmatic path).

    :func:`set_key` parses an operator-supplied string; this one takes the
    value directly.  ``migrate`` must use this: round-tripping a default
    through the YAML formatter and back through :func:`coerce` re-quotes it,
    which turns an empty string into ``''''''``.
    """
    key = cs.find(dotted)
    spec = _writable_or_raise(key, dotted)
    return _write(dotted, value, spec, repo_root=repo_root, dry_run=dry_run)


def _write(
    dotted: str,
    value: Any,
    spec: cs.ConfigFile,
    *,
    repo_root: Path | None,
    dry_run: bool,
) -> Change:
    """Shared write path for :func:`set_key` and :func:`set_value`."""
    validate_reference(dotted, value, repo_root=repo_root)

    path = cs.resolve_path(spec.id, repo_root=repo_root)
    text, newline = _read_raw(path)
    lines = text.splitlines()
    index = scan(text)
    before, _ = _dig(read(spec.id, repo_root=repo_root), dotted)

    if dotted in index:
        loc = index[dotted]
        m = _KEY_RE.match(lines[loc.line])
        assert m is not None
        comment = _comment_of(m.group("rest"))
        lines[loc.line] = f"{' ' * loc.indent}{m.group('key')}: {_fmt(value)}{comment}"
        line_no = loc.line
        action = "set"
    else:
        line_no, lines = _insert(lines, index, dotted, value)
        action = "add"

    new_text = newline.join(lines) + (newline if text.endswith(LF) or not text else "")
    change = Change(
        path=dotted,
        file=spec.id,
        file_path=path,
        before=before,
        after=value,
        action=action,
        line=line_no,
        diff=_one_line_diff(text, new_text),
    )
    if not dry_run:
        atomic_write_text(path, new_text, mode=0o644, newline="")
    return change


def _insert(
    lines: list[str], index: dict[str, Location], dotted: str, value: Any
) -> tuple[int, list[str]]:
    """Insert a key that is not yet declared, under its nearest parent."""
    parts = dotted.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        parent = ".".join(parts[:cut])
        if parent in index:
            loc = index[parent]
            child_indent = loc.indent + 2
            leaf = ".".join(parts[cut:])
            at = loc.end
            # Step back over trailing blank lines so the new key joins the
            # block rather than floating after it.
            while at - 1 > loc.line and not lines[at - 1].strip():
                at -= 1
            lines.insert(at, f"{' ' * child_indent}{leaf}: {_fmt(value)}")
            return at, lines
    # No parent at all — a new top-level key goes at the end.
    while lines and not lines[-1].strip():
        lines.pop()
    lines.append(f"{dotted}: {_fmt(value)}")
    return len(lines) - 1, lines


def unset_key(
    dotted: str, *, repo_root: Path | None = None, dry_run: bool = False
) -> Change:
    """Remove a key (and its nested block, if any).

    Raises:
        ConfigError: the key is unknown, absent, or lives in a file this
            command may not write.
    """
    key = cs.find(dotted)
    spec = _writable_or_raise(key, dotted)
    path = cs.resolve_path(spec.id, repo_root=repo_root)
    text, newline = _read_raw(path)
    index = scan(text)
    if dotted not in index:
        raise ConfigError(f"{dotted} is not set in {path.name}; nothing to unset.")

    loc = index[dotted]
    lines = text.splitlines()
    before, _ = _dig(read(spec.id, repo_root=repo_root), dotted)
    del lines[loc.line: loc.end]
    new_text = newline.join(lines) + (newline if text.endswith(LF) else "")
    change = Change(
        path=dotted,
        file=spec.id,
        file_path=path,
        before=before,
        after=None,
        action="unset",
        line=loc.line,
        diff=_one_line_diff(text, new_text),
    )
    if not dry_run:
        atomic_write_text(path, new_text, mode=0o644, newline="")
    return change


def _one_line_diff(before: str, after: str) -> list[str]:
    """Unified-ish diff of the lines that actually changed."""
    import difflib  # noqa: PLC0415

    return [
        line
        for line in difflib.unified_diff(
            before.splitlines(), after.splitlines(), lineterm="", n=0
        )
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]


# --------------------------------------------------------------------------
# check / migrate
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One problem (or note) found by :func:`check`."""

    level: str  # "error" | "warning" | "note"
    file: str
    path: str
    message: str


def _flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dicts to dotted keys (stops at non-dict values)."""
    out: dict[str, Any] = {}
    if not isinstance(data, dict):
        return out
    for k, v in data.items():
        dotted = f"{prefix}{k}"
        out[dotted] = v
        if isinstance(v, dict):
            out.update(_flatten(v, f"{dotted}."))
    return out


def check(*, repo_root: Path | None = None) -> list[Finding]:
    """Report missing, unknown, deprecated and unresolvable configuration.

    Covers the faults that currently only surface at runtime: a duplicated
    top-level key, a role bound to an unknown model, a backend selected with
    no credential present, and options a newer release added that this host's
    file has never seen.
    """
    findings: list[Finding] = []
    index = cs.by_path()
    known_dynamic = [k.path for k in cs.schema() if k.dynamic]
    # A nested model contributes leaf keys only (`agent.role`), never the
    # container (`agent`).  Without this the container reads as an unknown
    # key and every structured section in the file is flagged as a typo.
    namespaces = {
        ".".join(k.path.split(".")[:i])
        for k in cs.schema()
        for i in range(1, len(k.path.split(".")))
    }

    for spec in cs.FILES:
        path = cs.resolve_path(spec.id, repo_root=repo_root)
        if not path.is_file():
            findings.append(
                Finding("warning", spec.id, "", f"{spec.filename} not found at {path}")
            )
            continue

        if spec.id != "env":
            text, _ = _read_raw(path)
            for dup in duplicate_top_level_keys(text):
                findings.append(
                    Finding(
                        "error",
                        spec.id,
                        dup,
                        f"declared more than once — YAML keeps only the last "
                        f"block, so earlier settings are silently discarded",
                    )
                )
            try:
                model = cs._import_model(spec.model) if spec.model else None
                if model is not None:
                    model.model_validate(yaml.safe_load(text) or {})
            except Exception as exc:  # noqa: BLE001 — reported, not raised
                findings.append(
                    Finding("error", spec.id, "", f"does not validate: {_first_line(exc)}")
                )

        present = _flatten(read(spec.id, repo_root=repo_root))
        for dotted in sorted(present):
            full = f"env.{dotted}" if spec.id == "env" else dotted
            if full in index:
                continue
            if full in namespaces:
                continue
            if any(full.startswith(f"{d}.") for d in known_dynamic):
                continue
            findings.append(
                Finding(
                    "warning",
                    spec.id,
                    full,
                    "not in the schema — preserved, but check for a typo",
                )
            )

        for key in cs.schema():
            if key.file != spec.id or key.dynamic:
                continue
            local = key.path.split(".", 1)[1] if spec.id == "env" else key.path
            if local in present:
                continue
            if key.required:
                findings.append(
                    Finding("error", spec.id, key.path, "required but not set")
                )
            elif spec.id != "env":
                findings.append(
                    Finding(
                        "note",
                        spec.id,
                        key.path,
                        f"not set; the default {key.default!r} applies. "
                        f"`config migrate` writes it explicitly — which is how "
                        f"an upgrade surfaces options this file has never seen",
                    )
                )

    findings.extend(_check_references(repo_root=repo_root))
    return findings


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__


def _check_references(*, repo_root: Path | None = None) -> list[Finding]:
    """Cross-file references: role→model bindings and backend credentials."""
    findings: list[Finding] = []
    known = _known_model_ids(repo_root=repo_root)
    role_models = read("models", repo_root=repo_root).get("role_models") or {}
    if isinstance(role_models, dict) and known:
        for role, model_id in role_models.items():
            if str(model_id) not in known:
                findings.append(
                    Finding(
                        "error",
                        "models",
                        f"role_models.{role}",
                        f"bound to unknown model id {model_id!r} — this role "
                        f"falls back to the global default at agent boot",
                    )
                )

    # Co-required env keys (today: OpenShell sandbox delegation).  Same shape
    # of fault as the backend credential below — a switch is on and the thing
    # it points at was never configured.
    env_names = read("env", repo_root=repo_root)
    for trigger, required in cs.REQUIRES.items():
        enabled = trigger in env_names or _truthy(os.environ.get(trigger))
        if not enabled:
            continue
        for name in required:
            if name in env_names or os.environ.get(name):
                continue
            findings.append(
                Finding(
                    "error",
                    "env",
                    f"env.{name}",
                    f"{trigger} is set but {name} is not — the agent believes "
                    f"execution is sandboxed while the delegation target is "
                    f"absent, and nothing reports it until a task runs code",
                )
            )

    backend, _ = _dig(read("acc-config", repo_root=repo_root), "llm.backend")
    needed = cs.BACKEND_CREDENTIALS.get(str(backend or ""))
    if needed:
        if needed not in env_names and not os.environ.get(needed):
            findings.append(
                Finding(
                    "error",
                    "env",
                    f"env.{needed}",
                    f"llm.backend is {backend!r} but {needed} is set neither in "
                    f".env nor in the environment — every task will fail to "
                    f"reach the model",
                )
            )
    return findings


def migrate(
    file_id: str, *, repo_root: Path | None = None, dry_run: bool = False
) -> list[Change]:
    """Add options a newer release introduced, with their defaults.

    Existing values are never touched: this only appends keys that are absent.
    Returns the changes made (or that would be made under *dry_run*).
    """
    spec = cs.file_by_id(file_id)
    if not spec.writable:
        return []
    path = cs.resolve_path(file_id, repo_root=repo_root)
    if not path.is_file():
        return []

    present = _flatten(read(file_id, repo_root=repo_root))
    changes: list[Change] = []
    for key in cs.schema():
        if key.file != file_id or key.dynamic or key.required or key.secret:
            continue
        if key.path in present:
            continue
        # A key whose parent block is itself absent will be created along with
        # its parent by the first child written; re-scan per write so the
        # second child lands inside it.
        changes.append(
            set_value(key.path, key.default, repo_root=repo_root, dry_run=dry_run)
        )
        if dry_run:
            present[key.path] = key.default
    return changes
