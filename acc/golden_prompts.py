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
    return evaluate(prompt, reply_dict, elapsed_ms)


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
