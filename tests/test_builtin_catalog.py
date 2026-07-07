"""Built-in day-0 catalog loader + ratings + generator-freshness guard."""

from __future__ import annotations

import pytest

from acc.pkg import builtin_catalog as bc
from acc.pkg import ratings as rt


def test_builtin_catalog_loads_the_bundled_packs():
    cat = bc.load_builtin_catalog()
    assert cat.id == "acc-builtin"
    assert cat.name and cat.oidc_issuer  # header populated
    assert len(cat.packages) >= 5
    names = {p.name for p in cat.packages}
    assert "@acc/workspace-roles" in names
    # counts are real (workspace-roles ships skills + mcps)
    ws = next(p for p in cat.packages if p.name == "@acc/workspace-roles")
    assert ws.n_roles > 0 and ws.n_skills > 0
    # the Catalogs pane's roles column = the de-duplicated union
    assert len(cat.all_roles()) >= 40


def test_builtin_catalog_absent_data_is_empty(monkeypatch):
    monkeypatch.setattr(bc, "_raw", lambda: {})
    cat = bc.load_builtin_catalog()
    assert cat.packages == [] and cat.all_roles() == []


def test_generated_file_is_not_stale():
    """The committed builtin_catalog.yaml must match a fresh generation from the
    bundled packs — guards against editing the packs without regenerating."""
    from tools.gen_builtin_catalog import _DEFAULT_SRC, _OUT, build, render

    if not _DEFAULT_SRC.exists():
        pytest.skip("fixture packs not present")
    expected = render(build(_DEFAULT_SRC))
    actual = _OUT.read_text(encoding="utf-8")
    assert actual == expected, "run: python -m tools.gen_builtin_catalog"


# --------------------------------------------------------------------------
# Ratings
# --------------------------------------------------------------------------


def test_rating_set_get_clear(tmp_path):
    p = tmp_path / "ratings.yaml"
    assert rt.get_rating("@acc/x", p) == 0
    rt.set_rating("@acc/x", 4, p)
    assert rt.get_rating("@acc/x", p) == 4
    assert rt.load_ratings(p) == {"@acc/x": 4}
    rt.set_rating("@acc/x", 0, p)  # clear
    assert rt.get_rating("@acc/x", p) == 0


def test_rating_rejects_out_of_range(tmp_path):
    p = tmp_path / "ratings.yaml"
    with pytest.raises(ValueError):
        rt.set_rating("@acc/x", 9, p)


def test_rating_ignores_corrupt_values(tmp_path):
    p = tmp_path / "ratings.yaml"
    p.write_text("ratings:\n  '@acc/a': 3\n  '@acc/b': banana\n  '@acc/c': 99\n",
                 encoding="utf-8")
    # only the valid 1..5 entry survives
    assert rt.load_ratings(p) == {"@acc/a": 3}


def test_stars_glyph():
    assert rt.stars_glyph(0) == "—"
    assert rt.stars_glyph(3) == "★★★☆☆"
    assert rt.stars_glyph(9) == "★★★★★"  # clamped
