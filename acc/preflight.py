"""One read-only health report for a deployment's configuration.

Five configuration faults have each cost real time on a live deployment, and
every one of them was silent: the thing that was misconfigured reported success
and the consequence appeared somewhere else, much later.  This module is the
place that looks for them on purpose.

The severity classes exist because the operator's next action differs:

===========  =========================================================
**BROKEN**   the deployment cannot work as configured — an unknown
             ``model_id``, a missing key name, an unreadable file.
             Fix before anything else; this is what sets the exit code.
**DEGRADED** configured correctly, but something it depends on is
             unhealthy right now — an endpoint that will not answer.
             Often transient; worth knowing, not worth blocking on.
**DRIFTED**  declared state and running state disagree — configuration
             edited without a restart.  Nothing is wrong with the
             files; what is running is simply not what they say.
===========  =========================================================

One implementation, three surfaces
----------------------------------
The checks are a registry of plain callables and :func:`run` returns data, not
text.  ``acc-cli doctor`` renders it, and so can the TUI and the web GUI.  That
is deliberate: a second implementation is exactly how three surfaces start
disagreeing about whether a deployment is healthy, and then the operator has to
work out which one is lying.

Nothing here mutates anything, and no check ever reads a secret **value** —
only whether a name is present.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger("acc.preflight")


class Severity(str, Enum):
    """Why a check failed, which decides what the operator does next."""

    BROKEN = "broken"
    DEGRADED = "degraded"
    DRIFTED = "drifted"
    OK = "ok"


#: Only BROKEN sets a non-zero exit.  A degraded endpoint is frequently a
#: transient upstream blip, and a monitor that pages on it teaches people to
#: ignore the page.
FAILING = (Severity.BROKEN,)


@dataclass(frozen=True)
class Result:
    """The outcome of one check."""

    name: str
    severity: Severity
    summary: str
    detail: str = ""
    subject: str = ""  # the role / key / file at fault, when there is one

    @property
    def ok(self) -> bool:
        return self.severity is Severity.OK

    def as_dict(self) -> dict[str, str]:
        return {
            "check": self.name,
            "severity": self.severity.value,
            "summary": self.summary,
            "detail": self.detail,
            "subject": self.subject,
        }


@dataclass
class Context:
    """What the checks are allowed to look at.

    Passed in rather than read ad hoc so a check is testable against a fixture
    directory and so nothing reaches for global state mid-run.

    Attributes:
        repo_root: where configuration files are resolved from.
        environ: the environment; checks read key **names**, never values.
        probe_endpoints: perform network probes.  Off by default — a health
            command must be safe to run on any cadence, and a check that dials
            out is neither fast nor side-effect free.
        timeout_s: per-probe timeout when probing is on.
    """

    repo_root: Path | None = None
    environ: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    probe_endpoints: bool = False
    timeout_s: float = 5.0


Check = Callable[[Context], Iterable[Result]]

_REGISTRY: list[tuple[str, Check]] = []


def register(name: str) -> Callable[[Check], Check]:
    """Add a check to the registry under *name*."""

    def _decorate(fn: Check) -> Check:
        _REGISTRY.append((name, fn))
        return fn

    return _decorate


def registry() -> list[tuple[str, Check]]:
    return list(_REGISTRY)


def run(ctx: Context | None = None, *, only: str | None = None) -> list[Result]:
    """Run every registered check and return its results.

    A check that raises is reported as BROKEN rather than propagating: a health
    command that dies on its first surprise tells the operator nothing about
    the other nine things it was going to look at.
    """
    ctx = ctx or Context()
    out: list[Result] = []
    for name, check in _REGISTRY:
        if only and name != only:
            continue
        try:
            out.extend(check(ctx))
        except Exception as exc:  # noqa: BLE001 — reported, never raised
            logger.exception("preflight: check %r raised", name)
            out.append(
                Result(
                    name=name,
                    severity=Severity.BROKEN,
                    summary="check itself failed",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
    return out


def worst(results: Iterable[Result]) -> Severity:
    order = [Severity.OK, Severity.DRIFTED, Severity.DEGRADED, Severity.BROKEN]
    return max((r.severity for r in results), key=order.index, default=Severity.OK)


def exit_code(results: Iterable[Result]) -> int:
    """Non-zero when any BROKEN check failed."""
    return 1 if any(r.severity in FAILING for r in results) else 0


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

#: How a configstore finding maps onto a preflight severity.  configstore is
#: the single source for "is this configuration self-consistent"; preflight
#: classifies and presents it, and adds the checks that need the world
#: (endpoints, running containers) rather than just the files.
_LEVEL_TO_SEVERITY = {
    "error": Severity.BROKEN,
    "warning": Severity.DEGRADED,
    "note": Severity.OK,
}


def _first_clause(message: str) -> str:
    """A one-line summary of a finding, without mangling it.

    Splitting on "." breaks on "llm.backend"; the useful cut is the first
    line, trimmed, with the full text still carried in ``detail``.
    """
    line = message.strip().splitlines()[0].strip()
    return line if len(line) <= 110 else line[:107] + "..."


@register("configuration")
def check_configuration(ctx: Context) -> Iterable[Result]:
    """Everything the configuration schema can decide from the files alone.

    Deliberately delegates to :func:`acc.configstore.check` instead of
    re-deriving the same rules.  Duplicating them is how the CLI and the TUI
    end up disagreeing about whether a role is bound to a real model.
    """
    from acc import configstore as store  # noqa: PLC0415

    findings = store.check(repo_root=ctx.repo_root)
    if not findings:
        yield Result("configuration", Severity.OK, "configuration is consistent")
        return

    reported = False
    for finding in findings:
        severity = _LEVEL_TO_SEVERITY.get(finding.level, Severity.DEGRADED)
        if severity is Severity.OK:
            continue  # a key left at its default is not a fault
        reported = True
        yield Result(
            name="configuration",
            severity=severity,
            summary=_first_clause(finding.message),
            detail=finding.message,
            subject=finding.path or finding.file,
        )
    if not reported:
        yield Result("configuration", Severity.OK, "configuration is consistent")


@register("duplicate-keys")
def check_duplicate_keys(ctx: Context) -> Iterable[Result]:
    """A top-level key declared twice: YAML keeps the last, silently.

    This is why the deployment-profile tooling had to fence its edits between
    markers — two ``role_models:`` blocks are valid YAML and the earlier one
    simply vanishes.
    """
    from acc import configschema as schema  # noqa: PLC0415
    from acc import configstore as store  # noqa: PLC0415

    found = False
    for spec in schema.FILES:
        if spec.id == "env":
            continue
        path = schema.resolve_path(spec.id, repo_root=ctx.repo_root)
        if not path.is_file():
            continue
        text, _ = store._read_raw(path)
        for key in store.duplicate_top_level_keys(text):
            found = True
            yield Result(
                name="duplicate-keys",
                severity=Severity.BROKEN,
                summary=f"{key!r} is declared twice in {spec.filename}",
                detail=(
                    "YAML keeps only the last block, so the earlier settings are "
                    "discarded without a word."
                ),
                subject=f"{spec.id}:{key}",
            )
    if not found:
        yield Result("duplicate-keys", Severity.OK, "no duplicated top-level keys")


@register("key-names")
def check_key_names(ctx: Context) -> Iterable[Result]:
    """Every ``api_key_env`` a model refers to must exist in the environment.

    Reads the **name**, never the value: a preflight report that could print a
    credential is a preflight report nobody can paste into an issue.
    """
    from acc.models import load_models, load_role_chains  # noqa: PLC0415

    # ONLY models this deployment actually uses.  The shipped registry lists
    # every provider ACC can talk to, and a deployment is expected to hold
    # credentials for the one or two it chose — flagging the other twelve is a
    # false-positive storm that trains the operator to ignore the report.
    in_use: set[str] = set()
    for chain in load_role_chains().values():
        in_use.update(chain)

    unused_missing = 0
    flagged = False
    for entry in load_models():
        name = (entry.api_key_env or "").strip()
        if not name:
            continue
        if name in ctx.environ and str(ctx.environ.get(name, "")).strip():
            continue
        if entry.model_id not in in_use:
            unused_missing += 1
            continue
        flagged = True
        yield Result(
            name="key-names",
            severity=Severity.BROKEN,
            summary=f"{name} is not set, but model {entry.model_id!r} needs it",
            detail=(
                "A role is bound to this model and it declares api_key_env; "
                "without that variable every call it makes is rejected."
            ),
            subject=entry.model_id,
        )
    if not flagged:
        extra = (
            f" ({unused_missing} unused registry model(s) also lack theirs, "
            f"which only matters if a role is bound to them)"
            if unused_missing
            else ""
        )
        yield Result(
            "key-names",
            Severity.OK,
            f"every key name a bound model needs is present{extra}",
        )


@register("role-models")
def check_role_models(ctx: Context) -> Iterable[Result]:
    """Every role->model binding, including each entry of a failover chain.

    ``configstore.check`` validates the primary; a chain's later entries are
    just as capable of naming a model that does not exist, and a chain whose
    secondary is a typo provides no failover at all — while looking configured.
    """
    from acc.models import load_models, load_role_chains  # noqa: PLC0415

    known = {m.model_id for m in load_models()}
    if not known:
        yield Result(
            "role-models",
            Severity.BROKEN,
            "the model registry is empty or unreadable",
            detail="Every role falls back to the global default in this state.",
            subject="models.yaml",
        )
        return

    bad = False
    for role, chain in sorted(load_role_chains().items()):
        for position, model_id in enumerate(chain):
            if model_id in known:
                continue
            bad = True
            where = "primary" if position == 0 else f"fallback #{position}"
            yield Result(
                name="role-models",
                severity=Severity.BROKEN,
                summary=f"role {role!r} {where} names unknown model {model_id!r}",
                detail=(
                    "Resolved at agent boot; an unknown id silently falls back to "
                    "the global default, so the role runs on a model nobody chose."
                ),
                subject=role,
            )
    if not bad:
        yield Result("role-models", Severity.OK, "every role->model binding resolves")


@register("sandbox")
def check_sandbox(ctx: Context) -> Iterable[Result]:
    """Sandbox delegation switched on with no gateway to delegate to.

    ACC's sandboxed execution hands the work to an OpenShell gateway.  With the
    switch on and the gateway absent, the agent believes its code execution is
    contained while nothing is actually mediating it — and the first sign is a
    task failing to run code.
    """
    from acc import configschema as schema  # noqa: PLC0415

    env_path = schema.resolve_path("env", repo_root=ctx.repo_root)
    declared: set[str] = set()
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                declared.add(line.split("=", 1)[0].strip())

    def _present(name: str) -> bool:
        return name in declared or bool(str(ctx.environ.get(name, "")).strip())

    enabled = str(ctx.environ.get("ACC_SANDBOX_ENABLED", "")).strip().lower() in (
        "1", "true", "yes", "on",
    ) or "ACC_SANDBOX_ENABLED" in declared

    if not enabled:
        yield Result("sandbox", Severity.OK, "sandbox delegation is off")
        return
    if not _present("OPENSHELL_GATEWAY"):
        yield Result(
            name="sandbox",
            severity=Severity.BROKEN,
            summary="sandbox delegation is on but OPENSHELL_GATEWAY is unset",
            detail=(
                "The runtime has nothing to delegate execution to. Nothing "
                "reports this until a task tries to run code."
            ),
            subject="OPENSHELL_GATEWAY",
        )
        return
    yield Result("sandbox", Severity.OK, "sandbox delegation is configured")


@register("drift")
def check_drift(ctx: Context) -> Iterable[Result]:
    """Configuration edited more recently than the process that read it.

    Role->model bindings resolve at agent **boot**.  An edit after that point is
    on disk, correct, and not in effect — the single most confusing state to
    debug, because every file the operator inspects says the right thing.
    """
    from acc import configschema as schema  # noqa: PLC0415

    started = ctx.environ.get("ACC_AGENT_STARTED_AT", "").strip()
    if not started:
        yield Result(
            "drift",
            Severity.OK,
            "no running agent to compare against",
            detail=(
                "ACC_AGENT_STARTED_AT is unset, so this check has nothing to "
                "compare configuration mtimes with."
            ),
        )
        return
    try:
        started_at = float(started)
    except ValueError:
        yield Result(
            "drift", Severity.DEGRADED,
            "ACC_AGENT_STARTED_AT is not a unix timestamp",
            detail=f"got {started!r}",
        )
        return

    stale: list[str] = []
    for spec in schema.FILES:
        path = schema.resolve_path(spec.id, repo_root=ctx.repo_root)
        if path.is_file() and path.stat().st_mtime > started_at:
            stale.append(spec.filename)
    if stale:
        yield Result(
            name="drift",
            severity=Severity.DRIFTED,
            summary=f"{', '.join(stale)} changed after the agent started",
            detail=(
                "Bindings resolve at boot, so these edits are on disk but not in "
                f"effect. Restart to apply. (agent up {int(time.time() - started_at)}s)"
            ),
            subject=stale[0],
        )
        return
    yield Result("drift", Severity.OK, "running state matches configuration")


@register("endpoints")
def check_endpoints(ctx: Context) -> Iterable[Result]:
    """Can the configured endpoints actually be reached?

    Off unless asked for.  Probing the **gateway root** matters as much as the
    model: a gateway-wide outage and one bad model id look identical from a
    single model probe, and they need completely different responses.
    """
    if not ctx.probe_endpoints:
        yield Result(
            "endpoints", Severity.OK, "endpoint probing not requested",
            detail="Pass --probe to dial the configured endpoints.",
        )
        return

    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    from acc.models import load_models  # noqa: PLC0415

    roots: dict[str, str] = {}
    for entry in load_models():
        base = (entry.base_url or "").strip()
        if base:
            roots.setdefault(base.rstrip("/"), entry.model_id)
    if not roots:
        yield Result("endpoints", Severity.OK, "no HTTP endpoints configured")
        return

    for base, model_id in sorted(roots.items()):
        try:
            request = urllib.request.Request(base, method="GET")
            with urllib.request.urlopen(request, timeout=ctx.timeout_s) as resp:
                code = resp.status
        except urllib.error.HTTPError as exc:
            # A 401/404 from the root still proves the gateway is answering,
            # which is the thing being tested here.
            code = exc.code
        except Exception as exc:  # noqa: BLE001
            yield Result(
                name="endpoints",
                severity=Severity.DEGRADED,
                summary=f"{base} is unreachable",
                detail=f"{type(exc).__name__}: {exc} (first seen for {model_id})",
                subject=base,
            )
            continue
        yield Result(
            "endpoints", Severity.OK, f"{base} answered ({code})", subject=base
        )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report(results: list[Result]) -> dict[str, Any]:
    """A machine-readable report, for ``--json`` and for monitoring."""
    counts: dict[str, int] = {}
    for r in results:
        counts[r.severity.value] = counts.get(r.severity.value, 0) + 1
    return {
        "healthy": exit_code(results) == 0,
        "worst": worst(results).value,
        "counts": counts,
        "results": [r.as_dict() for r in results],
    }
