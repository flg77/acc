"""Golden-prompt suite loader + runner (PR-K, D-005).

A golden prompt is a YAML file under ``examples/golden_prompts/`` with
the schema below.  Each carries:

* The prompt the operator would type into the Prompt screen.
* The target role (and optional target_agent_id).
* A list of expectations the reply MUST satisfy for the suite to
  pass — non-empty reply, max latency, required invocations, regex
  match against the output, etc.

The same schema is consumed by all three runner modes documented in
DECISIONS.md D-005:

* **CLI** — ``acc-cli e2e <name>`` / ``acc-cli e2e --all`` (this
  module's ``run_one`` / ``run_all``).
* **TUI Diagnostics pane** (deferred) — would import ``load_all`` +
  ``run_one`` and render results in a DataTable.
* **Scheduled** (deferred) — a maintenance agent fires
  ``run_all(stack_url=…)`` on cron and writes results to LanceDB.

Each runner shares the *expects* checker so a green test under
``acc-cli e2e`` means green in the TUI and in CI too.

Wire shape (one YAML file → one ``GoldenPrompt``)::

    name: "python_webscraper_basic"
    description: "Smoke-test that coding_agent can write a small scraper"
    prompt: |
      Write a Python webscraper that fetches IBM stock prices
      from Yahoo Finance.  Return only the closing price.
    target_role: "coding_agent"
    target_agent_id: ""        # optional; restricts to one agent
    operating_mode: "AUTO"     # optional; defaults to AUTO
    timeout_s: 60.0            # operator-side receive timeout
    expects:
      reply_non_empty: true
      latency_max_ms: 10000
      blocked: false
      output_contains: ["yfinance", "Close"]     # all-of
      output_matches_regex: "import\\s+(requests|urllib|httpx|yfinance)"
      invocations_kind_contains: []              # ["skill", "mcp"]
      invocations_target_contains: []            # ["code_generate"]

All fields except ``name``, ``prompt``, and ``target_role`` are
optional with sensible defaults.

PR-K ships the schema + loader + assertion engine + a 6-prompt
seed library; the runner integrates with the existing
:class:`acc.channels.TUIPromptChannel` so the same code path the
operator's Prompt screen uses is what CI exercises.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("acc.golden_prompts")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class ExpectsBlock(BaseModel):
    """Pass criteria for a golden prompt's reply.  Every field is
    optional — an empty ``expects:`` block means the only assertion
    is "the reply arrived".

    All checks are AND-ed.  Use multiple golden prompts to build
    OR-of-AND coverage."""

    model_config = ConfigDict(extra="forbid")

    reply_non_empty: bool = True
    """The reply's ``output`` must be a non-empty string."""

    latency_max_ms: Optional[int] = None
    """Maximum observed end-to-end latency in milliseconds.
    None disables the latency assertion."""

    blocked: bool = False
    """The reply's ``blocked`` flag must equal this value.  Default
    False means we expect the LLM call to succeed (not Cat-A gated)."""

    output_contains: list[str] = Field(default_factory=list)
    """Every substring must appear in the reply's ``output``
    (case-insensitive).  Empty list = no check."""

    output_matches_regex: Optional[str] = None
    """Compiled with re.IGNORECASE.  re.search semantics (anywhere
    in the output)."""

    invocations_kind_contains: list[str] = Field(default_factory=list)
    """Each named kind (``"skill"`` / ``"mcp"``) must appear among
    the reply's ``invocations``.  Empty = no check."""

    invocations_target_contains: list[str] = Field(default_factory=list)
    """Each named target must appear among the reply's
    ``invocations``.  Useful for ``["code_generate"]`` etc."""


