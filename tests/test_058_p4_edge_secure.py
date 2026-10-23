"""058 Phase 4 -- the edge-secure profile: guardrails that block, MLflow,
promptfoo on the golden prompts, garak as a release gate.

* ``edge-secure`` loads, and its guardrail / gate settings are posture.
* The WebGUI may switch promptfoo (058 Q6), never the garak gate.
* promptfoo's config is generated from the golden prompts.
* garak's report gates against a baseline.
* MLflow is attached by the profile's ``evals.mlflow_tracking_uri``.
"""

from __future__ import annotations

import json

import pytest

from acc import profiles
from acc.evals import garak
from acc.evals.promptfoo import build_config
from acc.golden_prompts import ExpectsBlock, GoldenPrompt


def test_edge_secure_loads_and_its_guardrails_are_posture():
    p = profiles.load_profile("edge_secure")
    posture = p.posture_changes()
    assert posture["compliance.owasp_enforce"] is True
    assert posture["evals.garak_gate"] is True
    assert "evals.promptfoo_enabled" not in posture, "promptfoo is the operator's switch (Q6)"
    assert {"ACC_SANDBOX_NAME", "ACC_MLFLOW_TRACKING_URI"} <= set(p.requires_env)


def test_the_evals_keys_are_in_the_schema():
    from acc import configschema

    keys = {k.path for k in configschema.schema(refresh=True)}
    assert {"evals.promptfoo_enabled", "evals.garak_gate", "evals.mlflow_tracking_uri"} <= keys


# ---------------------------------------------------------------------------
# promptfoo
# ---------------------------------------------------------------------------


def test_promptfoo_config_from_the_golden_prompts():
    prompts = [
        GoldenPrompt(name="getdone_a", prompt="write it", target_role="coding_agent",
                     expects=ExpectsBlock(output_contains=["def test"], output_matches_regex="assert")),
        GoldenPrompt(name="other", prompt="hi", target_role="assistant"),
    ]
    cfg = build_config(prompts, base_url="http://acc-webgui:8080/v1/", name_prefix="getdone_")
    assert cfg["prompts"] == ["{{prompt}}"]
    assert cfg["providers"] == [{"id": "openai:chat:coding_agent", "label": "coding_agent",
                                 "config": {"apiBaseUrl": "http://acc-webgui:8080/v1",
                                            "apiKeyEnvar": "ACC_COMPAT_API_KEY"}}]
    (test,) = cfg["tests"]
    assert test["vars"] == {"prompt": "write it"} and test["providers"] == ["coding_agent"]
    assert {"type": "icontains", "value": "def test"} in test["assert"]
    assert {"type": "regex", "value": "assert"} in test["assert"]


def test_evals_cli_writes_the_promptfoo_config(tmp_path, monkeypatch):
    import argparse

    from acc.cli import evals_cmd

    out = tmp_path / "promptfooconfig.yaml"
    parser = argparse.ArgumentParser()
    evals_cmd.register(parser.add_subparsers())
    args = parser.parse_args(["evals", "promptfoo-config", "--out", str(out), "--prefix", "getdone_"])
    assert args.func(args) == 0
    import yaml
    cfg = yaml.safe_load(out.read_text())
    assert {t["description"] for t in cfg["tests"]} >= {"getdone_fix_failing_test", "getdone_cli_flag"}


# ---------------------------------------------------------------------------
# garak
# ---------------------------------------------------------------------------


def _report(*rows):
    return [json.dumps({"entry_type": "eval", **r}) for r in rows] + [json.dumps({"entry_type": "attempt"})]


def test_garak_report_is_summarised_per_probe():
    s = garak.summarise(_report(
        {"probe": "promptinject.HijackHateHumans", "passed": 90, "total": 100},
        {"probe": "promptinject.HijackKillHumans", "passed": 10, "total": 100},
        {"probe": "dan.Dan_11_0", "passed": 5, "total": 5},
    ))
    assert s["promptinject"] == {"passed": 100, "total": 200, "rate": 0.5}
    assert s["dan"]["rate"] == 1.0


def test_the_first_run_becomes_the_baseline():
    assert garak.gate({"dan": {"rate": 0.9}}, None) == (True, [])


def test_a_drop_beyond_tolerance_blocks_promotion():
    ok, why = garak.gate({"dan": {"rate": 0.80, "passed": 80, "total": 100}},
                         {"dan": {"rate": 0.95}}, tolerance=0.02)
    assert not ok and why[0].startswith("dan: 80.00% < baseline 95.00%")


def test_a_probe_that_was_not_run_blocks_promotion():
    ok, why = garak.gate({}, {"leakreplay": {"rate": 1.0}})
    assert not ok and "not run" in why[0]


def test_garak_gate_cli_exit_codes(tmp_path):
    import argparse

    from acc.cli import evals_cmd

    report = tmp_path / "garak.report.jsonl"
    base = tmp_path / "baseline.json"
    parser = argparse.ArgumentParser()
    evals_cmd.register(parser.add_subparsers())

    report.write_text("\n".join(_report({"probe": "dan.X", "passed": 95, "total": 100})))
    a = parser.parse_args(["evals", "garak-gate", "--report", str(report), "--baseline", str(base)])
    assert a.func(a) == 0 and base.is_file(), "first run passes and becomes the baseline"

    report.write_text("\n".join(_report({"probe": "dan.X", "passed": 50, "total": 100})))
    assert a.func(a) == 1, "a regression blocks"


def test_garak_command_targets_the_compat_endpoint():
    argv = garak.command(base_url="http://acc-webgui:8080/v1/", model="assistant")
    assert argv[argv.index("--model_type") + 1] == "openai.OpenAICompatible"
    assert "uri=http://acc-webgui:8080/v1" in argv and "promptinject,dan,leakreplay" in argv


# ---------------------------------------------------------------------------
# MLflow and the WebGUI switch
# ---------------------------------------------------------------------------


def test_the_profile_attaches_mlflow(monkeypatch):
    from acc.backends import mlflow_runs

    monkeypatch.delenv("ACC_MLFLOW_TRACKING_URI", raising=False)
    fake = type("C", (), {"evals": type("E", (), {"mlflow_tracking_uri": "http://mlflow:5000"})()})()
    monkeypatch.setattr("acc.config.load_config", lambda *a, **k: fake)
    assert mlflow_runs._configured_uri() == "http://mlflow:5000"


def test_the_webgui_may_switch_promptfoo_but_not_the_garak_gate(monkeypatch):
    pytest.importorskip("fastapi")
    from acc.webgui.routes_config import _posture_keys

    assert "evals.garak_gate" in _posture_keys()
    assert "evals.garak_probes" in _posture_keys(), "dropping a probe weakens the gate"
    assert "compliance.owasp_enforce" in _posture_keys()
    assert "evals.promptfoo_enabled" not in _posture_keys()
