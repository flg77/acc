"""Golden-fixture tests for the ``acc.lib.okf`` P0 library.

The core promise: a *messy* Obsidian vault (missing front matter, missing
``type``, Obsidian wikilinks) converts — **non-destructively** — into a
conformant OKF bundle, and the validator catches the three conformance rules on
the raw vault.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.lib.okf import (
    DEFAULT_TYPE,
    Bundle,
    Concept,
    concept_to_markdown,
    from_obsidian,
    load_bundle,
    load_concept,
    split_frontmatter,
    validate,
)

NOW = "2026-07-08T00:00:00Z"


@pytest.fixture
def messy_vault(tmp_path: Path) -> Path:
    """A deliberately messy vault: one clean note, one with no front matter,
    one with front matter but no ``type``, a spaced filename, wikilinks (an
    alias + a dangling one), a reserved ``index.md``, and an excluded dir."""
    v = tmp_path / "vault"
    (v / "notes").mkdir(parents=True)
    (v / "playbook").mkdir()
    (v / ".trash").mkdir()

    (v / "notes" / "Alpha.md").write_text(
        "---\n"
        "type: Concept\n"
        "title: Alpha Concept\n"
        "tags: [foo, bar]\n"
        "---\n"
        "Alpha body. See [[Beta]], [[Beta|the beta note]] and [[Missing Note]].\n",
        encoding="utf-8")
    # No front matter at all → rule 1 fails in the raw vault.
    (v / "notes" / "Beta.md").write_text(
        "Just some beta thoughts.\nNo front matter here.\n", encoding="utf-8")
    # Front matter but no `type` → rule 2 fails in the raw vault.
    (v / "playbook" / "Deploy.md").write_text(
        "---\ntitle: Deploy Steps\n---\n1. do the thing\n", encoding="utf-8")
    # Spaced filename + a link to Alpha.
    (v / "Root Note.md").write_text(
        "---\ntype: Reference\n---\nRoot body linking [[Alpha]].\n",
        encoding="utf-8")
    # Reserved file — must be skipped by the transform, regenerated fresh.
    (v / "index.md").write_text("# stale hand-written index\n", encoding="utf-8")
    # Excluded dir — must never appear in the output bundle.
    (v / ".trash" / "Old.md").write_text("garbage\n", encoding="utf-8")
    return v


# --- split_frontmatter ------------------------------------------------------

def test_split_frontmatter_variants():
    fm, body, ok = split_frontmatter("---\ntype: X\na: 1\n---\nbody\n")
    assert ok and fm == {"type": "X", "a": 1} and body == "body\n"

    fm, body, ok = split_frontmatter("no block here\n")
    assert not ok and fm == {} and body == "no block here\n"

    fm, _, ok = split_frontmatter("---\ntype: X\nunterminated\n")
    assert not ok and fm == {}

    fm, _, ok = split_frontmatter("---\n: : bad yaml :\n\t- broken\n---\nb")
    assert not ok and fm == {}

    fm, _, ok = split_frontmatter("---\n- just\n- a\n- list\n---\nb")
    assert not ok and fm == {}  # front matter must be a mapping

    fm, body, ok = split_frontmatter("---\n---\nempty fm\n")
    assert ok and fm == {} and body == "empty fm\n"


# --- validate (the three rules) --------------------------------------------

def test_validate_catches_the_three_rules(messy_vault: Path):
    report = validate(load_bundle(messy_vault))
    assert report.conformant is False
    rules = {i.rule for i in report.errors}
    assert "frontmatter" in rules   # Beta.md (+ .trash/Old.md) — rule 1
    assert "type" in rules          # Deploy.md — rule 2
    # index.md is reserved → never counted as a concept.
    assert all("index.md" not in c.rel_path for c in load_bundle(messy_vault).concepts)


def test_validate_clean_bundle_is_conformant(tmp_path: Path):
    (tmp_path / "a.md").write_text("---\ntype: Reference\ntitle: A\n---\nhi\n",
                                   encoding="utf-8")
    report = validate(load_bundle(tmp_path))
    assert report.conformant is True
    assert report.n_concepts == 1
    assert report.errors == []


def test_validate_reserved_with_frontmatter_warns_not_errors(tmp_path: Path):
    (tmp_path / "a.md").write_text("---\ntype: Reference\n---\nx\n", encoding="utf-8")
    (tmp_path / "index.md").write_text("---\ntype: nope\n---\nlisting\n",
                                       encoding="utf-8")
    report = validate(load_bundle(tmp_path))
    assert report.conformant is True                     # still conformant
    assert any(w.rule == "reserved" for w in report.warnings)


# --- from_obsidian (the headline: messy vault → conformant bundle) ---------

def test_from_obsidian_produces_conformant_bundle(messy_vault: Path, tmp_path: Path):
    dest = tmp_path / "bundle"
    from_obsidian(messy_vault, dest, now=NOW)

    report = validate(load_bundle(dest))
    assert report.conformant is True, report.summary()
    assert report.n_concepts == 4                        # Alpha, Beta, Deploy, Root Note
    assert (dest / "index.md").exists()
    # Reserved index.md carries no front matter.
    assert not (dest / "index.md").read_text(encoding="utf-8").startswith("---")


def test_transform_is_non_destructive(messy_vault: Path, tmp_path: Path):
    before = {p: p.read_bytes()
              for p in messy_vault.rglob("*.md")}
    from_obsidian(messy_vault, tmp_path / "out", now=NOW)
    after = {p: p.read_bytes() for p in messy_vault.rglob("*.md")}
    assert after == before                               # source untouched


def test_transform_excludes_trash(messy_vault: Path, tmp_path: Path):
    bundle = from_obsidian(messy_vault, tmp_path / "out", now=NOW, write=False)
    assert all(not c.rel_path.startswith(".trash") for c in bundle.concepts)
    assert all("Old.md" not in c.rel_path for c in bundle.concepts)


def test_wikilinks_rewritten_to_bundle_relative(messy_vault: Path, tmp_path: Path):
    bundle = from_obsidian(messy_vault, tmp_path / "out", now=NOW, write=False)
    alpha = bundle.concept("notes/Alpha.md")
    assert "[Beta](/notes/Beta.md)" in alpha.body            # resolved
    assert "[the beta note](/notes/Beta.md)" in alpha.body   # alias preserved
    assert "[Missing Note](/Missing%20Note.md)" in alpha.body  # dangling, tolerated
    # No Obsidian wiki syntax survives anywhere in the bundle.
    assert all("[[" not in c.body for c in bundle.concepts)


def test_type_inference_defaults(messy_vault: Path, tmp_path: Path):
    bundle = from_obsidian(messy_vault, tmp_path / "out", now=NOW, write=False)
    assert bundle.concept("playbook/Deploy.md").type == "Playbook"  # folder hint
    assert bundle.concept("notes/Beta.md").type == DEFAULT_TYPE      # no hint


def test_enrichment_preserves_existing_keys(messy_vault: Path, tmp_path: Path):
    bundle = from_obsidian(messy_vault, tmp_path / "out", now=NOW, write=False)
    alpha = bundle.concept("notes/Alpha.md")
    assert alpha.type == "Concept"                 # not overwritten
    assert alpha.title == "Alpha Concept"          # not overwritten
    assert alpha.frontmatter["tags"] == ["foo", "bar"]
    beta = bundle.concept("notes/Beta.md")
    assert beta.title == "Beta"                    # derived from filename
    assert beta.frontmatter["description"].startswith("Just some beta")
    assert beta.frontmatter["timestamp"] == NOW


def test_transform_deterministic_without_now(messy_vault: Path, tmp_path: Path):
    b1 = from_obsidian(messy_vault, tmp_path / "o1", write=False)
    b2 = from_obsidian(messy_vault, tmp_path / "o2", write=False)
    assert [concept_to_markdown(c) for c in b1.concepts] == \
           [concept_to_markdown(c) for c in b2.concepts]
    # No timestamp invented when now is omitted.
    assert all("timestamp" not in c.frontmatter for c in b1.concepts)


# --- emit roundtrip ---------------------------------------------------------

def test_emit_parse_roundtrip(tmp_path: Path):
    c = Concept(rel_path="x.md",
                frontmatter={"type": "Reference", "title": "X", "tags": ["a"]},
                body="Hello\n\nWorld\n")
    (tmp_path / "x.md").write_text(concept_to_markdown(c), encoding="utf-8")
    back = load_concept(tmp_path / "x.md", "x.md")
    assert back.frontmatter == c.frontmatter
    assert back.body.strip() == c.body.strip()
    assert back.is_conformant


def test_emit_orders_type_first(tmp_path: Path):
    c = Concept(rel_path="x.md",
                frontmatter={"title": "X", "type": "Reference"}, body="b\n")
    text = concept_to_markdown(c)
    assert text.index("type:") < text.index("title:")


def test_generated_index_lists_concepts_grouped_by_type(messy_vault: Path, tmp_path: Path):
    dest = tmp_path / "bundle"
    from_obsidian(messy_vault, dest, now=NOW)
    index = (dest / "index.md").read_text(encoding="utf-8")
    assert "## Concept" in index and "## Playbook" in index
    assert "(/notes/Alpha.md)" in index


def test_load_bundle_missing_root_is_empty(tmp_path: Path):
    bundle = load_bundle(tmp_path / "nope")
    assert isinstance(bundle, Bundle)
    assert bundle.concepts == [] and bundle.reserved == []
