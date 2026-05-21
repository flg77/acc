"""Tests for the declarative collective spec (PR-B).

Covers:
* :class:`CollectiveSpec` / :class:`AgentSpec` Pydantic validation.
* Load + dump round-trip.
* :func:`upsert_agent_entry` — the Nucleus-Apply helper (PR-D's hook).
* :func:`roles_to_compose` — overlay synthesis shape.
* :func:`reconcile` — diff against a stubbed ``podman ps`` list.
* The three preset YAMLs at the repo root parse cleanly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from acc.collective import (
    AgentSpec,
    CollectiveSpec,
    dump_collective,
    load_collective,
    reconcile,
    roles_to_compose,
    upsert_agent_entry,
)


# ---------------------------------------------------------------------------
# Pydantic validation
# ---------------------------------------------------------------------------


class TestAgentSpecValidation:
    def test_minimum(self):
        a = AgentSpec(role="coding_agent")
        assert a.role == "coding_agent"
        assert a.replicas == 1
        assert a.cluster_id is None

    def test_replicas_range(self):
        with pytest.raises(ValidationError):
            AgentSpec(role="x", replicas=-1)
        with pytest.raises(ValidationError):
            AgentSpec(role="x", replicas=101)

    def test_empty_role_rejected(self):
        with pytest.raises(ValidationError):
            AgentSpec(role="")
        with pytest.raises(ValidationError):
            AgentSpec(role="   ")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            AgentSpec(role="x", unknown_field=True)  # type: ignore[call-arg]


class TestCollectiveSpecValidation:
    def test_minimum(self):
        c = CollectiveSpec(collective_id="sol-01")
        assert c.collective_id == "sol-01"
        assert c.agents == []

    def test_collective_id_must_be_dns_label_safe(self):
        with pytest.raises(ValidationError):
            CollectiveSpec(collective_id="Sol_01")          # underscore
        with pytest.raises(ValidationError):
            CollectiveSpec(collective_id="sol.01")          # dot
        with pytest.raises(ValidationError):
            CollectiveSpec(collective_id="-leading-dash")
        with pytest.raises(ValidationError):
            CollectiveSpec(collective_id="trailing-dash-")
        # Valid forms.
        CollectiveSpec(collective_id="sol-01")
        CollectiveSpec(collective_id="a")
        CollectiveSpec(collective_id="a1-b2-c3")


# ---------------------------------------------------------------------------
# Load + dump round-trip
# ---------------------------------------------------------------------------


class TestLoadDump:
    def test_roundtrip(self, tmp_path: Path):
        path = tmp_path / "collective.yaml"
        spec = CollectiveSpec(
            collective_id="sol-01",
            agents=[
                AgentSpec(role="coding_agent", replicas=3,
                          cluster_id="backend", purpose="implement"),
                AgentSpec(role="research_planner", replicas=1,
                          cluster_id="planner"),
            ],
        )
        dump_collective(spec, path)
        loaded = load_collective(path)
        assert loaded.collective_id == spec.collective_id
        assert len(loaded.agents) == 2
        assert loaded.agents[0].role == "coding_agent"
        assert loaded.agents[0].replicas == 3
        assert loaded.agents[0].cluster_id == "backend"

    def test_load_top_level_not_mapping_raises(self, tmp_path: Path):
        path = tmp_path / "bad.yaml"
        path.write_text("- just\n- a\n- list\n")
        with pytest.raises(ValueError):
            load_collective(path)

    def test_load_missing_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_collective(tmp_path / "missing.yaml")


# ---------------------------------------------------------------------------
# upsert_agent_entry — PR-D's hook
# ---------------------------------------------------------------------------


class TestUpsertAgentEntry:
    def test_appends_new(self, tmp_path: Path):
        path = tmp_path / "collective.yaml"
        dump_collective(CollectiveSpec(collective_id="sol-01"), path)

        upsert_agent_entry(path, "coding_agent", cluster_id="backend",
                           purpose="impl", replicas=1)
        loaded = load_collective(path)
        assert len(loaded.agents) == 1
        assert loaded.agents[0].role == "coding_agent"
        assert loaded.agents[0].cluster_id == "backend"
        assert loaded.agents[0].purpose == "impl"
        assert loaded.agents[0].replicas == 1

    def test_increments_existing_match(self, tmp_path: Path):
        path = tmp_path / "collective.yaml"
        dump_collective(CollectiveSpec(
            collective_id="sol-01",
            agents=[AgentSpec(role="coding_agent", replicas=2,
                               cluster_id="backend", purpose="impl")],
        ), path)

        upsert_agent_entry(path, "coding_agent", cluster_id="backend",
                           purpose="something else", replicas=1)
        loaded = load_collective(path)
        assert len(loaded.agents) == 1
        assert loaded.agents[0].replicas == 3
        # Existing purpose is preserved (we only fill it on first sight).
        assert loaded.agents[0].purpose == "impl"

    def test_different_cluster_id_appends_separate_entry(self, tmp_path: Path):
        path = tmp_path / "collective.yaml"
        dump_collective(CollectiveSpec(
            collective_id="sol-01",
            agents=[AgentSpec(role="coding_agent", replicas=2,
                               cluster_id="backend")],
        ), path)

        upsert_agent_entry(path, "coding_agent", cluster_id="frontend",
                           replicas=2)
        loaded = load_collective(path)
        assert len(loaded.agents) == 2


# ---------------------------------------------------------------------------
# roles_to_compose — overlay synthesis
# ---------------------------------------------------------------------------


class TestRolesToCompose:
    def test_no_agents_yields_empty_services(self):
        overlay = roles_to_compose(
            CollectiveSpec(collective_id="sol-01"),
        )
        assert overlay["services"] == {}

    def test_three_replicas_produces_three_services(self):
        spec = CollectiveSpec(
            collective_id="sol-01",
            agents=[AgentSpec(role="coding_agent", replicas=3,
                               cluster_id="backend",
                               agent_id_prefix="coding")],
        )
        overlay = roles_to_compose(spec)
        services = overlay["services"]
        assert set(services.keys()) == {
            "acc-cell-coding-1",
            "acc-cell-coding-2",
            "acc-cell-coding-3",
        }

    def test_service_shape_matches_base_compose_pattern(self):
        spec = CollectiveSpec(
            collective_id="sol-01",
            agents=[AgentSpec(role="coding_agent", replicas=1,
                               cluster_id="backend",
                               agent_id_prefix="coding")],
        )
        svc = roles_to_compose(spec)["services"]["acc-cell-coding-1"]
        env = svc["environment"]
        assert env["ACC_AGENT_ROLE"] == "coding_agent"
        assert env["ACC_AGENT_ID"] == "coding-1"
        assert env["ACC_COLLECTIVE_ID"] == "sol-01"
        assert env["ACC_CLUSTER_ID"] == "backend"
        assert env["ACC_NATS_URL"] == "nats://nats:4222"
        assert env["ACC_LANCEDB_PATH"] == "/app/data/lancedb/coding-1"
        # Volume mounts match the base compose's coding-split shape.
        assert "lancedb-data:/app/data/lancedb:U,z" in svc["volumes"]
        assert "../../acc-config.yaml:/app/acc-config.yaml:ro,z" in svc["volumes"]
        # Synthesized-label so the reconciler can find them.
        assert svc["labels"]["acc.synthesized"] == "true"
        assert svc["labels"]["acc.collective_id"] == "sol-01"
        assert svc["labels"]["acc.role"] == "coding_agent"

    def test_purpose_threaded_as_env_var(self):
        spec = CollectiveSpec(
            collective_id="sol-01",
            agents=[AgentSpec(role="coding_agent", replicas=1,
                               purpose="Implement Fibonacci")],
        )
        svc = next(iter(roles_to_compose(spec)["services"].values()))
        assert svc["environment"]["ACC_AGENT_PURPOSE"] == "Implement Fibonacci"

    def test_extra_env_wins_over_defaults(self):
        spec = CollectiveSpec(
            collective_id="sol-01",
            agents=[AgentSpec(
                role="coding_agent", replicas=1,
                extra_env={"ACC_NATS_URL": "nats://override:4222"},
            )],
        )
        svc = next(iter(roles_to_compose(spec)["services"].values()))
        assert svc["environment"]["ACC_NATS_URL"] == "nats://override:4222"

    def test_prefix_defaults_to_role_with_hyphens(self):
        spec = CollectiveSpec(
            collective_id="sol-01",
            agents=[AgentSpec(role="research_critic", replicas=1)],
        )
        svc_names = list(roles_to_compose(spec)["services"].keys())
        # "research_critic" → prefix "research-critic" → "acc-cell-research-critic-1"
        assert svc_names == ["acc-cell-research-critic-1"]


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


class TestReconcile:
    def _spec(self):
        return CollectiveSpec(
            collective_id="sol-01",
            agents=[AgentSpec(role="coding_agent", replicas=2,
                               agent_id_prefix="coding")],
        )

    def test_empty_podman_means_all_to_start(self):
        r = reconcile(self._spec(), podman_ps=[])
        assert r.to_start == ["acc-cell-coding-1", "acc-cell-coding-2"]
        assert r.to_stop == []
        assert r.unchanged == []

    def test_one_running_is_unchanged_one_to_start(self):
        ps = [{"Names": ["acc-cell-coding-1"]}]
        r = reconcile(self._spec(), podman_ps=ps)
        assert r.to_start == ["acc-cell-coding-2"]
        assert r.unchanged == ["acc-cell-coding-1"]
        assert r.to_stop == []

    def test_extra_container_is_to_stop(self):
        ps = [
            {"Names": ["acc-cell-coding-1"]},
            {"Names": ["acc-cell-coding-2"]},
            {"Names": ["acc-cell-orphan-9"]},
        ]
        r = reconcile(self._spec(), podman_ps=ps)
        assert r.to_start == []
        assert r.unchanged == ["acc-cell-coding-1", "acc-cell-coding-2"]
        assert r.to_stop == ["acc-cell-orphan-9"]

    def test_handles_legacy_name_string_format(self):
        """Older podman versions return Names as a single slash-prefixed
        string instead of a list."""
        ps = [{"Names": "/acc-cell-coding-1"}]
        r = reconcile(self._spec(), podman_ps=ps)
        assert "acc-cell-coding-1" in r.unchanged


# ---------------------------------------------------------------------------
# Shipped presets at the repo root parse cleanly
# ---------------------------------------------------------------------------


class TestShippedPresets:
    @pytest.fixture
    def repo_root(self) -> Path:
        # tests/ is one level below the repo root.
        return Path(__file__).resolve().parent.parent

    def test_default_collective_yaml_parses(self, repo_root: Path):
        spec = load_collective(repo_root / "collective.yaml")
        assert spec.collective_id == "sol-01"
        assert spec.agents == []  # shipped empty by design

    def test_coding_split_preset_parses(self, repo_root: Path):
        spec = load_collective(repo_root / "collective.coding-split.yaml")
        assert spec.collective_id == "sol-01"
        # 3 coding_agents, cluster backend.
        coding = [a for a in spec.agents if a.role == "coding_agent"]
        assert len(coding) == 1
        assert coding[0].replicas == 3
        assert coding[0].cluster_id == "backend"
        # And synthesizes to acc-cell-coding-1/2/3.
        names = list(roles_to_compose(spec)["services"].keys())
        assert names == ["acc-cell-coding-1", "acc-cell-coding-2",
                          "acc-cell-coding-3"]

    def test_autoresearcher_preset_parses(self, repo_root: Path):
        spec = load_collective(repo_root / "collective.autoresearcher.yaml")
        roles = [a.role for a in spec.agents]
        # 6 research roles per expected_topology.md.
        assert set(roles) == {
            "research_planner", "research_economist", "research_competitor",
            "research_strategist", "research_synthesizer", "research_critic",
        }
        # Economist + competitor have 2 replicas each (heuristic phase 2).
        for a in spec.agents:
            if a.role in ("research_economist", "research_competitor"):
                assert a.replicas == 2
