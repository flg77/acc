"""`20260908-asago-alignment` AS-01 -- ACC speaks the Risk Atlas Nexus vocabulary.

Every Nexus id a framework names must exist in the pinned vocabulary (so an
upstream rename fails here, offline); NIST subcategories carry their
asago-form id; the SSSOM export is a valid, complete table; the gap report
carries the ids; the CLI answers a policy risk with ACC's controls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acc import nexus as N
from acc.frameworks import Framework, FrameworkControl, load_all_frameworks


@pytest.fixture(scope="module")
def frameworks():
    return load_all_frameworks()


@pytest.fixture(scope="module")
def vocab():
    return N.vocabulary()


# ---------------------------------------------------------------------------
# the vocabulary
# ---------------------------------------------------------------------------


def test_vocabulary_is_pinned_and_dated(vocab):
    assert vocab.fetched.startswith("2026-")
    assert vocab.size > 120
    assert set(vocab.families) >= {"atlas", "owasp_llm", "owasp_asi", "owasp_ast10", "nist_genai", "nist_subcategory"}
    assert vocab.known("atlas-prompt-injection") and vocab.taxonomy_of("llm01-prompt-injection") == "owasp-llm-2.0"
    assert vocab.known("nist-ms-2.5") and vocab.family_of("nist-gv-1.1") == "nist_subcategory"
    assert not vocab.known("atlas-made-up") and not vocab.known("") and vocab.family_of("nope") == ""


def test_nist_subcategory_ids_derive_from_the_control_id():
    assert N.nist_subcategory_id("GOVERN-1.1") == "nist-gv-1.1"
    assert N.nist_subcategory_id("MEASURE-2.11") == "nist-ms-2.11"
    assert N.nist_subcategory_id("manage-4.1") == "nist-mg-4.1"
    assert N.nist_subcategory_id("ART-9") == "" and N.nist_subcategory_id("") == ""


# ---------------------------------------------------------------------------
# the shipped frameworks
# ---------------------------------------------------------------------------


def test_every_nexus_id_in_every_framework_is_known(frameworks, vocab):
    assert N.unknown_ids(frameworks, vocab) == []


def test_nist_controls_carry_their_own_nexus_id(frameworks):
    nist = next(f for f in frameworks if f.framework_id == "nist_ai_rmf")
    assert nist.nexus_taxonomy == "nist-ai-rmf"
    for c in nist.controls:
        assert c.nexus_ids == [N.nist_subcategory_id(c.control_id)], c.control_id


def test_frameworks_the_nexus_does_not_carry_stay_unmapped(frameworks):
    for fid in ("eu_ai_act", "iso_42001", "soc2"):
        fw = next(f for f in frameworks if f.framework_id == fid)
        assert fw.nexus_taxonomy == "" and all(not c.nexus_ids for c in fw.controls)


def test_a_framework_with_one_nexus_id_per_threat_indexes_and_covers(frameworks):
    """The threat model (where present) maps every entry to at least one
    risk, and the reverse index answers an asago risk with ACC threats."""
    tm = next((f for f in frameworks if f.framework_id == "atlas_threat_model"), None)
    if tm is None:
        pytest.skip("threat model not in this tree")
    assert all(c.nexus_ids for c in tm.controls)
    idx = N.nexus_index(frameworks)
    assert any(fid == tm.framework_id for fid, _ in idx.get("llm01-prompt-injection", []))
    assert N.coverage(frameworks)[tm.framework_id]["mapped"] == len(tm.controls)


# ---------------------------------------------------------------------------
# SSSOM
# ---------------------------------------------------------------------------


def _fw(fid, controls, taxonomy=""):
    return Framework(framework_id=fid, name=fid, nexus_taxonomy=taxonomy,
                     controls=[FrameworkControl(control_id=c, title=t, nexus_ids=ids) for c, t, ids in controls])


def test_sssom_rows_and_predicates():
    fws = [_fw("nist_ai_rmf", [("GOVERN-1.1", "Legal", ["nist-gv-1.1"])], "nist-ai-rmf"),
           _fw("threats", [("T-01", "Indirect\tinjection", ["llm01-prompt-injection", "atlas-made-up"])])]
    rows = N.sssom_rows(fws)
    assert [r["predicate_id"] for r in rows] == [N.PREDICATE_EXACT, N.PREDICATE_RELATED, N.PREDICATE_RELATED]
    assert rows[0]["subject_id"] == "acc:nist_ai_rmf/GOVERN-1.1" and rows[0]["object_source"] == "nist-ai-rmf"
    assert rows[1]["object_source"] == "owasp-llm-2.0" and rows[1]["comment"] == ""
    assert rows[2]["comment"].startswith("UNKNOWN")
    text = N.render_sssom(rows, now=0)
    head, table = text.split("\n" + "\t".join(N.SSSOM_COLUMNS) + "\n", 1)
    assert head.startswith("# curie_map:") and "mapping_date: 1970-01-01" in head
    lines = [l for l in table.strip("\n").split("\n")]
    assert len(lines) == 3 and all(len(l.split("\t")) == len(N.SSSOM_COLUMNS) for l in lines)
    assert "Indirect injection" in lines[1]          # a tab in a label cannot break the table


def test_the_shipped_export_is_a_complete_table(frameworks):
    text = N.render_sssom(N.sssom_rows(frameworks))
    body = [l for l in text.split("\n") if l and not l.startswith("#")]
    assert body[0] == "\t".join(N.SSSOM_COLUMNS)
    assert len(body) > 11                           # at least the 11 NIST rows
    assert all(len(l.split("\t")) == len(N.SSSOM_COLUMNS) for l in body[1:])
    assert not any("UNKNOWN" in l for l in body)


# ---------------------------------------------------------------------------
# the gap report carries the ids; the CLI answers a risk
# ---------------------------------------------------------------------------


def test_gap_report_carries_nexus_ids(tmp_path):
    from acc.gap_analysis import analyze_gaps, dump_gap_report, render_markdown
    from acc.governance_inventory import load_all_layers
    fw = _fw("nist_ai_rmf", [("GOVERN-1.1", "Legal and regulatory requirements understood", ["nist-gv-1.1"])], "nist-ai-rmf")
    report = analyze_gaps(load_all_layers(), fw)
    assert report.controls[0].nexus_ids == ["nist-gv-1.1"]
    assert "- nexus: nist-gv-1.1" in render_markdown(report)
    payload = json.loads(dump_gap_report(report, tmp_path).read_text(encoding="utf-8"))
    assert payload["controls"][0]["nexus_ids"] == ["nist-gv-1.1"]


def test_cli_nexus_lookup_and_coverage(capsys, monkeypatch):
    from acc.cli import compliance_cmd as C
    import argparse
    monkeypatch.setattr(C, "_frameworks", lambda only="": [
        _fw("nist_ai_rmf", [("GOVERN-1.1", "Legal", ["nist-gv-1.1"])], "nist-ai-rmf")])
    assert C._cmd_nexus(argparse.Namespace(nexus_id="nist-gv-1.1", json=True)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["known"] and out["controls"] == [{"framework_id": "nist_ai_rmf", "control_id": "GOVERN-1.1", "title": "Legal"}]
    assert C._cmd_nexus(argparse.Namespace(nexus_id="asi10-rogue-agents", json=False)) == 2
    assert "0 ACC control(s)" in capsys.readouterr().out
    assert C._cmd_coverage(argparse.Namespace(json=False)) == 0
    assert "1/1" in capsys.readouterr().out.replace(" ", "")
    assert C._cmd_mappings(argparse.Namespace(framework="", json=False, out="-")) == 0
    assert capsys.readouterr().out.startswith("# curie_map:")


# ---------------------------------------------------------------------------
# an asago risk extraction dropped in
# ---------------------------------------------------------------------------


def _extraction(tmp_path, risks, wrapped=True):
    p = tmp_path / "risk-extraction.json"
    payload = {"risk_extraction": {"risks": risks}} if wrapped else {"risks": risks}
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_load_risk_extraction_accepts_both_shapes_and_rejects_others(tmp_path):
    risks = [{"nexus_id": "atlas-hallucination", "confidence": 0.9}]
    assert N.load_risk_extraction(_extraction(tmp_path, risks)) == risks
    assert N.load_risk_extraction(_extraction(tmp_path, risks, wrapped=False)) == risks
    bad = tmp_path / "nope.json"; bad.write_text('{"foo": 1}', encoding="utf-8")
    with pytest.raises(ValueError):
        N.load_risk_extraction(bad)


def test_cross_mapping_ids_drop_the_label():
    assert N.cross_mapping_ids(["nist-ms-2.5 (Confabulation)", "owasp-llm09-2025 (Misinformation)", "", None]) == \
        ["nist-ms-2.5", "owasp-llm09-2025"]


def test_join_risks_answers_by_nexus_id_then_cross_mapping_then_says_so():
    fws = [_fw("nist_ai_rmf", [("MEASURE-2.5", "Evaluated for validity", ["nist-ms-2.5"])], "nist-ai-rmf"),
           _fw("threats", [("T-01", "Indirect injection", ["llm01-prompt-injection"])])]
    risks = [
        {"nexus_id": "llm01-prompt-injection", "confidence": 0.8, "evidence": [{"exact": "No untrusted text", "page": 3}]},
        {"nexus_id": "atlas-hallucination", "confidence": 0.9, "cross_mappings": ["nist-ms-2.5 (Confabulation)"]},
        {"nexus_id": "asi10-rogue-agents"},
        {"nexus_id": "atlas-made-up"},
    ]
    rows = N.join_risks(risks, fws, gap_covered={("threats", "T-01"): True, ("nist_ai_rmf", "MEASURE-2.5"): False})
    assert [c["control_id"] for c in rows[0]["controls"]] == ["T-01"] and rows[0]["controls"][0]["covered"] is True
    assert rows[0]["evidence"] == ["No untrusted text"] and rows[0]["via_cross_mapping"] == ""
    assert rows[1]["via_cross_mapping"] == "nist-ms-2.5" and rows[1]["controls"][0]["covered"] is False
    assert rows[1]["controls"][0]["matched_on"] == "nist-ms-2.5"
    assert rows[2]["controls"] == [] and rows[2]["known"] is True
    assert rows[3]["controls"] == [] and rows[3]["known"] is False
    plain = N.join_risks(risks[:1], fws)                       # no gap analysis: unknown, not guessed
    assert plain[0]["controls"][0]["covered"] is None


def test_cli_risks_reports_answered_and_unanswered(tmp_path, capsys, monkeypatch):
    from acc.cli import compliance_cmd as C
    import argparse
    monkeypatch.setattr(C, "_frameworks", lambda only="": [
        _fw("threats", [("T-01", "Indirect injection", ["llm01-prompt-injection"])])])
    path = _extraction(tmp_path, [{"nexus_id": "llm01-prompt-injection", "confidence": 0.8},
                                  {"nexus_id": "asi10-rogue-agents"}])
    assert C._cmd_risks(argparse.Namespace(path=str(path), no_gaps=True, json=False)) == 2
    out = capsys.readouterr().out
    assert "-> threats" in out and "nothing in ACC answers" in out and "1/2 risk(s) answered" in out
    assert C._cmd_risks(argparse.Namespace(path=str(path), no_gaps=True, json=True)) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["risks"][0]["controls"][0]["control_id"] == "T-01" and data["risks"][1]["controls"] == []
    assert C._cmd_risks(argparse.Namespace(path=str(tmp_path / "missing.json"), no_gaps=True, json=False)) == 1


def test_the_shipped_tree_answers_a_realistic_extraction(tmp_path, frameworks):
    """The example from asago's README, plus an agentic risk: NIST answers by
    cross-mapping, the threat model (where present) by nexus id."""
    path = _extraction(tmp_path, [
        {"nexus_id": "atlas-hallucination", "confidence": 0.92,
         "evidence": [{"exact": "must not generate content that could be construed as medical advice", "document": "acme.pdf", "page": 12}],
         "cross_mappings": ["nist-ms-2.5 (Confabulation)", "owasp-llm09-2025 (Misinformation)"]},
        {"nexus_id": "llm01-prompt-injection", "confidence": 0.85},
    ])
    rows = N.join_risks(N.load_risk_extraction(path), frameworks, gap_covered=N.gap_coverage_map(frameworks))
    assert rows[0]["known"] and rows[0]["taxonomy"] == "ibm-risk-atlas"
    has_threats = any(f.framework_id == "atlas_threat_model" for f in frameworks)
    if has_threats:
        assert rows[1]["controls"] and all(c["covered"] is not None for c in rows[1]["controls"])
    else:
        assert rows[1]["controls"] == []
