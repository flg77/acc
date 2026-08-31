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

Nothing here mutates anything, and no check ever **reports** a secret value.

That rule was originally written as *no check ever reads a secret value — only
whether a name is present*, which the capability probe could not honour: a keyed
gateway answers 401 to an unauthenticated request, so *what does this endpoint
support* is unanswerable without the credential the operator already configured.
The rule is therefore narrowed to its intent. A check MAY use a configured
credential to make a request the operator explicitly asked for with ``--probe``;
it may never let that value reach a summary, a detail, an error or a log line.
The enforcing half lives in ``acc.endpoint_profile._redact`` plus a sentinel
test, because a rule nothing checks is a preference.
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


@register("endpoint-capability")
def check_endpoint_capabilities(ctx: Context) -> Iterable[Result]:
    """What does each configured endpoint actually support?

    ``check_endpoints`` above answers *is it up*. This answers *what is it*,
    which is the question three shipped optimisations silently depend on: the
    PR-CA1 stable prefix assumes the server prefix-caches, PR-CA2 skips the
    client hint on vLLM/Ollama for the same reason, and the local embedder in
    ``acc/backends/llm_vllm.py`` exists because the served model *may* not
    embed.  None of the three was ever verified against a running server.

    One dense row per endpoint rather than a block per capability: a health
    report is scanned, and nine rows per model is not.  The reason for an
    ``unknown`` goes in the **summary**, not the detail — the renderer
    suppresses detail on OK rows, and an unknown is an OK row (it is not a
    fault), so a reason in the detail would never be seen.

    Nothing here can be BROKEN.  An endpoint without ``/tokenize`` is normal,
    and a transient network fault must not decide an exit code.
    """
    if not ctx.probe_endpoints:
        yield Result(
            "endpoint-capability", Severity.OK, "capability probing not requested",
            detail="Pass --probe to ask each endpoint what it supports.",
        )
        return

    from acc.endpoint_profile import probe_endpoint  # noqa: PLC0415
    from acc.models import load_models  # noqa: PLC0415

    entries = sorted(load_models(), key=lambda e: e.model_id)
    if not entries:
        yield Result("endpoint-capability", Severity.OK, "no models configured")
        return

    # Probe once per distinct endpoint+model, but report once per *entry*.
    #
    # The two are not the same, and conflating them hid a real case: two
    # registry entries can name the same served model and declare DIFFERENT
    # windows. Deduplicating the rows as well as the probes would validate only
    # the first entry's declaration and silently skip the second — which is
    # exactly the drift this check exists to surface.
    #
    # The key carries the model, unlike ``check_endpoints`` which dedups by
    # root: two models on one vLLM have different capabilities.
    cache: dict[tuple[str, str, str], Any] = {}
    for entry in entries:
        key = (entry.backend, (entry.base_url or "").rstrip("/"), entry.model)
        if key not in cache:
            cache[key] = probe_endpoint(
                entry, timeout_s=ctx.timeout_s, environ=ctx.environ,
            )
        yield from _capability_results(entry, cache[key])


def _capability_results(entry: Any, profile: Any) -> Iterable[Result]:
    """Render one profile as a single Result.  Never raises, never BROKEN."""
    parts: list[str] = []
    details: list[str] = []
    severity = Severity.OK

    # --- window -----------------------------------------------------------
    if profile.served_window.known:
        window = f"window {profile.served_window.value}"
        scaling = profile.window_scaling
        if scaling.known and scaling.value.get("direction") == "reduced":
            window += f" ({scaling.value['ratio']:.0%} of native)"
        elif scaling.known and scaling.value.get("direction") == "extended":
            # Never "YaRN".  Served-exceeds-native proves rescaling is
            # configured; nothing visible here says which kind.
            window += f" ({scaling.value['ratio']:.1f}x native, rescaled)"
        parts.append(window)

        # ``ModelEntry.context_window`` arrives with
        # ``20260826-context-budget``.  Until it exists there is nothing to
        # reconcile against, so this stays quiet rather than inventing a
        # comparison.
        declared = int(getattr(entry, "context_window", 0) or 0)
        if declared and declared != profile.served_window.value:
            severity = _worse(severity, Severity.DRIFTED)
            details.append(
                f"models.yaml declares {declared}; the server serves "
                f"{profile.served_window.value}. Declaring less than is served is a "
                "legitimate choice (KV headroom for co-tenants); declaring more "
                "overflows."
            )
    else:
        parts.append(f"window unknown ({profile.served_window.note})")

    # --- prefix cache -----------------------------------------------------
    # Two separate facts, kept apart: *configured on* is a label and certain,
    # *hitting* needs traffic before it means anything.
    if profile.prefix_cache_enabled.known and not profile.prefix_cache_enabled.value:
        severity = _worse(severity, Severity.DEGRADED)
        parts.append("prefix cache OFF")
        details.append(
            "ACC restructured its prompts (PR-CA1) so the per-role prefix stays "
            "cacheable; with caching disabled that design cost buys nothing here."
        )
    elif profile.prefix_cache_hits.known:
        hits = profile.prefix_cache_hits.value
        if hits.get("rate") is None:
            parts.append("prefix cache on, cold")
        elif hits["rate"] < 0.2:
            severity = _worse(severity, Severity.DEGRADED)
            parts.append(f"prefix cache {hits['rate']:.0%} hit")
            details.append(
                f"{hits['hits']} of {hits['queries']} queried tokens hit. Either the "
                "server is still warming, or ACC's per-role prefix is not "
                "byte-stable; tests/test_prompt_prefix_stability_real_roles.py "
                "settles the second."
            )
        else:
            parts.append(f"prefix cache {hits['rate']:.0%} hit")
    else:
        parts.append("prefix cache unknown")

    # --- the rest ---------------------------------------------------------
    if profile.tokenize.known:
        parts.append("tokenize " + ("yes" if profile.tokenize.value else "no"))
    if profile.embeddings.known:
        parts.append("embeddings " + ("yes" if profile.embeddings.value else "no"))
    if profile.kv_cache.known and profile.kv_cache.value.get("pool_tokens"):
        pool = profile.kv_cache.value["pool_tokens"]
        parts.append(f"KV pool {pool}t")
        if profile.served_window.known and profile.served_window.value:
            details.append(
                f"KV pool holds ~{pool / profile.served_window.value:.1f} concurrent "
                "full-window sequences; past that vLLM preempts and recomputes."
            )

    if profile.errors:
        severity = _worse(severity, Severity.DEGRADED)
        details.extend(profile.errors)

    yield Result(
        name="endpoint-capability",
        severity=severity,
        # The model id goes in the SUMMARY, not just ``subject``: the renderer
        # prints ``[subject]`` only for rows that are not OK, so on a healthy
        # deployment with two models on one backend the rows would otherwise be
        # indistinguishable.
        summary=f"{entry.model_id} ({profile.backend}): "
        + (" | ".join(parts) or "nothing determined"),
        detail=" ".join(details),
        subject=entry.model_id,
    )


#: Escalation order for this check alone.  ``worst()`` above ranks a whole run
#: and includes BROKEN, which is unreachable here by design (REQ-CHK-003).
_ESCALATION = (Severity.OK, Severity.DRIFTED, Severity.DEGRADED)


def _worse(a: Severity, b: Severity) -> Severity:
    return max(a, b, key=_ESCALATION.index)


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
