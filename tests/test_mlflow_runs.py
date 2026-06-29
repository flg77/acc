"""Proposal 020 WS-D — MLFlow run-logging seam tests.

Hermetic: no real mlflow / tracking server. Covers (1) the no-op path
when disabled, (2) a recorded-call path with a fake ``mlflow`` module
injected into sys.modules, (3) best-effort resilience (errors swallowed),
and (4) the golden_prompts.persist_results integration calls it.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from acc.backends import mlflow_runs


# ---------------------------------------------------------------------------
# A fake mlflow module — records calls without a tracking server
# ---------------------------------------------------------------------------


class _FakeMlflow(types.ModuleType):
    def __init__(self):
        super().__init__("mlflow")
        self.tracking_uri = None
        self.experiment = None
        self.params: dict = {}
        self.tags: dict = {}
        self.metrics: list[tuple[str, float]] = []
        self.runs_started: list[str] = []
        self.raise_on_metric = False

    def set_tracking_uri(self, uri):
        self.tracking_uri = uri

    def set_experiment(self, name):
        self.experiment = name

    def log_params(self, params):
        self.params.update(params)

    def set_tags(self, tags):
        self.tags.update(tags)

    def log_metric(self, key, value):
        if self.raise_on_metric:
            raise RuntimeError("tracking server down")
        self.metrics.append((key, value))

    def start_run(self, run_name=None):
        self.runs_started.append(run_name or "")
        fake = self

        class _Ctx:
            def __enter__(self_inner):
                return object()

            def __exit__(self_inner, *a):
                return False

        return _Ctx()


@pytest.fixture
def fake_mlflow(monkeypatch):
    fm = _FakeMlflow()
    monkeypatch.setitem(sys.modules, "mlflow", fm)
    monkeypatch.setenv("ACC_MLFLOW_TRACKING_URI", "http://mlflow.test:5000")
    return fm


def _results():
    from acc.golden_prompts import GoldenResult
    return [
        GoldenResult(name="alpha", passed=True, elapsed_ms=120),
        GoldenResult(name="beta", passed=False, elapsed_ms=80,
                     failures=["output_contains: missing 'x'"]),
    ]


# ---------------------------------------------------------------------------
# Disabled / no-op path
# ---------------------------------------------------------------------------


def test_disabled_without_tracking_uri(monkeypatch):
    monkeypatch.delenv("ACC_MLFLOW_TRACKING_URI", raising=False)
    assert mlflow_runs.enabled() is False
    assert mlflow_runs.log_golden_results(_results()) is False


def test_mlflow_run_yields_none_when_disabled(monkeypatch):
    monkeypatch.delenv("ACC_MLFLOW_TRACKING_URI", raising=False)
    with mlflow_runs.mlflow_run(run_name="x") as run:
        assert run is None


def test_disabled_when_mlflow_not_importable(monkeypatch):
    monkeypatch.setenv("ACC_MLFLOW_TRACKING_URI", "http://x:5000")
    # Ensure import fails: shadow with a sentinel that raises on import.
    monkeypatch.setitem(sys.modules, "mlflow", None)  # `import mlflow` → ImportError
    assert mlflow_runs.enabled() is False


# ---------------------------------------------------------------------------
# Enabled / recorded-call path
# ---------------------------------------------------------------------------


def test_enabled_with_fake_mlflow(fake_mlflow):
    assert mlflow_runs.enabled() is True


def test_log_golden_results_records_metrics(fake_mlflow):
    ok = mlflow_runs.log_golden_results(
        _results(), run_meta={"model": "claude-sonnet", "git_sha": "abc1234"},
    )
    assert ok is True
    assert fake_mlflow.tracking_uri == "http://mlflow.test:5000"
    assert fake_mlflow.experiment == "acc-golden-prompts"
    keys = {k for k, _ in fake_mlflow.metrics}
    assert "golden.count" in keys and "golden.pass_rate" in keys
    assert ("pass.alpha", 1.0) in fake_mlflow.metrics
    assert ("pass.beta", 0.0) in fake_mlflow.metrics
    assert ("latency_ms.alpha", 120.0) in fake_mlflow.metrics
    # aggregate pass_rate = 1/2
    assert ("golden.pass_rate", 0.5) in fake_mlflow.metrics
    # params carried from run_meta
    assert fake_mlflow.params.get("model") == "claude-sonnet"


def test_eval_and_reasoning_helpers(fake_mlflow):
    assert mlflow_runs.log_eval_outcome(eval_name="safety_pii", score=0.95) is True
    assert mlflow_runs.log_reasoning_depth(depth=4.0, run_meta={"model": "m"}) is True
    assert ("eval.score", 0.95) in fake_mlflow.metrics
    assert ("reasoning.depth", 4.0) in fake_mlflow.metrics


# ---------------------------------------------------------------------------
# Best-effort resilience
# ---------------------------------------------------------------------------


def test_tracking_outage_never_raises(fake_mlflow):
    fake_mlflow.raise_on_metric = True
    # Must not raise — returns False on the error path.
    assert mlflow_runs.log_golden_results(_results()) is False


def test_key_sanitisation(fake_mlflow):
    from acc.golden_prompts import GoldenResult
    mlflow_runs.log_golden_results([GoldenResult(name="weird name!@#", passed=True, elapsed_ms=10)])
    # the metric key for the prompt must be sanitised (illegal !@# replaced)
    pass_keys = [k for k, _ in fake_mlflow.metrics if k.startswith("pass.")]
    assert pass_keys and all(c not in pass_keys[0] for c in "!@#")


# ---------------------------------------------------------------------------
# persist_results integration
# ---------------------------------------------------------------------------


def test_persist_results_invokes_mlflow(fake_mlflow, tmp_path):
    from acc.golden_prompts import persist_results
    persist_results(_results(), tmp_path / "golden.jsonl",
                    run_meta={"model": "claude-haiku"})
    # JSONL still written
    assert (tmp_path / "golden.jsonl").read_text(encoding="utf-8").count("\n") == 2
    # AND an mlflow run was logged with the same meta
    assert fake_mlflow.params.get("model") == "claude-haiku"
    assert any(k == "golden.count" for k, _ in fake_mlflow.metrics)


def test_persist_results_noop_mlflow_when_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv("ACC_MLFLOW_TRACKING_URI", raising=False)
    from acc.golden_prompts import persist_results
    # Must not raise even though mlflow is unconfigured.
    persist_results(_results(), tmp_path / "g.jsonl", run_meta={"model": "m"})
    assert (tmp_path / "g.jsonl").is_file()


def test_deep_links_none_when_unset(monkeypatch):
    """Proposal G P3 — no tracking URI → no deep links (edge / unconfigured)."""
    monkeypatch.delenv("ACC_MLFLOW_TRACKING_URI", raising=False)
    assert mlflow_runs.tracking_uri() == ""
    assert mlflow_runs.mlflow_trace_url("tk-1") is None
    assert mlflow_runs.mlflow_experiment_url() is None


def test_trace_url_built_when_set(monkeypatch):
    monkeypatch.setenv("ACC_MLFLOW_TRACKING_URI", "https://mlflow.dc.svc:5000/")
    url = mlflow_runs.mlflow_trace_url("tk-abc")
    assert url is not None
    assert url.startswith("https://mlflow.dc.svc:5000/#/traces")
    assert "tk-abc" in url
    # empty task_id → None even when configured
    assert mlflow_runs.mlflow_trace_url("") is None


def test_experiment_url_built_when_set(monkeypatch):
    monkeypatch.setenv("ACC_MLFLOW_TRACKING_URI", "https://mlflow.dc.svc:5000")
    url = mlflow_runs.mlflow_experiment_url()
    assert url is not None
    assert url.startswith("https://mlflow.dc.svc:5000/#/experiments")
