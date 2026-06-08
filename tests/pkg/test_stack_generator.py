"""D5 — `new-stack` collective.yaml generator."""

from __future__ import annotations

import yaml

import pytest

from acc.collective import CollectiveSpec
from acc.pkg.stack import CONTROL_FULL, generate_stack, main, render_stack_yaml


def test_full_profile_has_all_control_plus_domain():
    spec = generate_stack(
        "fsi-dc", packs=["@acc/capital-markets-roles@^0.1"],
        agents=["equity_analyst"], profile="full")
    roles = [a["role"] for a in spec["agents"]]
    assert set(CONTROL_FULL) <= set(roles)
    assert "equity_analyst" in roles
    assert spec["required_packages"] == ["@acc/capital-markets-roles@^0.1"]
    # validates against the real schema
    CollectiveSpec.model_validate(spec)


def test_edge_min_profile_is_reduced_running_set():
    spec = generate_stack("fsi-edge", packs=["@acc/capital-markets-roles@^0.1"],
                          agents=["equity_analyst"], profile="edge-min")
    control = [a["role"] for a in spec["agents"] if a["cluster_id"] == "ctl"]
    assert control == ["arbiter", "ingester", "observer"]   # governance present, lean
    assert "arbiter" in control                              # arbiter mandatory


def test_unknown_profile_raises():
    with pytest.raises(ValueError):
        generate_stack("x", packs=[], profile="bogus")


def test_invalid_pack_spec_raises():
    with pytest.raises(Exception):
        generate_stack("x", packs=["not-a-scoped-name"], profile="edge-min")


def test_render_is_valid_yaml_and_spec():
    text = render_stack_yaml("s", packs=["@acc/finance-roles@^1.0"],
                             agents=["financial_analyst"], profile="dc")
    data = yaml.safe_load(text)
    CollectiveSpec.model_validate(data)


def test_cli_writes_yaml(tmp_path, capsys):
    out = tmp_path / "collective.fsi.yaml"
    rc = main(["--name", "fsi", "--packs", "@acc/capital-markets-roles@^0.1",
               "--agents", "equity_analyst,portfolio_manager",
               "--profile", "edge-min", "--out", str(out)])
    assert rc == 0
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    CollectiveSpec.model_validate(data)
    roles = [a["role"] for a in data["agents"]]
    assert "equity_analyst" in roles and "portfolio_manager" in roles