class GoldenPrompt(BaseModel):
    """One golden-prompt definition (one YAML file)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    prompt: str
    target_role: str
    target_agent_id: str = ""
    operating_mode: str = "AUTO"  # D-003 — preview
    timeout_s: float = 60.0
    expects: ExpectsBlock = Field(default_factory=ExpectsBlock)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class GoldenResult(BaseModel):
    """One golden-prompt evaluation result."""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    elapsed_ms: int
    output_excerpt: str = ""
    failures: list[str] = Field(default_factory=list)
    error: str = ""
    """Set when the runner couldn't even reach the reply phase (NATS
    timeout, channel exception).  Distinct from a reply that arrived
    but failed assertions (which lands in ``failures``)."""
    task_id: str = ""
    """The NATS task id this run dispatched (proposal G).  The universal
    join key: tokens / compliance / invocations / the OTel trace are all
    keyed off it, so persisting it on the run record is what lets a later
    history view enrich the run with those signals."""
    agent_id: str = ""
    """The agent that executed the task, when the reply carries it."""
    input_tokens: int = 0
    """Prompt/input tokens the run consumed (proposal G P2); 0 if unreported."""
    cache_read_tokens: int = 0
    """Prompt-cache read tokens (proposal G P2)."""
    compliance_health_score: float = -1.0
    """Compliance-health score [0..1] at completion (proposal G P2).  ``-1.0``
    = not reported (render "—", never a perfect score)."""
    eval_verdict: str = ""
    """The model's ``[EVAL_OUTCOME]`` self-verdict, when present (proposal G P2)."""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _default_root() -> Path:
    """Resolve the golden-prompt directory.

    Precedence:
    1. ``ACC_GOLDEN_PROMPTS_ROOT`` env var (absolute path).
    2. ``<repo>/examples/golden_prompts``.
    3. CWD ``./examples/golden_prompts``.
    """
    import os  # noqa: PLC0415
    env = os.environ.get("ACC_GOLDEN_PROMPTS_ROOT", "").strip()
    if env:
        return Path(env)
    repo_root = Path(__file__).resolve().parent.parent
    candidate = repo_root / "examples" / "golden_prompts"
    if candidate.is_dir():
        return candidate
    return Path("examples/golden_prompts")


