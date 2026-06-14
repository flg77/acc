"""Collective presets stay aligned with the rest of ACC.

Runs the same alignment check the `/acc-collectives` skill drives
(:mod:`tools.check_collectives`) over every shipped preset, so a preset that
references an unknown role, an unregistered model, or a pack it forgot to
declare fails CI rather than the edge host.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL = _REPO_ROOT / "tools" / "check_collectives.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_collectives", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def checker():
    return _load_checker()


def test_tool_present():
    assert _TOOL.is_file(), "tools/check_collectives.py is missing"


def test_all_shipped_presets_are_aligned(checker):
    """No ERRORs across collectives/ + the root collective.yaml."""
    findings = checker._validate(_REPO_ROOT / "collectives")
    assert not findings.errors, "collective alignment errors:\n" + "\n".join(findings.errors)


def test_manifest_covers_all_referenced_packs(checker):
    """Every required_packages entry across presets is known to packs.yaml."""
    manifest = checker._load_manifest()
    known = set(manifest["packs"]) | checker._catalog_pack_names()
    import yaml
    for spec_path in (_REPO_ROOT / "collectives").glob("collective.*.yaml"):
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        for entry in (spec.get("required_packages") or []):
            name = "@" + entry.split("@")[1]
            assert name in known, f"{spec_path.name}: pack {name} unknown to packs.yaml"


def test_checker_flags_unknown_role(checker, tmp_path):
    """Negative control: a bogus role with no provider is an ERROR."""
    bad = tmp_path / "collective.bogus.yaml"
    bad.write_text(
        "collective_id: bogus\nagents:\n  - role: not_a_real_role_xyz\n",
        encoding="utf-8",
    )
    findings = checker._validate(tmp_path)
    assert any("not_a_real_role_xyz" in e for e in findings.errors)


def test_checker_flags_unregistered_model(checker, tmp_path):
    """Negative control: a model not in models.yaml is an ERROR."""
    bad = tmp_path / "collective.badmodel.yaml"
    bad.write_text(
        "collective_id: bm\nagents:\n  - role: assistant\n    model: gpt-9-imaginary\n",
        encoding="utf-8",
    )
    findings = checker._validate(tmp_path)
    assert any("gpt-9-imaginary" in e for e in findings.errors)
