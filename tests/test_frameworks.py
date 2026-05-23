"""Tests for compliance framework catalogs (PR-Z2a)."""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.frameworks import (
    Framework,
    import_framework,
    load_all_frameworks,
    load_framework,
)

_BSI = """\
framework_id: bsi_c5
name: "BSI C5"
version: "2020"
source: "BSI C5:2020"
controls:
  - control_id: OPS-01
    title: "Capacity planning"
    description: "Plan capacity."
    category: OPS
"""


def test_load_framework_validates(tmp_path):
    f = tmp_path / "bsi.yaml"
    f.write_text(_BSI, encoding="utf-8")
    fw = load_framework(f)
    assert isinstance(fw, Framework)
    assert fw.framework_id == "bsi_c5"
    assert fw.control_count == 1
    assert fw.controls[0].control_id == "OPS-01"


def test_load_framework_rejects_unknown_key(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("framework_id: x\nname: X\nbogus: 1\ncontrols: []\n", encoding="utf-8")
    with pytest.raises(Exception):
        load_framework(f)


def test_load_all_merges_builtin_under_imported(tmp_path):
    builtin = tmp_path / "builtin"
    imported = tmp_path / "imported"
    builtin.mkdir()
    imported.mkdir()
    (builtin / "soc2.yaml").write_text(
        "framework_id: soc2\nname: builtin\ncontrols: []\n", encoding="utf-8",
    )
    (imported / "soc2.yaml").write_text(
        "framework_id: soc2\nname: imported-override\ncontrols: []\n",
        encoding="utf-8",
    )
    frameworks = load_all_frameworks([builtin, imported])
    assert len(frameworks) == 1
    assert frameworks[0].name == "imported-override"


def test_load_all_skips_malformed(tmp_path):
    (tmp_path / "ok.yaml").write_text(
        "framework_id: ok\nname: ok\ncontrols: []\n", encoding="utf-8",
    )
    (tmp_path / "broken.yaml").write_text("{not: valid: yaml:", encoding="utf-8")
    frameworks = load_all_frameworks([tmp_path])
    assert [f.framework_id for f in frameworks] == ["ok"]


def test_import_framework_validates_and_copies(tmp_path):
    src = tmp_path / "incoming" / "bsi.yaml"
    src.parent.mkdir()
    src.write_text(_BSI, encoding="utf-8")
    dest_root = tmp_path / "store"
    out = import_framework(src, dest_root=dest_root)
    assert out == dest_root / "bsi_c5.yaml"
    assert load_framework(out).framework_id == "bsi_c5"


def test_import_framework_rejects_bad_file(tmp_path):
    src = tmp_path / "bad.yaml"
    src.write_text("not a framework", encoding="utf-8")
    with pytest.raises(Exception):
        import_framework(src, dest_root=tmp_path / "store")


def test_shipped_catalogs_load():
    """The repo ships the four built-in framework catalogs."""
    ids = {f.framework_id for f in load_all_frameworks()}
    assert {"nist_ai_rmf", "iso_42001", "eu_ai_act", "soc2"} <= ids
    # Each shipped catalog has controls.
    for f in load_all_frameworks():
        if f.framework_id in {"nist_ai_rmf", "iso_42001", "eu_ai_act", "soc2"}:
            assert f.control_count >= 5, f.framework_id