def load_one(path: Path) -> GoldenPrompt:
    """Load and validate one golden-prompt YAML file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return GoldenPrompt.model_validate(raw)


def load_all(root: Optional[Path] = None) -> list[GoldenPrompt]:
    """Load every ``*.yaml`` file under *root*.  Skips files that
    fail to parse (logs a warning) so a malformed addition doesn't
    block the rest of the suite.

    Returns the prompts sorted by ``name`` so CI runs are
    deterministic."""
    root = root or _default_root()
    if not root.is_dir():
        logger.warning("golden_prompts: root %s not found", root)
        return []
    prompts: list[GoldenPrompt] = []
    for path in sorted(root.glob("*.yaml")):
        try:
            prompts.append(load_one(path))
        except Exception as exc:
            logger.warning(
                "golden_prompts: skipped %s (%s)", path.name, exc,
            )
    return prompts


# ---------------------------------------------------------------------------
# Assertion engine
# ---------------------------------------------------------------------------


def evaluate(prompt: GoldenPrompt, reply: dict, elapsed_ms: int) -> GoldenResult:
    """Apply *prompt.expects* to a TASK_COMPLETE-shaped *reply* dict.

    Args:
        prompt: The golden-prompt definition.
        reply: The reply payload (a dict matching TASK_COMPLETE).
            Keys read: ``output`` (str), ``blocked`` (bool),
            ``invocations`` (list[dict] with ``kind``, ``target``).
        elapsed_ms: Observed end-to-end latency in milliseconds.

    Returns:
        A :class:`GoldenResult` with ``passed=True`` iff every
        expectation in ``prompt.expects`` was satisfied.
    """
    expects = prompt.expects
    failures: list[str] = []
    output = str(reply.get("output", "") or "")
    blocked = bool(reply.get("blocked", False))
    invocations = reply.get("invocations") or []

    # 1. blocked / non-empty
    if blocked != expects.blocked:
        failures.append(
            f"blocked: expected {expects.blocked}, got {blocked} "
            f"(reason={reply.get('block_reason', '—')!r})"
        )
    if expects.reply_non_empty and not output.strip():
        failures.append("reply was empty")

    # 2. latency
    if expects.latency_max_ms is not None and elapsed_ms > expects.latency_max_ms:
        failures.append(
            f"latency: {elapsed_ms}ms exceeds budget {expects.latency_max_ms}ms"
        )

    # 3. output_contains (all substrings, case-insensitive)
    lower_output = output.lower()
    for needle in expects.output_contains:
        if needle.lower() not in lower_output:
            failures.append(f"output missing substring: {needle!r}")

    # 4. regex
    if expects.output_matches_regex:
        try:
            if not re.search(expects.output_matches_regex, output, re.IGNORECASE):
                failures.append(
                    f"output failed regex: {expects.output_matches_regex!r}"
                )
        except re.error as exc:
            failures.append(f"regex compile error: {exc}")

    # 5. invocations
    seen_kinds = {str(inv.get("kind", "")) for inv in invocations}
    for need in expects.invocations_kind_contains:
        if need not in seen_kinds:
            failures.append(
                f"invocations missing kind={need!r} (saw {sorted(seen_kinds)})"
            )
    seen_targets = {str(inv.get("target", "")) for inv in invocations}
    for need in expects.invocations_target_contains:
        if need not in seen_targets:
            failures.append(
                f"invocations missing target={need!r} (saw {sorted(seen_targets)})"
            )

    excerpt = output[:200] + ("…" if len(output) > 200 else "")
    return GoldenResult(
        name=prompt.name,
        passed=not failures,
        elapsed_ms=elapsed_ms,
        output_excerpt=excerpt,
        failures=failures,
    )


# ---------------------------------------------------------------------------
# Runner (async)
# ---------------------------------------------------------------------------


async def run_one(
    prompt: GoldenPrompt,
    *,
    observer: Any,
    collective_id: str,
) -> GoldenResult:
    """Execute one golden prompt against a live ACC stack.

    Uses the same :class:`acc.channels.TUIPromptChannel` the
    operator's Prompt screen drives — guarantees CI exercises the
    real send / receive correlation path.

    Args:
        prompt: The golden-prompt definition to run.
        observer: A connected ``NATSObserver`` instance (so the
            channel's task-listener registry shares the routing
            backend that processes the inbound TASK_COMPLETE).
        collective_id: NATS collective subject prefix.

    Returns:
        A :class:`GoldenResult`.  When the receive() times out or
        the channel raises, the result has ``passed=False`` and
        ``error`` set; ``failures`` stays empty (those are
        assertion failures, not transport failures).
    """
    from acc.channels.tui import TUIPromptChannel  # noqa: PLC0415

    channel = TUIPromptChannel(observer, collective_id=collective_id)
    started = time.time()
    task_id = ""
    try:
        task_id = await channel.send(
            prompt=prompt.prompt,
            target_role=prompt.target_role,
            target_agent_id=prompt.target_agent_id or None,
        )
        reply = await channel.receive(task_id, timeout=prompt.timeout_s)
    except Exception as exc:
        elapsed_ms = int((time.time() - started) * 1000)
        return GoldenResult(
            name=prompt.name,
            passed=False,
            elapsed_ms=elapsed_ms,
            error=str(exc),
            task_id=task_id,
        )
    finally:
        try:
            await channel.close()
        except Exception:
            pass

    elapsed_ms = int((time.time() - started) * 1000)
    # `reply` is a `PromptResponse` dataclass-ish; build a
    # TASK_COMPLETE-shaped dict the evaluator understands.
    reply_dict = {
        "output": getattr(reply, "output", "") or "",
        "blocked": getattr(reply, "blocked", False),
        "block_reason": getattr(reply, "block_reason", ""),
        "invocations": getattr(reply, "invocations", []) or [],
    }
    result = evaluate(prompt, reply_dict, elapsed_ms)
    # Proposal G — stamp the join keys + the per-task signals the reply now
    # carries (P2: tokens / compliance / self-verdict) so the run record is
    # self-contained for the eval-history pane.
    result.task_id = task_id
    result.agent_id = str(getattr(reply, "agent_id", "") or "")
    result.input_tokens = int(getattr(reply, "input_tokens", 0) or 0)
    result.cache_read_tokens = int(getattr(reply, "cache_read_tokens", 0) or 0)
    result.compliance_health_score = float(
        getattr(reply, "compliance_health_score", -1.0)
    )
    result.eval_verdict = str(getattr(reply, "eval_verdict", "") or "")
    return result


async def run_all(
    prompts: list[GoldenPrompt],
    *,
    observer: Any,
    collective_id: str,
) -> list[GoldenResult]:
    """Run every prompt sequentially.  Sequential (not parallel)
    so the suite's latency budgets are measured against a serial
    operator workflow, not a contended one."""
    results: list[GoldenResult] = []
    for prompt in prompts:
        results.append(await run_one(
            prompt, observer=observer, collective_id=collective_id,
        ))
    return results


# ---------------------------------------------------------------------------
# Formatting helpers (shared between CLI and TUI)
# ---------------------------------------------------------------------------


def persist_results(
    results: list[GoldenResult],
    path: "str | Path | None" = None,
    *,
    run_meta: "Optional[dict]" = None,
) -> None:
    """Record a suite execution: JSONL history (when *path* is given) +
    an MLFlow run (when configured).

    The scheduled maintenance-agent runner passes *path* so a history
    accrues at ``<repo>/test/history/golden.jsonl`` (or wherever the
    operator points ``--history``).  Each row is a self-contained JSON
    object: the GoldenResult fields plus a shared ``run_ts`` and any
    ``run_meta`` (host, model, git sha…).

    *path* is optional so the TUI / CLI can log the MLFlow run WITHOUT
    writing a JSONL file (the TUI keeps its own per-prompt history via
    ``append_run_record``): ``path=None`` skips the JSONL write and still
    logs to MLFlow.

    Mirrors the JSONL-history pattern the operator's
    ``acc-llm-test-history`` skill uses so the two archives are
    greppable with the same tools.  Best-effort: a write failure
    logs but never raises (a broken history file must not fail a
    scheduled run).
    """
    import json as _json  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    run_ts = _time.time()
    meta = dict(run_meta or {})
    if path is not None:
        p = Path(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as fh:
                for r in results:
                    row = r.model_dump()
                    row["run_ts"] = run_ts
                    row.update(meta)
                    fh.write(_json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("golden_prompts: history append failed: %s", exc)

    # Proposal 020 WS-D — also log the suite execution as an MLFlow run
    # when configured (ACC_MLFLOW_TRACKING_URI set + acc[mlflow] installed).
    # Same ``run_meta`` keeps the JSONL history + MLFlow experiment in
    # sync.  Best-effort + lazy: a no-op when MLFlow isn't configured, and
    # a tracking-server outage never fails the run (mirrors the JSONL
    # write posture above).
    try:
        from acc.backends.mlflow_runs import log_golden_results  # noqa: PLC0415
        log_golden_results(results, run_meta=meta)
    except Exception as exc:  # noqa: BLE001
        logger.debug("golden_prompts: mlflow run-log skipped (%s)", exc)


# ---------------------------------------------------------------------------
# PR-Y-2 — writable store, multi-root load, markdown import, capture
# ---------------------------------------------------------------------------
#
# The Diagnostics pane (#9) must be usable: operators expect executed
# prompts to land there for review, to write/edit prompts in-pane, and
# to attach a directory of markdown files that the pane watches.  The
# shipped suite (examples/golden_prompts) is read-only in the image, so
# additions go to a *writable* store and any number of *attached* dirs;
# all are merged by name (later roots override earlier).


def writable_root() -> Path:
    """The writable golden-prompt store.

    Operator-authored prompts (in-pane editor) and captured executed
    prompts land here.  ``ACC_GOLDEN_WRITABLE_ROOT`` overrides; the
    container default ``/app/.acc-golden`` is a writable mount.
    """
    import os  # noqa: PLC0415
    raw = os.environ.get("ACC_GOLDEN_WRITABLE_ROOT", "").strip()
    return Path(raw) if raw else Path("/app/.acc-golden")


def _watch_cfg_path() -> Path:
    return writable_root() / ".watch-dirs"


def attached_watch_dirs() -> list[Path]:
    """Directories the operator attached via the pane's "+ Add" button.

    Persisted one-path-per-line in ``<writable_root>/.watch-dirs`` so
    the attachment survives restarts.  Used as extra load roots."""
    try:
        lines = _watch_cfg_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [
        Path(ln.strip()) for ln in lines
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def add_watch_dir(path: Path | str) -> None:
    """Register *path* as a watched golden-prompt directory (idempotent)."""
    cfg = _watch_cfg_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    entry = str(Path(path))
    existing = {str(p) for p in attached_watch_dirs()}
    if entry in existing:
        return
    with cfg.open("a", encoding="utf-8") as fh:
        fh.write(entry + "\n")


def golden_roots() -> list[Path]:
    """All load roots, lowest-precedence first.

    shipped suite < writable store < attached dirs.  ``load_merged``
    walks them in order so a later root's prompt (same ``name``)
    overrides an earlier one."""
    return [_default_root(), writable_root(), *attached_watch_dirs()]


_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def parse_markdown_prompt(text: str, *, name_hint: str) -> GoldenPrompt:
    """Parse a markdown golden prompt into a :class:`GoldenPrompt`.

    Supports optional YAML front matter delimited by ``---`` lines::

        ---
        target_role: coding_agent
        expects:
          output_contains: ["def "]
        ---
        Write a Python function that returns the nth Fibonacci number.

    The body becomes ``prompt`` (unless front matter sets ``prompt``);
    ``name`` defaults to *name_hint* (the filename stem) and
    ``target_role`` defaults to ``coding_agent`` when not given.
    """
    body = text.lstrip("﻿")
    fm: dict[str, Any] = {}
    m = _FRONT_MATTER_RE.match(body)
    if m:
        fm = yaml.safe_load(m.group(1)) or {}
        if not isinstance(fm, dict):
            fm = {}
        body = m.group(2)
    data: dict[str, Any] = dict(fm)
    data.setdefault("name", name_hint)
    data.setdefault("target_role", "coding_agent")
    if "prompt" not in data:
        data["prompt"] = body.strip()
    return GoldenPrompt.model_validate(data)


def load_one_md(path: Path) -> GoldenPrompt:
    """Load + validate one markdown golden prompt."""
    return parse_markdown_prompt(
        path.read_text(encoding="utf-8"), name_hint=path.stem,
    )


def load_merged(roots: Optional[list[Path]] = None) -> list[GoldenPrompt]:
    """Load ``*.yaml`` + ``*.md`` across all *roots*, merged by name.

    Later roots override earlier ones (writable / attached beat the
    shipped suite).  Malformed files are skipped with a warning so one
    bad addition can't blank the pane.  Sorted by name for determinism.
    """
    roots = roots if roots is not None else golden_roots()
    by_name: dict[str, GoldenPrompt] = {}
    for root in roots:
        if not root:
            continue
        rp = Path(root)
        if not rp.is_dir():
            continue
        for path in sorted(rp.glob("*.yaml")):
            try:
                p = load_one(path)
            except Exception as exc:
                logger.warning("golden_prompts: skipped %s (%s)", path, exc)
                continue
            by_name[p.name] = p
        for path in sorted(rp.glob("*.md")):
            try:
                p = load_one_md(path)
            except Exception as exc:
                logger.warning("golden_prompts: skipped %s (%s)", path, exc)
                continue
            by_name[p.name] = p
    return sorted(by_name.values(), key=lambda gp: gp.name)


def dump_prompt(prompt: GoldenPrompt, path: Path | str) -> Path:
    """Atomically serialise *prompt* to a YAML file at *path*."""
    import os  # noqa: PLC0415
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        prompt.model_dump(), sort_keys=False, allow_unicode=True,
    )
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    return p


def save_prompt(prompt: GoldenPrompt, root: Optional[Path] = None) -> Path:
    """Save *prompt* into the writable store as ``<name>.yaml``."""
    root = root or writable_root()
    return dump_prompt(prompt, Path(root) / f"{prompt.name}.yaml")


def _slugify(text: str) -> str:
    """Lowercase, alnum+underscore slug for a generated prompt name."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return slug[:40] or "captured"


