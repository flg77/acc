"""Tests for the governance-layer inventory loader (PR-Z1a).

The Compliance pane uses this to show *what governance is loaded*.  The
parse is display-only + best-effort; these pin the rule-id/version
extraction across the three Rego dialects (A/B inline summary, C
auto-id + Context line) and the root resolution.
"""

from __future__ import annotations

from pathlib import Path

from acc.governance_inventory import (
    GovernanceLayer,
    list_frameworks,
    load_all_layers,
    load_layer,
    parse_rego_file,
    regulatory_root,
)

_CAT_A = """\
# Version: 0.6.0
package acc.membrane.constitutional

# A-001: Signals from outside the collective are rejected.
default allow_signal = false

# A-002: Category A rules cannot be updated by any signal.
deny_rule_update if {
    input.action == "RULE_UPDATE"
}
"""

_CAT_C = """\
# Version: 0.2.0
package acc.membrane.adaptive

# --------------------------------------------------------------------------
# C-AUTO-20260402-001 (carried from v0.1.0)
# --------------------------------------------------------------------------
# Source:     ICL episode ep_7f3a4b
# Context:   PDF documents > 10MB consistently cause RESOURCE_EXHAUSTION.
allow_large_pdf if { true }
"""


def _write_layer(root: Path, dirname: str, fname: str, content: str) -> None:
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# parse_rego_file
# ---------------------------------------------------------------------------


def test_parse_inline_summary_and_version(tmp_path):
    f = tmp_path / "c.rego"
    f.write_text(_CAT_A, encoding="utf-8")
    version, rules = parse_rego_file(f)
    assert version == "0.6.0"
    ids = [r.rule_id for r in rules]
    assert ids == ["A-001", "A-002"]
    assert rules[0].summary == "Signals from outside the collective are rejected."
    assert rules[0].line > 0


def test_parse_cat_c_context_line(tmp_path):
    f = tmp_path / "adaptive.rego"
    f.write_text(_CAT_C, encoding="utf-8")
    version, rules = parse_rego_file(f)
    assert version == "0.2.0"
    assert len(rules) == 1
    r = rules[0]
    assert r.rule_id == "C-AUTO-20260402-001"
    # Summary comes from the Context: line, not the "(carried…)" note or
    # the Source: meta line.
    assert "RESOURCE_EXHAUSTION" in r.summary
    assert "carried" not in r.summary.lower()
    assert "episode" not in r.summary.lower()


def test_parse_missing_version_is_empty(tmp_path):
    f = tmp_path / "x.rego"
    f.write_text("# A-001: a rule with no version header\n", encoding="utf-8")
    version, rules = parse_rego_file(f)
    assert version == ""
    assert rules[0].rule_id == "A-001"


def test_parse_dedups_repeated_ids(tmp_path):
    f = tmp_path / "x.rego"
    f.write_text(
        "# A-001: first mention\nallow if { true }\n# A-001: re-mentioned\n",
        encoding="utf-8",
    )
    _v, rules = parse_rego_file(f)
    assert [r.rule_id for r in rules] == ["A-001"]


def test_parse_missing_file_is_safe(tmp_path):
    version, rules = parse_rego_file(tmp_path / "nope.rego")
    assert version == "" and rules == []


# ---------------------------------------------------------------------------
# load_layer / load_all_layers (synthetic root)
# ---------------------------------------------------------------------------


def test_load_layer_aggregates_files(tmp_path):
    _write_layer(tmp_path, "category_a", "constitutional.rego", _CAT_A)
    _write_layer(tmp_path, "category_a", "kernel.rego",
                 "# A-050: kernel rule\n")
    layer = load_layer("A", root=tmp_path)
    assert isinstance(layer, GovernanceLayer)
    assert layer.category == "A"
    assert layer.immutable is True
    assert layer.version == "0.6.0"
    assert layer.rule_count == 3  # A-001, A-002, A-050
    assert len(layer.file_paths) == 2


def test_load_layer_lists_data_json_for_browsing(tmp_path):
    _write_layer(tmp_path, "category_b", "conditional.rego",
                 "# Version: 0.3.0\n# B-001: a setpoint rule\n")
    (tmp_path / "category_b" / "data_rhoai.json").write_text("{}", encoding="utf-8")
    layer = load_layer("B", root=tmp_path)
    assert any(p.endswith("data_rhoai.json") for p in layer.file_paths)


def test_load_layer_absent_dir_is_empty(tmp_path):
    layer = load_layer("C", root=tmp_path)
    assert layer.rule_count == 0 and layer.version == ""


def test_load_all_layers_order(tmp_path):
    layers = load_all_layers(root=tmp_path)
    assert [l.category for l in layers] == ["A", "B", "C"]
    assert layers[0].immutable and not layers[1].immutable


def test_list_frameworks_absent_is_empty(tmp_path):
    assert list_frameworks(root=tmp_path) == []


def test_list_frameworks_lists_yaml_stems(tmp_path):
    fw = tmp_path / "frameworks"
    fw.mkdir()
    (fw / "bsi.yaml").write_text("framework_id: bsi\n", encoding="utf-8")
    (fw / "nist_ai_rmf.yaml").write_text("framework_id: nist\n", encoding="utf-8")
    assert list_frameworks(root=tmp_path) == ["bsi", "nist_ai_rmf"]


# ---------------------------------------------------------------------------
# Root resolution + the real shipped policy files
# ---------------------------------------------------------------------------


def test_regulatory_root_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_REGULATORY_ROOT", str(tmp_path))
    assert regulatory_root() == tmp_path


def test_shipped_layers_load():
    """The repo's real regulatory_layer must parse into non-empty A/B/C
    layers — guards a reformat that breaks the parser."""
    layers = {l.category: l for l in load_all_layers()}
    assert layers["A"].version and layers["A"].rule_count >= 6
    assert layers["A"].immutable is True
    assert layers["B"].rule_count >= 1
    assert any(r.rule_id == "A-001" for r in layers["A"].rules)
