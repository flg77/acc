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


# ---------------------------------------------------------------------------
# base_run_meta + path-less persist (run-logging activation, v0.5.x)
# ---------------------------------------------------------------------------


def test_base_run_meta_seeds_git_sha_host_and_merges_extra(monkeypatch):
    monkeypatch.setenv("ACC_VERSION", "0.5.34-2-gdeadbee")
    meta = mlflow_runs.base_run_meta(collective_id="sol-01", model=None,
                                     source="e2e-cli")
    assert meta["git_sha"] == "0.5.34-2-gdeadbee"   # from ACC_VERSION
    assert meta["host"]                              # gethostname, non-empty
    assert meta["collective_id"] == "sol-01"
    assert meta["source"] == "e2e-cli"
    assert "model" not in meta                       # None values dropped


def test_base_run_meta_falls_back_to_package_version(monkeypatch):
    monkeypatch.delenv("ACC_VERSION", raising=False)
    from acc import __version__
    assert mlflow_runs.base_run_meta()["git_sha"] == __version__


def test_persist_results_path_none_logs_mlflow_without_jsonl(fake_mlflow, tmp_path):
    """TUI/CLI activation path: log the MLFlow run but write NO history file."""
    from acc.golden_prompts import persist_results
    target = tmp_path / "should-not-exist.jsonl"
    persist_results(_results(), None,
                    run_meta=mlflow_runs.base_run_meta(source="tui-diagnostics"))
    assert not target.exists()                       # no JSONL written
    assert fake_mlflow.runs_started                   # an mlflow run was logged
    assert fake_mlflow.params.get("source") == "tui-diagnostics"


def test_persist_results_path_none_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("ACC_MLFLOW_TRACKING_URI", raising=False)
    from acc.golden_prompts import persist_results
    # No path, no mlflow — must be a clean no-op, never raises.
    persist_results(_results(), None, run_meta={"source": "x"})


# ---------------------------------------------------------------------------
# 047 Slice 4 — golden-prompt DEFINITION round-trip (log_prompt / pull_prompts)
# ---------------------------------------------------------------------------


def test_log_prompt_disabled_noop(monkeypatch):
    monkeypatch.delenv("ACC_MLFLOW_TRACKING_URI", raising=False)
    assert mlflow_runs.log_prompt("p", "name: p\n") is False


def test_log_prompt_records_artifact_and_tags(fake_mlflow):
    logged: dict[str, str] = {}
    fake_mlflow.log_text = lambda text, path: logged.update({path: text})
    ok = mlflow_runs.log_prompt("myp", "name: myp\nprompt: hi\n")
    assert ok is True
    assert logged["golden_prompt.yaml"] == "name: myp\nprompt: hi\n"
    assert fake_mlflow.tags.get("acc.artifact") == "golden_prompt"
    assert fake_mlflow.tags.get("acc.prompt_name") == "myp"


def test_log_prompt_requires_name(fake_mlflow):
    fake_mlflow.log_text = lambda *a, **k: None
    assert mlflow_runs.log_prompt("", "x") is False


def test_pull_prompts_disabled_noop(monkeypatch):
    monkeypatch.delenv("ACC_MLFLOW_TRACKING_URI", raising=False)
    assert mlflow_runs.pull_prompts() == []


def test_pull_prompts_reads_latest_per_name(fake_mlflow, tmp_path):
    art = tmp_path / "golden_prompt.yaml"
    art.write_text("name: pulled\nprompt: from-dc\n", encoding="utf-8")

    class _Run:
        def __init__(self, name, rid):
            self.data = type("D", (), {"tags": {"acc.prompt_name": name}})()
            self.info = type("I", (), {"run_id": rid})()

    class _Client:
        def get_experiment_by_name(self, n):
            return type("E", (), {"experiment_id": "exp1"})()

        def search_runs(self, ids, filter_string="", order_by=None):
            # newest first: two 'pulled' runs → latest wins (deduped)
            return [_Run("pulled", "r2"), _Run("pulled", "r1")]

    fake_mlflow.MlflowClient = lambda: _Client()
    fake_mlflow.artifacts = type("A", (), {
        "download_artifacts": staticmethod(
            lambda run_id, artifact_path: str(art)
        ),
    })()

    out = mlflow_runs.pull_prompts()
    assert out == [("pulled", "name: pulled\nprompt: from-dc\n")]


def test_pull_prompts_no_experiment_returns_empty(fake_mlflow):
    fake_mlflow.MlflowClient = lambda: type(
        "C", (), {"get_experiment_by_name": lambda self, n: None},
    )()
    assert mlflow_runs.pull_prompts() == []


def test_pull_prompts_resilient_on_error(fake_mlflow):
    def _boom():
        raise RuntimeError("mlflow down")

    fake_mlflow.MlflowClient = _boom   # MlflowClient() raises
    assert mlflow_runs.pull_prompts() == []