def candidate_from_task(
    prompt_text: str,
    target_role: str,
    *,
    operating_mode: str = "AUTO",
    name: Optional[str] = None,
) -> GoldenPrompt:
    """Build a candidate :class:`GoldenPrompt` from an executed task.

    The Prompt screen calls this on TASK_COMPLETE so an executed prompt
    can be reviewed + persisted as a golden prompt.  The default
    ``expects`` only asserts a non-empty reply — the operator tightens
    it in the in-pane editor."""
    return GoldenPrompt(
        name=name or _slugify(prompt_text),
        description="captured from the Prompt screen",
        prompt=prompt_text,
        target_role=target_role or "coding_agent",
        operating_mode=operating_mode or "AUTO",
    )


def save_candidate(
    prompt_text: str,
    target_role: str,
    *,
    operating_mode: str = "AUTO",
    root: Optional[Path] = None,
) -> Path:
    """Capture an executed prompt into the writable store under a
    unique ``<slug>-<n>.yaml`` filename (never clobbers a prior
    capture).  Returns the written path."""
    root = root or writable_root()
    cand = candidate_from_task(
        prompt_text, target_role, operating_mode=operating_mode,
    )
    base = cand.name
    n = 1
    target = Path(root) / f"{base}.yaml"
    while target.exists():
        n += 1
        target = Path(root) / f"{base}-{n}.yaml"
        cand = candidate_from_task(
            prompt_text, target_role, operating_mode=operating_mode,
            name=f"{base}-{n}",
        )
    return dump_prompt(cand, target)


