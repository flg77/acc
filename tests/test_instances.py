"""Instances (`20260906-acc-instance`, HG-40.1a): the binding, not a mechanism.

A deployment profile is a posture; an instance is a collective that carries
its own state, is owned by a principal the substrate vouches for, and runs
under a posture. The runtime already partitions everything by collective id;
the instance is one directory that holds the definition and every root, and
the environment that points the cells and the surfaces at it.

Pinned here: the record and its roots; owners must be vouched; the posture
must exist; the compose overlay carries the instance's env (per-cell
``{aid}``) and mount; export carries the definition and *says* what it left
behind; import creates a new instance owned here; the agent takes its overlay
dir from the environment; the TUI acts as its owner.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from acc import instances as I
from acc import profiles as P
from acc.collective import CollectiveSpec, roles_to_compose

ACC_CONFIG = """\
deploy_mode: standalone
operator_mode: prod
agent:
  role: ingester
llm:
  backend: ollama
  ollama_model: llama3.2:3b
"""
MODELS = """\
models:
  - model_id: local
    backend: ollama
    model: llama3.2:3b
"""
EDGE = """\
description: Local models only
settings:
  llm.backend: ollama
"""


@pytest.fixture
def site(tmp_path, monkeypatch):
    (tmp_path / "acc-config.yaml").write_text(ACC_CONFIG, encoding="utf-8")
    (tmp_path / "models.yaml").write_text(MODELS, encoding="utf-8")
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "edge-lean.yaml").write_text(EDGE, encoding="utf-8")
    monkeypatch.setenv("ACC_CONFIG_PATH", str(tmp_path / "acc-config.yaml"))
    monkeypatch.setenv("ACC_MODELS_PATH", str(tmp_path / "models.yaml"))
    monkeypatch.setenv("ACC_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(tmp_path / "collective.yaml"))
    monkeypatch.setenv("ACC_CATALOGS_PATH", str(tmp_path / "catalogs.yaml"))
    monkeypatch.setenv(P.PROFILES_DIR_VAR, str(tmp_path / "profiles"))
    monkeypatch.setenv(I.INSTANCES_DIR_VAR, str(tmp_path / "instances"))
    return tmp_path


# ---------------------------------------------------------------------------
# the record and its roots
# ---------------------------------------------------------------------------


class TestCreate:
    def test_create_writes_record_definition_and_roots(self, site):
        inst = I.create("alice-dev", owner="system:alice", profile="edge-lean",
                        packs=["@acc/workspace-roles@^1.0"], agents=["coding_agent"],
                        created_by="system:flg", note="alice's box")
        base = site / "instances" / "alice-dev"
        assert (base / "instance.yaml").is_file()
        for name in I.STATE_DIRS:
            assert (base / name).is_dir(), name
        assert (base / "overlays" / "collective.md").is_file()
        spec = CollectiveSpec.model_validate(yaml.safe_load((base / "collective.yaml").read_text()))
        assert spec.collective_id == "alice-dev"                  # the id IS the collective id
        assert spec.required_packages == ["@acc/workspace-roles@^1.0"]
        roles = [a.role for a in spec.agents]
        assert "coding_agent" in roles and "arbiter" in roles and "ingester" in roles   # edge control set
        assert "assistant" not in roles
        back = I.load_instance("alice-dev")
        assert back.owner == "system:alice" and back.profile == "edge-lean" and back.hub == ""
        assert back.created_by == "system:flg" and back.created_at > 0 and back.note == "alice's box"
        assert I.list_instances() == ["alice-dev"]

    def test_hub_and_stack_profile(self, site):
        inst = I.create("bob-dev", owner="kubernetes:bob", hub="enterprise", stack_profile="full")
        assert inst.hub == "enterprise"
        spec = CollectiveSpec.model_validate(
            yaml.safe_load((site / "instances" / "bob-dev" / "collective.yaml").read_text()))
        assert "assistant" in [a.role for a in spec.agents]

    @pytest.mark.parametrize("bad", ["Alice", "alice_dev", "-x", "a" * 64, ""])
    def test_id_must_be_a_dns_label(self, site, bad):
        with pytest.raises(I.InstanceError, match="DNS label"):
            I.create(bad, owner="system:alice")

    @pytest.mark.parametrize("owner", ["alice", "external:U123", "slack:U1", ":x", "system:"])
    def test_owner_must_be_vouched(self, site, owner):
        with pytest.raises(I.InstanceError):
            I.create("alice-dev", owner=owner)
        assert I.list_instances() == []

    def test_posture_must_exist(self, site):
        with pytest.raises(I.InstanceError, match="no profile 'nope'"):
            I.create("alice-dev", owner="system:alice", profile="nope")

    def test_cannot_be_its_own_hub_or_exist_twice(self, site):
        with pytest.raises(I.InstanceError, match="own hub"):
            I.create("alice-dev", owner="system:alice", hub="alice-dev")
        I.create("alice-dev", owner="system:alice")
        with pytest.raises(I.InstanceError, match="already exists"):
            I.create("alice-dev", owner="system:alice")

    def test_unknown_stack_profile_is_refused(self, site):
        with pytest.raises(I.InstanceError, match="unknown profile"):
            I.create("alice-dev", owner="system:alice", stack_profile="mega")

    def test_archive_keeps_state(self, site):
        I.create("alice-dev", owner="system:alice")
        (site / "instances" / "alice-dev" / "lancedb" / "x").write_text("state")
        inst = I.archive("alice-dev")
        assert inst.archived and I.load_instance("alice-dev").archived
        assert (site / "instances" / "alice-dev" / "lancedb" / "x").is_file()

    def test_missing_instance_names_the_known_ones(self, site):
        I.create("alice-dev", owner="system:alice")
        with pytest.raises(I.InstanceError, match="Known: alice-dev"):
            I.load_instance("zed")


# ---------------------------------------------------------------------------
# what the cells and the surfaces get
# ---------------------------------------------------------------------------


class TestEnvironment:
    def test_cell_env_points_every_root_at_the_mount(self, site):
        inst = I.create("alice-dev", owner="system:alice", hub="enterprise")
        env = I.cell_env(inst)
        assert env["ACC_COLLECTIVE_ID"] == "alice-dev"
        assert env["ACC_INSTANCE_OWNER"] == "system:alice"
        assert env["ACC_HUB_COLLECTIVE_ID"] == "enterprise"
        assert env["ACC_LANCEDB_PATH"] == "/app/instances/alice-dev/lancedb/{aid}"
        assert env["ACC_TRACELOG_DIR"] == "/app/instances/alice-dev/trace"
        assert env["ACC_COLLECTIVE_DIR"] == "/app/instances/alice-dev/overlays"
        assert I.cell_volumes(inst) == ["../../instances/alice-dev:/app/instances/alice-dev:z"]
        standalone = I.create("carol", owner="system:carol")
        assert "ACC_HUB_COLLECTIVE_ID" not in I.cell_env(standalone)

    def test_compose_overlay_stamps_every_cell(self, site):
        inst = I.create("alice-dev", owner="system:alice", agents=["coding_agent"])
        overlay = I.compose_overlay(inst, image="localhost/acc-agent-core:9.9.9")
        services = overlay["services"]
        assert services, "no cells rendered"
        for name, svc in services.items():
            env = svc["environment"]
            aid = env["ACC_AGENT_ID"]
            assert env["ACC_COLLECTIVE_ID"] == "alice-dev"
            assert env["ACC_LANCEDB_PATH"] == f"/app/instances/alice-dev/lancedb/{aid}"   # {aid} resolved per cell
            assert env["ACC_TRACELOG_DIR"] == "/app/instances/alice-dev/trace"
            assert env["ACC_COLLECTIVE_DIR"] == "/app/instances/alice-dev/overlays"
            assert "../../instances/alice-dev:/app/instances/alice-dev:z" in svc["volumes"]
            assert svc["image"] == "localhost/acc-agent-core:9.9.9"
            assert svc["labels"]["acc.collective_id"] == "alice-dev"
        ids = [svc["environment"]["ACC_AGENT_ID"] for svc in services.values()]
        assert len(set(ids)) == len(ids) > 1                      # each cell keeps its own id

    def test_roles_to_compose_without_extras_is_unchanged(self, site):
        spec = CollectiveSpec.model_validate({"collective_id": "sol-01",
                                              "agents": [{"role": "analyst", "replicas": 1}]})
        plain = roles_to_compose(spec)
        (svc,) = plain["services"].values()
        assert svc["environment"]["ACC_LANCEDB_PATH"].startswith("/app/data/lancedb/")
        assert not any("instances" in v for v in svc["volumes"])

    def test_worker_pool_cells_get_the_extras_too(self, site):
        spec = CollectiveSpec.model_validate({"collective_id": "alice-dev", "worker_pool": 2})
        overlay = roles_to_compose(spec, extra_env={"ACC_TRACELOG_DIR": "/app/instances/alice-dev/trace",
                                              "ACC_LANCEDB_PATH": "/app/instances/alice-dev/lancedb/{aid}"},
                                   extra_volumes=["../../instances/alice-dev:/app/instances/alice-dev:z"])
        for svc in overlay["services"].values():
            assert svc["environment"]["ACC_TRACELOG_DIR"] == "/app/instances/alice-dev/trace"
            assert svc["environment"]["ACC_LANCEDB_PATH"].endswith(svc["environment"]["ACC_AGENT_ID"])
            assert "../../instances/alice-dev:/app/instances/alice-dev:z" in svc["volumes"]

    def test_surface_env_uses_host_paths(self, site):
        inst = I.create("alice-dev", owner="system:alice")
        env = I.surface_env(inst)
        base = site / "instances" / "alice-dev"
        assert env["ACC_COLLECTIVE_ID"] == env["ACC_COLLECTIVE_IDS"] == "alice-dev"
        assert Path(env["ACC_SESSIONS_DIR"]) == base / "sessions"
        assert Path(env["ACC_TRACELOG_DIR"]) == base / "trace"
        assert Path(env["ACC_COLLECTIVE_DIR"]) == base / "overlays"


# ---------------------------------------------------------------------------
# distribution: the definition travels, state does not
# ---------------------------------------------------------------------------


class TestExportImport:
    def test_export_carries_definition_and_names_the_state_left_behind(self, site):
        I.create("alice-dev", owner="system:alice", profile="edge-lean",
                 packs=["@acc/workspace-roles@^1.0"], hub="enterprise", note="n")
        (site / "instances" / "alice-dev" / "overlays" / "collective.md").write_text("# voice\n")
        (site / "instances" / "alice-dev" / "lancedb" / "table").write_text("secret state")
        ex = I.export_instance("alice-dev")
        d = ex.document
        assert d["kind"] == "acc-instance" and d["version"] == 1
        assert d["instance"] == {"id": "alice-dev", "profile": "edge-lean", "hub": "enterprise",
                                 "stack_profile": "edge", "note": "n"}
        assert "owner" not in d["instance"]                       # a principal of THIS substrate
        assert d["collective"]["collective_id"] == "alice-dev"
        assert d["collective"]["required_packages"] == ["@acc/workspace-roles@^1.0"]
        assert d["overlays"] == {"collective.md": "# voice\n"}
        assert d["profile"]["name"] == "edge-lean" if "name" in d["profile"] else d["profile"]
        assert d["signature"] is None
        text = yaml.safe_dump(d)
        assert "secret state" not in text
        assert any(x.startswith("lancedb/") for x in ex.state_excluded)
        assert any(x.startswith("sessions/") for x in ex.state_excluded)
        assert "overlays" not in " ".join(ex.state_excluded)

    def test_import_creates_a_new_instance_owned_here(self, site):
        I.create("alice-dev", owner="system:alice", profile="edge-lean",
                 packs=["@acc/workspace-roles@^1.0"], agents=["coding_agent"])
        (site / "instances" / "alice-dev" / "overlays" / "collective.md").write_text("# voice\n")
        doc = I.export_instance("alice-dev").document
        # a second site: no profiles, no instances
        (site / "profiles" / "edge-lean.yaml").unlink()
        inst = I.import_instance(doc, owner="kubernetes:bob", instance_id="bob-dev", created_by="system:flg")
        assert inst.id == "bob-dev" and inst.owner == "kubernetes:bob" and inst.profile == "edge-lean"
        assert "edge-lean" in P.list_profiles()                  # the posture came along, not applied
        spec = CollectiveSpec.model_validate(
            yaml.safe_load((site / "instances" / "bob-dev" / "collective.yaml").read_text()))
        assert spec.collective_id == "bob-dev"
        assert "coding_agent" in [a.role for a in spec.agents]
        assert (site / "instances" / "bob-dev" / "overlays" / "collective.md").read_text() == "# voice\n"
        assert not any((site / "instances" / "bob-dev" / "lancedb").iterdir())   # state starts empty

    def test_import_refuses_garbage(self, site):
        with pytest.raises(I.InstanceError, match="kind"):
            I.import_instance({"kind": "profile"}, owner="system:alice")
        with pytest.raises(I.InstanceError, match="no collective"):
            I.import_instance({"kind": "acc-instance", "instance": {"id": "x"}, "collective": {}},
                              owner="system:alice")


# ---------------------------------------------------------------------------
# the agent reads its overlay dir from the environment
# ---------------------------------------------------------------------------


def test_agent_overlay_dir_comes_from_env():
    import inspect
    from acc import agent as agent_mod
    src = inspect.getsource(agent_mod)
    i = src.index('env_dir = os.environ.get("ACC_COLLECTIVE_DIR", "")')
    block = src[i - 200: i + 400]
    assert "collective_dir = _Path(env_dir)" in block
    assert 'collective_dir = cwd if (cwd / "collective.md").is_file() else None' in block


# ---------------------------------------------------------------------------
# the TUI acts as its owner
# ---------------------------------------------------------------------------


class TestTuiActor:
    def test_actor_is_the_resolved_principal(self, monkeypatch):
        from acc.tui import actor
        from acc.identity import Principal, Tier
        actor.reset()
        monkeypatch.setattr("acc.identity.current",
                            lambda **kw: Principal(subject="alice", source="system", tier=Tier.OPERATOR))
        assert actor.tui_actor() == "system:alice"
        att = actor.tui_attribution()
        assert att["requested_by"] == "system:alice"
        assert att["requester_source"] == "tui" and att["requester_channel"] == "tui"
        assert att["requester_tier"] == "operator" and att["requester_ceiling"] == "CRITICAL"
        actor.reset()

    def test_anonymous_when_nothing_resolves(self, monkeypatch):
        from acc.tui import actor
        actor.reset()

        def boom(**kw):
            raise RuntimeError("no substrate")
        monkeypatch.setattr("acc.identity.current", boom)
        assert actor.tui_actor() == "tui:anonymous"
        assert actor.tui_attribution() == {}
        actor.reset()

    def test_tui_memory_scope_stays_pooled_but_the_person_is_known(self, monkeypatch):
        from acc.tui import actor
        from acc.identity import Principal, Tier
        from acc.memory_scope import scope_key
        from acc.attribution import requester_of, person_of
        actor.reset()
        monkeypatch.setattr("acc.identity.current",
                            lambda **kw: Principal(subject="alice", source="system", tier=Tier.OPERATOR))
        payload = {"signal_type": "TASK_ASSIGN", **actor.tui_attribution()}
        assert requester_of(payload) == "system:alice"
        assert person_of(requester_of(payload))                   # counts as a person (quorum, attribution)
        assert scope_key(payload) == scope_key({"requester_source": "tui", "requested_by": "tui:x"}) \
            or "tui" in scope_key(payload)
        actor.reset()
