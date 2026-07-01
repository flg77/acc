"""MLFlow experiments/runs logging — proposal 020 WS-D (net-new).

The OTel telemetry backend (``metrics_otel`` + ``pipeline_tracing``)
already exports per-task **traces** to MLFlow's ``/v1/traces``.  This
module is the complementary **experiments/runs** layer: it turns a
golden-prompt / eval / reasoning-bench *execution* into one MLFlow run
with **params** (role, model, pack version, git sha) + **metrics**
(pass-rate, per-prompt pass/latency, eval score, reasoning depth), so
"benchmark after a role or model change" lands in MLFlow's
experiment-comparison UI.

Design constraints:

* **Opt-in + lazy.** No-op unless ``ACC_MLFLOW_TRACKING_URI`` is set AND
  the ``mlflow`` package imports.  ``import acc.backends.mlflow_runs`` is
  always safe — mlflow is imported lazily inside the functions.
* **Best-effort.** A tracking-server outage must NEVER fail a suite — the
  same posture as ``golden_prompts.persist_results`` (log + swallow).
* **Off the hot path.** Call this from the runner / CLI / scheduled side,
  never from the agent's per-task pipeline (keeps mlflow out of agent
  pods + the FQDN-egress NetworkPolicy).

Reuses the exact ``GoldenResult`` shape (``name`` / ``passed`` /
``elapsed_ms`` / ``failures``) — no new data model.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator, Optional, Sequence

if TYPE_CHECKING:
    from acc.golden_prompts import GoldenResult

logger = logging.getLogger("acc.backends.mlflow_runs")

# Env contract (mirrors the OTel backend's ``OTEL_*`` convention).
_ENV_TRACKING_URI = "ACC_MLFLOW_TRACKING_URI"
_ENV_EXPERIMENT = "ACC_MLFLOW_EXPERIMENT"
_DEFAULT_EXPERIMENT = "acc-golden-prompts"

# MLFlow caps run/metric/param key lengths + param value length; clip
# defensively so a long git-sha-with-message or model id can't raise.
_MAX_PARAM_LEN = 500
_MAX_KEY_LEN = 250


def enabled() -> bool:
    """True iff MLFlow run-logging is configured (tracking URI set) AND
    the ``mlflow`` package is importable.  Cheap — import attempt is
    cached by the interpreter after first call."""
    if not os.environ.get(_ENV_TRACKING_URI, "").strip():
        return False
    try:
        import mlflow  # noqa: F401, PLC0415
    except Exception:  # noqa: BLE001
        logger.debug(
            "mlflow_runs: %s set but mlflow not importable — install the "
            "acc[mlflow] extra to enable run logging", _ENV_TRACKING_URI,
        )
        return False
    return True


def _experiment_name(override: Optional[str]) -> str:
    return (
        override
        or os.environ.get(_ENV_EXPERIMENT, "").strip()
        or _DEFAULT_EXPERIMENT
    )


def _acc_version() -> str:
    try:
        from acc import __version__  # noqa: PLC0415
        return __version__
    except Exception:  # noqa: BLE001
        return ""


def base_run_meta(**extra: Any) -> dict[str, Any]:
    """Build a ``run_meta`` dict identifying an MLFlow run.

    Seeds ``git_sha`` (the deployed ACC release — ``ACC_VERSION`` if the
    container/CI set it, else the package ``__version__``) and ``host``,
    then merges any caller-supplied ``extra`` (collective_id, model,
    source…), dropping ``None`` values.  Callers pass the result straight
    to :func:`acc.golden_prompts.persist_results` so the run name
    (``golden-<model>-<git_sha>``) and logged params are meaningful
    instead of the bare ``golden-model-`` fallback.  Cheap; never raises."""
    meta: dict[str, Any] = {
        "git_sha": os.environ.get("ACC_VERSION", "").strip() or _acc_version(),
    }
    try:
        import socket  # noqa: PLC0415
        meta["host"] = socket.gethostname()
    except Exception:  # noqa: BLE001
        pass
    meta.update({k: v for k, v in extra.items() if v is not None})
    return meta


def _clip(value: Any, limit: int) -> str:
    s = str(value)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _safe_key(key: str) -> str:
    # MLFlow allows alphanumerics, _, -, ., space, /.  Replace the rest.
    out = "".join(c if (c.isalnum() or c in "_-. /") else "_" for c in str(key))
    return out[:_MAX_KEY_LEN]


@contextmanager
def mlflow_run(
    *,
    experiment: Optional[str] = None,
    run_name: Optional[str] = None,
    params: Optional[dict[str, Any]] = None,
    tags: Optional[dict[str, Any]] = None,
) -> Iterator[Any]:
    """Open an MLFlow run, yield it (or ``None`` when disabled).

    No-op + yields ``None`` when :func:`enabled` is False, so callers can
    always ``with mlflow_run(...) as run:`` unconditionally.  Never
    raises — any mlflow error logs and degrades to the no-op path.
    """
    if not enabled():
        yield None
        return
    try:
        import mlflow  # noqa: PLC0415

        mlflow.set_tracking_uri(os.environ[_ENV_TRACKING_URI].strip())
        mlflow.set_experiment(_experiment_name(experiment))
        with mlflow.start_run(run_name=run_name) as run:
            if params:
                mlflow.log_params(
                    {_safe_key(k): _clip(v, _MAX_PARAM_LEN) for k, v in params.items()}
                )
            if tags:
                mlflow.set_tags(
                    {_safe_key(k): _clip(v, _MAX_PARAM_LEN) for k, v in tags.items()}
                )
            yield run
    except Exception as exc:  # noqa: BLE001
        logger.warning("mlflow_runs: run failed (%s) — skipping", exc)
        yield None


def log_golden_results(
    results: "Sequence[GoldenResult]",
    *,
    run_meta: Optional[dict[str, Any]] = None,
    experiment: Optional[str] = None,
    run_name: Optional[str] = None,
) -> bool:
    """Log a golden-prompt suite execution as one MLFlow run.

    Params come from ``run_meta`` (host / model / git sha / pack version —
    whatever the runner stamps, same dict it passes to
    :func:`acc.golden_prompts.persist_results`).  Metrics:

    * ``golden.count`` / ``golden.passed`` / ``golden.pass_rate`` (aggregate)
    * per-prompt ``pass.<name>`` (1.0/0.0) + ``latency_ms.<name>``

    Returns True when a run was logged, False on the no-op/disabled/error
    path.  Best-effort — never raises.
    """
    if not enabled():
        return False
    results = list(results or [])
    meta = dict(run_meta or {})
    try:
        import mlflow  # noqa: PLC0415

        total = len(results)
        passed = sum(1 for r in results if getattr(r, "passed", False))
        rate = (passed / total) if total else 0.0
        name = run_name or (
            f"golden-{meta.get('model', 'model')}-{meta.get('git_sha', '')[:8]}".rstrip("-")
        )
        with mlflow_run(
            experiment=experiment, run_name=name, params=meta,
            tags={"acc.suite": "golden_prompts"},
        ) as run:
            if run is None:
                return False
            mlflow.log_metric("golden.count", float(total))
            mlflow.log_metric("golden.passed", float(passed))
            mlflow.log_metric("golden.pass_rate", float(rate))
            for r in results:
                rname = _safe_key(getattr(r, "name", "unknown"))
                mlflow.log_metric(f"pass.{rname}", 1.0 if getattr(r, "passed", False) else 0.0)
                elapsed = getattr(r, "elapsed_ms", None)
                if isinstance(elapsed, (int, float)):
                    mlflow.log_metric(f"latency_ms.{rname}", float(elapsed))
        logger.info(
            "mlflow_runs: logged golden suite (%d/%d passed) to experiment %r",
            passed, total, _experiment_name(experiment),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("mlflow_runs: log_golden_results failed (%s)", exc)
        return False


def log_eval_outcome(
    *,
    eval_name: str,
    score: float,
    verdict: str = "",
    run_meta: Optional[dict[str, Any]] = None,
    experiment: Optional[str] = None,
) -> bool:
    """Log a single eval (behavioral / safety) outcome as an MLFlow run.

    For ``acc/pkg/evals.py`` consumers.  Best-effort; returns logged?.
    """
    if not enabled():
        return False
    meta = dict(run_meta or {})
    try:
        import mlflow  # noqa: PLC0415

        with mlflow_run(
            experiment=experiment or "acc-evals",
            run_name=f"eval-{eval_name}",
            params={**meta, "eval_name": eval_name, "verdict": verdict},
            tags={"acc.suite": "evals"},
        ) as run:
            if run is None:
                return False
            mlflow.log_metric("eval.score", float(score))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("mlflow_runs: log_eval_outcome failed (%s)", exc)
        return False


def log_reasoning_depth(
    *,
    depth: float,
    run_meta: Optional[dict[str, Any]] = None,
    experiment: Optional[str] = None,
    run_name: Optional[str] = None,
) -> bool:
    """Log a reasoning-bench depth score as an MLFlow run.

    For the ``acc-bench`` / ``trace_eval`` harness.  Best-effort.
    """
    if not enabled():
        return False
    meta = dict(run_meta or {})
    try:
        import mlflow  # noqa: PLC0415

        with mlflow_run(
            experiment=experiment or "acc-reasoning-bench",
            run_name=run_name or f"reasoning-{meta.get('model', 'model')}",
            params=meta,
            tags={"acc.suite": "reasoning_bench"},
        ) as run:
            if run is None:
                return False
            mlflow.log_metric("reasoning.depth", float(depth))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("mlflow_runs: log_reasoning_depth failed (%s)", exc)
        return False


# ---------------------------------------------------------------------------
# Proposal G P3 — deep links into the MLflow UI.
#
# Gated on the tracking URI ONLY (not on the mlflow package): these are plain
# browser URLs the operator opens, so a TUI/pod without the acc[mlflow] extra
# can still surface them once the DC URI is configured.  Return None when no
# tracking URI is set, so the eval-history pane shows the link only on the DC.
# ---------------------------------------------------------------------------


def tracking_uri() -> str:
    """The configured MLflow tracking URI (``""`` when unset)."""
    return os.environ.get(_ENV_TRACKING_URI, "").strip()


def mlflow_trace_url(task_id: str) -> Optional[str]:
    """Deep link to the MLflow **Traces** view filtered to this task's OTel
    trace.  The agent stamps ``acc.task_id`` on the ``acc.task.process`` root
    span; on the DC the collector fans traces to MLflow's ``/v1/traces``, so a
    golden run's ``task_id`` resolves to its trace.  ``None`` when the tracking
    URI is unset or *task_id* is empty.

    URL shape targets MLflow 3.x trace search — the operator lands on the
    Traces page filtered by the task; adjust the filter syntax per MLflow
    version if it differs."""
    base = tracking_uri()
    if not base or not task_id:
        return None
    from urllib.parse import quote  # noqa: PLC0415

    flt = quote(f'attributes.`acc.task_id` = "{task_id}"')
    return f"{base.rstrip('/')}/#/traces?searchFilter={flt}"


def mlflow_experiment_url(experiment: Optional[str] = None) -> Optional[str]:
    """Deep link to the experiment that golden-suite runs log into (proposal
    G P3).  ``None`` when the tracking URI is unset."""
    base = tracking_uri()
    if not base:
        return None
    from urllib.parse import quote  # noqa: PLC0415

    return f"{base.rstrip('/')}/#/experiments?searchFilter=" + quote(
        f'name = "{_experiment_name(experiment)}"'
    )
