"""Named deployment profiles: validated, reversible, and honest about limits.

Three properties, and each has a failure mode worse than not having the feature:

* **half-applied** leaves the deployment in a state no profile describes, so
  validation runs before anything is written and a failed validation writes
  nothing at all;
* **irreversible** means a profile switch is really a migration, so applying
  records what it replaced;
* **silently incomplete on export** means the receiving site discovers the gap
  when an agent cannot authenticate, so an export states what it does not carry.

A fourth is tested because it is easy to get wrong quietly: an unknown key in a
profile is **refused**, not ignored. A profile that drops a key an operator
wrote does not describe the deployment it claims to.
"""

from __future__ import annotations

import json

import pytest

from acc import profiles as P

MODELS = """\
models:
  - model_id: local
    backend: ollama
    model: llama3.2:3b
  - model_id: cloud
    backend: anthropic
    model: claude-haiku-4-6
    api_key_env: ANTHROPIC_API_KEY
role_models:
  analyst: local
"""

ACC_CONFIG = """\
deploy_mode: standalone
operator_mode: prod
agent:
  role: ingester
llm:
  backend: ollama
  ollama_model: llama3.2:3b
"""


@pytest.fixture
def site(tmp_path, monkeypatch):
    (tmp_path / "acc-config.yaml").write_text(ACC_CONFIG, encoding="utf-8")
    (tmp_path / "models.yaml").write_text(MODELS, encoding="utf-8")
    (tmp_path / "profiles").mkdir()
    monkeypatch.setenv("ACC_CONFIG_PATH", str(tmp_path / "acc-config.yaml"))
    monkeypatch.setenv("ACC_MODELS_PATH", str(tmp_path / "models.yaml"))
    monkeypatch.setenv("ACC_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(tmp_path / "collective.yaml"))
    monkeypatch.setenv("ACC_CATALOGS_PATH", str(tmp_path / "catalogs.yaml"))
    monkeypatch.setenv(P.PROFILES_DIR_VAR, str(tmp_path / "profiles"))
    return tmp_path


def write(site, name, body):
    (site / "profiles" / f"{name}.yaml").write_text(body, encoding="utf-8")


EDGE = """\
description: Local models only
settings:
  llm.backend: ollama
  llm.ollama_model: qwen2.5:7b
role_models:
  analyst: local
"""

HARDENED = """\
description: Compliance floor raised
settings:
  operator_mode: prod
  compliance.cat_a_enforce: true
"""


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


class TestLoading:
    def test_an_unknown_key_is_refused_not_ignored(self, site):
        """A profile that silently drops a key does not describe the deployment."""
        write(site, "bad", "settings:\n  llm.backend: ollama\n  totally.made.up: 1\n")
        with pytest.raises(P.ProfileError, match="may not set"):
            P.load_profile("bad")

    def test_a_missing_profile_lists_what_exists(self, site):
        write(site, "edge", EDGE)
        with pytest.raises(P.ProfileError, match="edge"):
            P.load_profile("nope")

    def test_a_malformed_file_is_refused(self, site):
        write(site, "broken", "settings: [not, a, mapping]\n")
        with pytest.raises(P.ProfileError):
            P.load_profile("broken")

    def test_round_trip(self, site):
        write(site, "edge", EDGE)
        profile = P.load_profile("edge")
        assert profile.settings["llm.ollama_model"] == "qwen2.5:7b"
        assert profile.role_models == {"analyst": "local"}


# --------------------------------------------------------------------------
# Validation gates application
# --------------------------------------------------------------------------


class TestValidationGatesApply:
    def test_a_bad_enum_fails_validation(self, site):
        write(site, "bad", "settings:\n  llm.backend: not_a_backend\n")
        result = P.validate(P.load_profile("bad"))
        assert not result.ok
        assert any("not_a_backend" in p for p in result.problems)

    def test_an_unknown_model_fails_validation(self, site):
        write(site, "bad", "settings: {}\nrole_models:\n  analyst: ghost\n")
        result = P.validate(P.load_profile("bad"))
        assert not result.ok
        assert any("ghost" in p for p in result.problems)

    def test_apply_writes_nothing_when_validation_fails(self, site):
        """Half-applied is worse than either the old profile or the new one."""
        before = (site / "acc-config.yaml").read_bytes()
        write(site, "bad", "settings:\n  llm.backend: not_a_backend\n")
        with pytest.raises(P.ProfileError, match="nothing was changed"):
            P.apply(P.load_profile("bad"))
        assert (site / "acc-config.yaml").read_bytes() == before

    def test_a_valid_profile_validates(self, site):
        write(site, "edge", EDGE)
        assert P.validate(P.load_profile("edge")).ok

    def test_missing_declared_env_is_reported_without_blocking(self, site):
        write(site, "needs", "settings: {}\nrequires_env:\n  - SOME_SITE_KEY\n")
        result = P.validate(P.load_profile("needs"))
        assert result.ok, "a missing credential is the site's to provide, not a fault"
        assert result.missing_env == ["SOME_SITE_KEY"]


# --------------------------------------------------------------------------
# Apply and revert
# --------------------------------------------------------------------------


class TestApplyIsReversible:
    def test_apply_changes_the_configuration(self, site):
        write(site, "edge", EDGE)
        changes = P.apply(P.load_profile("edge"))
        assert any(c.key == "llm.ollama_model" for c in changes)

        from acc import configstore as store

        assert store.get("llm.ollama_model").value == "qwen2.5:7b"

    def test_dry_run_writes_nothing(self, site):
        write(site, "edge", EDGE)
        before = (site / "acc-config.yaml").read_bytes()
        changes = P.apply(P.load_profile("edge"), dry_run=True)
        assert changes
        assert (site / "acc-config.yaml").read_bytes() == before
        assert P.active_profile() is None

    def test_the_active_profile_is_reportable(self, site):
        write(site, "edge", EDGE)
        P.apply(P.load_profile("edge"))
        active = P.active_profile()
        assert active and active["name"] == "edge"

    def test_revert_restores_what_was_replaced(self, site):
        write(site, "edge", EDGE)
        from acc import configstore as store

        original = store.get("llm.ollama_model").value
        P.apply(P.load_profile("edge"))
        assert store.get("llm.ollama_model").value != original

        P.revert()
        assert store.get("llm.ollama_model").value == original

    def test_revert_clears_the_active_marker(self, site):
        write(site, "edge", EDGE)
        P.apply(P.load_profile("edge"))
        P.revert()
        assert P.active_profile() is None

    def test_revert_without_an_apply_is_refused(self, site):
        with pytest.raises(P.ProfileError, match="no recorded"):
            P.revert()

    def test_applying_twice_is_a_no_op_the_second_time(self, site):
        write(site, "edge", EDGE)
        P.apply(P.load_profile("edge"))
        assert P.apply(P.load_profile("edge")) == []


# --------------------------------------------------------------------------
# Posture
# --------------------------------------------------------------------------


class TestPostureChangesAreDistinguished:
    def test_posture_keys_are_flagged_in_the_diff(self, site):
        """Nobody should raise or lower the governance floor by accident."""
        write(site, "hard", HARDENED)
        changes = P.diff(P.load_profile("hard"))
        posture = [c for c in changes if c.posture]
        assert posture and all(c.key in P.POSTURE_KEYS for c in posture)

    def test_a_model_change_is_not_a_posture_change(self, site):
        write(site, "edge", EDGE)
        assert not any(c.posture for c in P.diff(P.load_profile("edge")))

    def test_the_marker_records_posture_changes(self, site):
        write(site, "hard", HARDENED)
        P.apply(P.load_profile("hard"))
        assert P.active_profile()["posture_changes"]


# --------------------------------------------------------------------------
# Export / import
# --------------------------------------------------------------------------


class TestExportIsHonest:
    def test_export_states_what_the_site_must_provide(self, site):
        """The failure this prevents: discovering the gap at first auth error."""
        write(site, "cloud", "settings: {}\nrole_models:\n  analyst: cloud\n")
        doc = P.export_profile("cloud")
        assert "ANTHROPIC_API_KEY" in doc["requires"]["environment"]
        assert "not included" in doc["requires"]["note"].lower()

    def test_export_carries_no_credentials(self, site):
        write(
            site, "leaky",
            "settings:\n  llm.api_key_env: SOME_KEY\n  llm.backend: ollama\n",
        )
        doc = P.export_profile("leaky")
        assert "llm.api_key_env" not in doc["settings"]
        assert "llm.api_key_env" in doc["not_carried"]

    def test_import_installs_without_applying(self, site):
        write(site, "edge", EDGE)
        doc = P.export_profile("edge")
        (site / "profiles" / "edge.yaml").unlink()

        P.import_profile(doc)
        assert "edge" in P.list_profiles()
        assert P.active_profile() is None, "import must not apply"

    def test_import_refuses_to_clobber_by_default(self, site):
        write(site, "edge", EDGE)
        doc = P.export_profile("edge")
        with pytest.raises(P.ProfileError, match="already exists"):
            P.import_profile(doc)
        P.import_profile(doc, overwrite=True)

    def test_an_unknown_document_version_is_refused(self, site):
        with pytest.raises(P.ProfileError, match="version"):
            P.import_profile({"name": "x", "acc_profile_version": 99})

    def test_a_non_profile_document_is_refused(self, site):
        with pytest.raises(P.ProfileError, match="not an exported"):
            P.import_profile({"hello": "world"})

    def test_an_exported_document_is_json_serialisable(self, site):
        write(site, "edge", EDGE)
        assert json.loads(json.dumps(P.export_profile("edge")))["name"] == "edge"
