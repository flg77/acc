"""``acc-cli plan submit`` accepts both JSON and YAML plan files (D2).

The handler previously called ``json.loads`` directly which rejected
the YAML scenario plans landed in PR #29's docs work.  This test
module pins both formats + the malformed-input contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acc.cli.plan_cmd import _parse_plan_text


_VALID_PLAN_DICT = {
    "signal_type": "PLAN",
    "plan_id": "test-plan-1",
    "collective_id": "sol-01",
    "steps": [
        {"step_id": "s1", "role": "coding_agent",
         "depends_on": [], "task_description": "noop"},
    ],
}


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def test_json_plan_parses(tmp_path: Path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(_VALID_PLAN_DICT), encoding="utf-8")
    out = _parse_plan_text(p.read_text(encoding="utf-8"), str(p))
    assert isinstance(out, dict)
    assert out["plan_id"] == "test-plan-1"
    assert out["steps"][0]["role"] == "coding_agent"


def test_json_plan_with_arbitrary_extension_falls_through(tmp_path: Path):
    """A path without .yaml/.yml hits the JSON-first branch."""
    p = tmp_path / "plan.txt"
    p.write_text(json.dumps(_VALID_PLAN_DICT), encoding="utf-8")
    out = _parse_plan_text(p.read_text(encoding="utf-8"), str(p))
    assert isinstance(out, dict)
    assert out["plan_id"] == "test-plan-1"


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------


_VALID_PLAN_YAML = """\
signal_type: "PLAN"
plan_id: "test-plan-yaml"
collective_id: "sol-01"
steps:
  - step_id: "s1"
    role: "coding_agent"
    depends_on: []
    task_description: |
      Generate a simple module.
"""


def test_yaml_plan_parses_with_yaml_extension(tmp_path: Path):
    p = tmp_path / "plan.yaml"
    p.write_text(_VALID_PLAN_YAML, encoding="utf-8")
    out = _parse_plan_text(p.read_text(encoding="utf-8"), str(p))
    assert isinstance(out, dict)
    assert out["plan_id"] == "test-plan-yaml"
    assert out["steps"][0]["task_description"].startswith("Generate")


def test_yaml_plan_parses_with_yml_extension(tmp_path: Path):
    p = tmp_path / "plan.yml"
    p.write_text(_VALID_PLAN_YAML, encoding="utf-8")
    out = _parse_plan_text(p.read_text(encoding="utf-8"), str(p))
    assert isinstance(out, dict)


def test_yaml_extension_fails_loudly_on_invalid_yaml(tmp_path: Path, capsys):
    """A .yaml file with broken YAML must NOT silently fall through to
    JSON — a yaml-extension file should always be YAML.  Operators
    expect a yaml-shaped diagnostic in this case."""
    p = tmp_path / "broken.yaml"
    p.write_text("steps: [\n  not valid: yaml: : :", encoding="utf-8")
    out = _parse_plan_text(p.read_text(encoding="utf-8"), str(p))
    assert out is None
    captured = capsys.readouterr()
    assert "invalid YAML" in captured.err


# ---------------------------------------------------------------------------
# Stdin / generic fallback
# ---------------------------------------------------------------------------


def test_yaml_text_via_unknown_extension_falls_back(tmp_path: Path):
    """A YAML body served from stdin (path_hint == '-') or a
    non-yaml extension must still parse via the JSON-fallback branch.
    """
    out = _parse_plan_text(_VALID_PLAN_YAML, "-")
    assert isinstance(out, dict)
    assert out["plan_id"] == "test-plan-yaml"


def test_malformed_input_returns_none(capsys):
    out = _parse_plan_text("{[ ! not parseable as either }",
                           "stdin.json")
    assert out is None
    err = capsys.readouterr().err
    assert "invalid JSON" in err
