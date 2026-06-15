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

    def test_synthesized_agents_mount_packages_volume(self):
        """Synthesized cells must mount the shared acc-packages volume +
        declare it top-level, or pack-served roles never resolve and the
        cell boots dormant (regression: cells only had roles/ bind-mount)."""
        overlay = roles_to_compose(
            CollectiveSpec(
                collective_id="sol-01",
                required_packages=["@acc/workspace-roles@^1.0"],
                agents=[AgentSpec(role="coding_agent_architect", replicas=1)],
            )
        )
        svc = next(iter(overlay["services"].values()))
        assert "acc-packages:/var/lib/acc/packages:U,z" in svc["volumes"]
        # Top-level decl (bare/null) so -f base -f overlay merges into the
        # one project-prefixed acc-packages volume the base agents use.
        assert "acc-packages" in overlay["volumes"]
        assert overlay["volumes"]["acc-packages"] is None

    def test_worker_pool_agents_mount_packages_volume(self):
        overlay = roles_to_compose(
            CollectiveSpec(
                collective_id="sol-01",
                worker_pool=2,
                agents=[AgentSpec(role="coding_agent_implementer", replicas=1)],
            )
        )
        svc = next(iter(overlay["services"].values()))
        assert "acc-packages:/var/lib/acc/packages:U,z" in svc["volumes"]
        assert overlay["volumes"].get("acc-packages", "MISSING") is None

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
# PR-Q — agentset-defined worker pool
# ---------------------------------------------------------------------------


class TestWorkerPool:
    def test_worker_pool_defaults_zero(self):
        spec = CollectiveSpec(collective_id="sol-01")
        assert spec.worker_pool == 0

    def test_worker_pool_field_validates_range(self):
        import pytest
        CollectiveSpec(collective_id="sol-01", worker_pool=4)
        with pytest.raises(Exception):
            CollectiveSpec(collective_id="sol-01", worker_pool=-1)
        with pytest.raises(Exception):
            CollectiveSpec(collective_id="sol-01", worker_pool=999)

    def test_recommended_pool_size_sums_replicas(self):
        from acc.collective import recommended_pool_size
        spec = CollectiveSpec(
            collective_id="sol-01",
            agents=[
                AgentSpec(role="coding_agent_implementer", replicas=2),
                AgentSpec(role="coding_agent_reviewer", replicas=1),
                AgentSpec(role="coding_agent_tester", replicas=1),
            ],
        )
        assert recommended_pool_size(spec) == 4

    def test_worker_pool_emits_dormant_services_not_concrete(self):
        spec = CollectiveSpec(
            collective_id="sol-01",
            agents=[
                AgentSpec(role="coding_agent_implementer", replicas=2),
                AgentSpec(role="coding_agent_reviewer", replicas=1),
            ],
            worker_pool=3,
        )
        services = roles_to_compose(spec)["services"]
        names = sorted(services.keys())
        # 3 dormant workers; NO concrete acc-cell-* services.
        assert names == ["acc-worker-1", "acc-worker-2", "acc-worker-3"]
        assert not any(n.startswith("acc-cell-") for n in names)

    def test_dormant_service_shape(self):
        spec = CollectiveSpec(
            collective_id="sol-01",
            agents=[AgentSpec(role="coding_agent_implementer", replicas=1)],
            worker_pool=1,
        )
        svc = roles_to_compose(spec)["services"]["acc-worker-1"]
        env = svc["environment"]
        assert env["ACC_AGENT_ROLE"] == "dormant"
        assert env["ACC_AGENT_ID"] == "worker-1"
        assert env["ACC_COLLECTIVE_ID"] == "sol-01"
        # isolated lancedb path per worker.
        assert env["ACC_LANCEDB_PATH"] == "/app/data/lancedb/worker-1"
        # labelled so reconcile / podman ps can spot pool members.
        assert svc["labels"]["acc.role"] == "dormant"
        assert svc["labels"]["acc.worker_pool"] == "true"
        # the .env env_file passthrough carries ACC_ARBITER_VERIFY_KEY.
        assert {"path": "../../.env", "required": False} in svc["env_file"]

    def test_zero_pool_keeps_concrete_agents(self):
        """Default (worker_pool=0) is the PR-B path: concrete agents."""
        spec = CollectiveSpec(
            collective_id="sol-01",
            agents=[AgentSpec(role="coding_agent", replicas=2)],
            worker_pool=0,
        )
        names = sorted(roles_to_compose(spec)["services"].keys())
        assert names == ["acc-cell-coding-agent-1", "acc-cell-coding-agent-2"]
        assert not any(n.startswith("acc-worker-") for n in names)

    def test_worker_pool_roundtrips_through_yaml(self, tmp_path):
        from acc.collective import dump_collective, load_collective
        spec = CollectiveSpec(
            collective_id="sol-01",
            agents=[AgentSpec(role="coding_agent_tester", replicas=1)],
            worker_pool=2,
        )
        path = tmp_path / "collective.yaml"
        dump_collective(spec, path)
        loaded = load_collective(path)
        assert loaded.worker_pool == 2

    def test_shipped_worker_pool_preset_parses(self):
        """The collective.worker-pool.yaml example must load + size
        the pool to the sum of its subrole replicas."""
        from pathlib import Path
        from acc.collective import load_collective, recommended_pool_size
        repo_root = Path(__file__).resolve().parent.parent
        preset = repo_root / "collectives" / "collective.worker-pool.yaml"
        spec = load_collective(preset)
        assert spec.worker_pool == 4
        assert recommended_pool_size(spec) == 4  # 2 + 1 + 1
        # The agents are coding_agent subroles.
        roles = {a.role for a in spec.agents}
        assert roles == {
            "coding_agent_implementer",
            "coding_agent_reviewer",
            "coding_agent_tester",
        }


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

    @pytest.fixture
    def presets_dir(self, repo_root: Path) -> Path:
        # Named presets live under collectives/; the live default stays at root.
        return repo_root / "collectives"

    def test_default_collective_yaml_parses(self, repo_root: Path):
        spec = load_collective(repo_root / "collective.yaml")
        assert spec.collective_id == "sol-01"
        assert spec.agents == []  # shipped empty by design

    def test_coding_split_preset_parses(self, presets_dir: Path):
        spec = load_collective(presets_dir / "collective.coding-split.yaml")
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

    def test_autoresearcher_preset_parses(self, presets_dir: Path):
        spec = load_collective(presets_dir / "collective.autoresearcher.yaml")
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