# ---------------------------------------------------------------------------
# Proposal 044 O2 — durable persistence (export/import) + save history (VC)
# ---------------------------------------------------------------------------
#
# The writable store lives on a named volume, so captures + edits already
# survive a normal restart.  But a volume reset (rebuild, `down -v`, fresh
# host) loses them, and the operator asked for two more guarantees:
#   * a way to get prompts OUT to a host directory and back IN (durable
#     backup that survives even a volume wipe), and
#   * "every save is basically a commit" — an append-only history so the
#     operator can see what was saved when.


def save_history_path() -> Path:
    """The append-only save-history log (the golden-prompt "version
    control").  One JSONL row per Save — durable + greppable."""
    return writable_root() / "history.jsonl"


def version_count(name: str) -> int:
    """How many times *name* has been saved (rows in the history log)."""
    import json as _json  # noqa: PLC0415

    n = 0
    try:
        text = save_history_path().read_text(encoding="utf-8")
    except OSError:
        return 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = _json.loads(line)
        except ValueError:
            continue
        if row.get("name") == name:
            n += 1
    return n


def append_save_history(name: str, content: str, *, action: str = "save") -> int:
    """Append a save-history row for *name* and return its new version
    count (1-based).  Row = ``{ts, name, action, sha256, bytes}``.

    Best-effort: a write failure logs but never raises — the history
    log must never block or fail a save."""
    import hashlib  # noqa: PLC0415
    import json as _json  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    raw = content.encode("utf-8")
    row = {
        "ts": _time.time(),
        "name": name,
        "action": action,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }
    p = save_history_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("golden_prompts: save-history append failed: %s", exc)
    n = version_count(name)
    # Proposal G — keep the exact saved content as a restorable version blob
    # (versions/<slug>/<n>.yaml) alongside the hash-log row.
    try:
        save_version_blob(name, content, n)
    except OSError as exc:
        logger.warning("golden_prompts: version blob write failed: %s", exc)
    return n


