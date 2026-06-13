"""Proposal 024 Phase 2 (UC3) — semantic capability routing tests.

A fake deterministic embed_fn keeps these tests model-free: vectors are
keyword-buckets, so "ship a python microservice" lands near the coding
purpose without sharing a literal substring with it.  The flag-off path
must stay byte-identical to the pre-024 substring filter.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from acc.capability_index import CapabilityIndex, CapabilityQuery

_DIM = 8


def _fake_embed(text: str) -> list[float]:
    """Deterministic keyword-bucket embedding (unit-ish, 8-dim)."""
    t = text.lower()
    v = [0.0] * _DIM
    if any(w in t for w in ("code", "software", "python", "microservice", "service")):
        v[0] += 1.0
    if any(w in t for w in ("clinical", "medical", "patient", "evidence")):
        v[1] += 1.0
    if any(w in t for w in ("analysis", "analyse", "data")):
        v[2] += 1.0
    if not any(v):
        v[7] = 1.0
    return v


@pytest.fixture(autouse=True)
def _empty_packages_root(tmp_path, monkeypatch):
    # Isolate from the session-scoped @acc/* family-pack install.
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "no-packages"))


@pytest.fixture
def roles_tree(tmp_path: Path) -> tuple[Path, Path]:
    roles_root = tmp_path / "roles"
    mcps_root = tmp_path / "mcps"
    roles_root.mkdir()
    mcps_root.mkdir()
    for name, purpose, task_types in [
        ("coding_agent", "Write and review software_engineering code.", ["CODE_WRITE"]),
        ("clinical_reviewer", "Review clinical_research literature for evidence.", ["CLINICAL_REVIEW"]),
        ("analyst", "Generic data analysis.", ["ANALYSE"]),
    ]:
        (roles_root / name).mkdir()
        (roles_root / name / "role.yaml").write_text(yaml.safe_dump({
            "role_definition": {
                "purpose": purpose,
                "persona": "concise",
                "task_types": task_types,
                "version": "1.0.0",
            }
        }))
    return roles_root, mcps_root


def _index(roles_tree, embed_fn=_fake_embed) -> CapabilityIndex:
    roles_root, mcps_root = roles_tree
    return CapabilityIndex(
        "test-01", roles_root=roles_root, mcps_root=mcps_root, embed_fn=embed_fn,
    )


class TestSemanticRouting:
    def test_no_substring_match_resolves_semantically(self, roles_tree, monkeypatch):
        """The headline: a task description sharing zero literal tokens with
        the purpose still routes to the right role."""
        monkeypatch.setenv("ACC_SEMANTIC_ROUTING", "1")
        idx = _index(roles_tree)
        reply = idx.query(CapabilityQuery(kind="role", domain="ship a python microservice"))
        assert reply.matches, "semantic path returned nothing"
        assert reply.matches[0].name == "coding_agent"
        assert "similarity" in reply.matches[0].metadata

    def test_ranking_orders_by_similarity(self, roles_tree, monkeypatch):
        monkeypatch.setenv("ACC_SEMANTIC_ROUTING", "1")
        idx = _index(roles_tree)
        reply = idx.query(CapabilityQuery(
            kind="role", domain="weigh clinical evidence for patients",
        ))
        assert reply.matches[0].name == "clinical_reviewer"
        sims = [m.metadata["similarity"] for m in reply.matches]
        assert sims == sorted(sims, reverse=True)

    def test_flag_off_keeps_substring_behaviour(self, roles_tree, monkeypatch):
        """Default off: byte-identical pre-024 filter — the same query that
        routes semantically above finds nothing by substring."""
        monkeypatch.delenv("ACC_SEMANTIC_ROUTING", raising=False)
        idx = _index(roles_tree)
        reply = idx.query(CapabilityQuery(kind="role", domain="ship a python microservice"))
        assert reply.matches == []

    def test_task_type_filter_still_hard(self, roles_tree, monkeypatch):
        """Structured filters stay hard filters — semantics only ranks
        within them."""
        monkeypatch.setenv("ACC_SEMANTIC_ROUTING", "1")
        idx = _index(roles_tree)
        reply = idx.query(CapabilityQuery(
            kind="role", domain="ship a python microservice", task_type="ANALYSE",
        ))
        assert [m.name for m in reply.matches] == ["analyst"]

    def test_embed_failure_falls_back_to_substring(self, roles_tree, monkeypatch):
        monkeypatch.setenv("ACC_SEMANTIC_ROUTING", "1")

        calls = {"n": 0}

        def flaky_embed(text: str) -> list[float]:
            calls["n"] += 1
            if calls["n"] > 3:  # rebuild embeds 3 purposes fine; query embed fails
                raise RuntimeError("embedder down")
            return _fake_embed(text)

        idx = _index(roles_tree, embed_fn=flaky_embed)
        reply = idx.query(CapabilityQuery(kind="role", domain="software_engineering"))
        # Fallback substring path still answers (purpose contains the token).
        assert [m.name for m in reply.matches] == ["coding_agent"]

    def test_infused_role_routable_after_rebuild(self, roles_tree, monkeypatch):
        """G4: a role that appears on disk is semantically routable in the
        same rebuild cycle that scans it."""
        monkeypatch.setenv("ACC_SEMANTIC_ROUTING", "1")
        roles_root, _ = roles_tree
        idx = _index(roles_tree)

        (roles_root / "sec_analyst").mkdir()
        (roles_root / "sec_analyst" / "role.yaml").write_text(yaml.safe_dump({
            "role_definition": {
                "purpose": "Deep analysis of market data filings.",
                "persona": "analytical",
                "task_types": ["FILING_ANALYSIS"],
                "version": "0.1.0",
            }
        }))
        idx.rebuild()
        reply = idx.query(CapabilityQuery(kind="role", domain="crunch the data"))
        assert reply.matches[0].name in ("sec_analyst", "analyst")
        assert any(m.name == "sec_analyst" for m in reply.matches)

    def test_flag_off_skips_embedding_entirely(self, roles_tree, monkeypatch):
        """With the flag off the embed_fn must never be called — default
        deployments pay zero cost (no model load, no per-purpose encode)."""
        monkeypatch.delenv("ACC_SEMANTIC_ROUTING", raising=False)

        def exploding_embed(text: str) -> list[float]:
            raise AssertionError("embed_fn called with ACC_SEMANTIC_ROUTING off")

        idx = _index(roles_tree, embed_fn=exploding_embed)
        reply = idx.query(CapabilityQuery(kind="role", domain="software_engineering"))
        assert [m.name for m in reply.matches] == ["coding_agent"]
