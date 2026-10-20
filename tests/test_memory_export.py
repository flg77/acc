"""`20260928-rdf-provenance-and-memory-views` follow-up -- the Memory view as an
OKF bundle, so Obsidian's graph is a third renderer (RDF-07).

The bundle is built from the view, so it holds what the exporter may see; these
assert its shape, that it validates as OKF, and that the CLI goes through the
viewer's policy.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from acc.lib.okf import load_bundle, validate
from acc.memory_export import bundle_from_view
from acc.memory_views import Sources, memory_view
from tests.test_memory_views import ALICE, OPERATOR, _ledger, _redis


def _sources():
    rows = _ledger()
    return Sources(redis=_redis(), collective_id="c1", hub_collective_id="hub1",
                   ledger_rows=lambda cid: [r for r in rows if r["collective_id"] == cid])


def _paths(bundle):
    return sorted(c.rel_path for c in bundle.concepts)


def test_one_concept_per_note_merged_across_tiers():
    bundle = bundle_from_view(memory_view(_sources(), OPERATOR))
    assert [p for p in _paths(bundle) if p.startswith("notes/")] == [
        "notes/n1.md", "notes/n2.md", "notes/n3.md"]
    n1 = bundle.concept("notes/n1.md")
    assert n1.frontmatter["acc_tiers"] == ["private", "shared"]
    assert "[[tiers/private|Private]]" in n1.body and "[[tiers/shared|Shared]]" in n1.body
    assert "[[roles/analyst|analyst]]" in n1.body


def test_role_and_tier_pages_exist_for_every_link():
    bundle = bundle_from_view(memory_view(_sources(), OPERATOR))
    paths = set(_paths(bundle))
    assert {"roles/analyst.md", "tiers/private.md", "tiers/shared.md", "tiers/hub.md"} <= paths
    for c in bundle.concepts:
        for target in re.findall(r"\[\[([^|\]]+)", c.body):
            assert f"{target}.md" in paths, target


def test_the_bundle_holds_only_what_the_exporter_may_see():
    bundle = bundle_from_view(memory_view(_sources(), ALICE))
    assert [p for p in _paths(bundle) if p.startswith("notes/")] == ["notes/n1.md", "notes/n2.md"]
    text = json.dumps([c.body for c in bundle.concepts])
    assert "U1" not in text and "U2" not in text


def test_the_written_bundle_is_valid_okf(tmp_path):
    from acc.lib.okf import write_bundle
    write_bundle(bundle_from_view(memory_view(_sources(), OPERATOR), exported_at="2026-10-05T00:00:00Z"),
                 tmp_path / "vault")
    report = validate(load_bundle(tmp_path / "vault"))
    assert report.conformant, report.summary()
    assert (tmp_path / "vault" / "index.md").is_file()


def test_cli_export_refuses_a_non_empty_dir_and_writes_with_the_viewer(monkeypatch, tmp_path, capsys):
    from acc import memory_views
    from acc.cli import memory_cmd

    monkeypatch.setattr(memory_views, "default_sources", lambda cid: _sources())
    monkeypatch.setattr("acc.identity.current",
                        lambda: SimpleNamespace(attribution=lambda: "slack:U1", tier="viewer",
                                                effective_ceiling="HIGH"))
    full = tmp_path / "full"
    full.mkdir()
    (full / "keep.md").write_text("x")
    args = SimpleNamespace(okf=str(full), collective="", force=False)
    assert memory_cmd._cmd_export(args) == memory_cmd.EXIT_REFUSED

    out = tmp_path / "vault"
    assert memory_cmd._cmd_export(SimpleNamespace(okf=str(out), collective="", force=False)) == 0
    assert sorted(p.name for p in (out / "notes").iterdir()) == ["n1.md", "n2.md"]
    assert "Wrote 2 note(s)" in capsys.readouterr().out