def export_store(dest: Path | str, *, root: Optional[Path] = None) -> int:
    """Copy every prompt in the writable store to *dest* as ``<name>.yaml``.

    A durable backup so operator-authored + captured prompts survive a
    volume reset.  Only ``*.yaml``/``*.md`` prompts are written — the
    history log + ``.watch-dirs`` config stay behind.  Returns the count."""
    root = root or writable_root()
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    prompts = load_merged([Path(root)])
    for p in prompts:
        dump_prompt(p, dest / f"{p.name}.yaml")
    return len(prompts)


def import_store(src: Path | str, *, root: Optional[Path] = None) -> int:
    """Copy prompts from *src* into the writable store (persisted).

    Distinct from attach/watch (:func:`add_watch_dir`): import COPIES the
    prompts into the writable volume so they persist even if *src* later
    goes away.  Returns the count imported."""
    root = root or writable_root()
    prompts = load_merged([Path(src)])
    for p in prompts:
        save_prompt(p, Path(root))
    return len(prompts)


# ---------------------------------------------------------------------------
# Proposal G P1 — per-run execution history + version-controlled prompts
# ---------------------------------------------------------------------------
#
# A golden run is no longer ephemeral: each execution appends a record to
# run_history.jsonl carrying the ``task_id`` join key, so the Diagnostics
# pane can show the outcomes of *repeated* runs and (P2) enrich each by
# task_id with tokens / compliance / trace.  Saved prompts keep restorable
# version blobs alongside the v0.5.24 save-history log.


def run_history_path() -> Path:
    """The append-only per-run execution history (proposal G)."""
    return writable_root() / "run_history.jsonl"


def append_run_record(
    result: GoldenResult,
    *,
    collective_id: str = "",
    run_id: str = "",
    run_ts: float = 0.0,
) -> str:
    """Append one execution record for *result* and return its ``run_id``.

    Carries the join keys (``task_id`` / ``agent_id``) so a run can later be
    enriched by task_id.  Best-effort: a write failure logs, never raises."""
    import json as _json  # noqa: PLC0415
    import time as _time  # noqa: PLC0415
    import uuid as _uuid  # noqa: PLC0415

    rid = run_id or _uuid.uuid4().hex
    row = {
        "run_id": rid,
        "prompt_name": result.name,
        "run_ts": run_ts or _time.time(),
        "task_id": result.task_id,
        "agent_id": result.agent_id,
        "collective_id": collective_id,
        "passed": result.passed,
        "elapsed_ms": result.elapsed_ms,
        "failures": list(result.failures),
        "error": result.error,
        "output_excerpt": result.output_excerpt,
        # Proposal G P2 — per-run signals joined by task_id.
        "input_tokens": result.input_tokens,
        "cache_read_tokens": result.cache_read_tokens,
        "compliance_health_score": result.compliance_health_score,
        "eval_verdict": result.eval_verdict,
    }
    p = run_history_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("golden_prompts: run-history append failed: %s", exc)
    return rid


def read_run_history(
    name: Optional[str] = None, *, limit: Optional[int] = None,
) -> list[dict]:
    """Read run_history.jsonl **newest-first**.  Filter to *name* when given;
    cap at *limit*.  Tolerant: skips malformed rows, ``[]`` when absent."""
    import json as _json  # noqa: PLC0415

    rows: list[dict] = []
    try:
        text = run_history_path().read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = _json.loads(line)
        except ValueError:
            continue
        if name is not None and row.get("prompt_name") != name:
            continue
        rows.append(row)
    rows.reverse()
    if limit is not None:
        rows = rows[:limit]
    return rows


def version_dir(name: str) -> Path:
    """Directory holding restorable saved versions of prompt *name*."""
    return writable_root() / "versions" / _slugify(name)


def save_version_blob(name: str, content: str, version: int) -> Path:
    """Persist the exact saved content as ``versions/<slug>/<n>.yaml`` so a
    prior version can be diffed/restored (proposal G)."""
    d = version_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{version}.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def list_versions(name: str) -> list[int]:
    """Sorted version numbers available for *name* (``[]`` when none)."""
    d = version_dir(name)
    if not d.is_dir():
        return []
    out: list[int] = []
    for f in d.glob("*.yaml"):
        try:
            out.append(int(f.stem))
        except ValueError:
            continue
    return sorted(out)


def read_version(name: str, version: int) -> str:
    """The stored content of a specific saved version (raises if absent)."""
    return (version_dir(name) / f"{version}.yaml").read_text(encoding="utf-8")


def diff_versions(name: str, a: int, b: int) -> str:
    """Unified diff between two saved versions of *name* (``a`` → ``b``)."""
    import difflib  # noqa: PLC0415

    left = read_version(name, a).splitlines(keepends=True)
    right = read_version(name, b).splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        left, right, fromfile=f"{name} v{a}", tofile=f"{name} v{b}",
    ))


def format_summary(results: list[GoldenResult]) -> str:
    """Render a one-line-per-prompt summary suitable for stdout."""
    if not results:
        return "(no prompts)"
    lines = []
    passed = sum(1 for r in results if r.passed)
    for r in results:
        marker = "PASS" if r.passed else "FAIL"
        lines.append(f"  [{marker}] {r.name}  ({r.elapsed_ms}ms)")
        for f in r.failures:
            lines.append(f"          · {f}")
        if r.error:
            lines.append(f"          · ERROR: {r.error}")
    lines.append("")
    lines.append(f"  total: {passed}/{len(results)} passed")
    return "\n".join(lines)
