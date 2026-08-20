"""Configuration schema + comment-preserving access.

The load-bearing property here is that a write does **not** reformat the file.
ACC's configuration templates are heavily commented and hand-tuned, and the
change that introduced this module was explicit that a round-trip which
re-emits the document would not be adopted.  So the strongest test in this
file is not an assertion about a value — it is that ``set`` followed by
``unset`` restores the file **byte for byte**.

Two bugs found while building this are pinned directly, because both were
silent:

* ``_known_model_ids`` read ``id`` where the registry field is ``model_id``
  (see :class:`acc.models.ModelEntry`).  An empty set makes the reference
  check short-circuit, so *every* role→model binding validated — a guard that
  always passes is worse than no guard.
* Writing through :func:`acc._atomic_write.atomic_write_text` in text mode
  translates ``\\n`` to ``os.linesep``.  On Windows that rewrote every line of
  an LF file, so a one-key edit produced a whole-file diff.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from acc import configschema as cs
from acc import configstore as st

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

MODELS = """\
# Central model registry.
models:
  - model_id: claude-haiku
    backend: anthropic
    model: claude-haiku-4-6
    label: "Haiku"
  - model_id: local-llama
    backend: ollama
    model: "llama3.2:3b"

role_models:
  compliance_officer: claude-haiku    # Cat-A/B/C judgement
"""

ACC_CONFIG = """\
# ACC Configuration
deploy_mode: standalone   # standalone | rhoai

agent:
  role: ingester           # ingester | analyst | arbiter
  collective_id: sol-01

llm:
  backend: ollama
  ollama_model: llama3.2:3b
"""


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Point every configuration surface at a temp copy."""
    acc_config = tmp_path / "acc-config.yaml"
    models = tmp_path / "models.yaml"
    acc_config.write_text(ACC_CONFIG, encoding="utf-8", newline="")
    models.write_text(MODELS, encoding="utf-8", newline="")
    monkeypatch.setenv("ACC_CONFIG_PATH", str(acc_config))
    monkeypatch.setenv("ACC_MODELS_PATH", str(models))
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(tmp_path / "collective.yaml"))
    monkeypatch.setenv("ACC_CATALOGS_PATH", str(tmp_path / "catalogs.yaml"))
    monkeypatch.setenv("ACC_ENV_PATH", str(tmp_path / ".env"))
    return tmp_path


def _bytes(path):
    return path.read_bytes()


# --------------------------------------------------------------------------
# Formatting preservation — the reason this module exists
# --------------------------------------------------------------------------


class TestFormattingIsPreserved:
    def test_add_then_unset_restores_the_file_byte_for_byte(self, cfg):
        target = cfg / "acc-config.yaml"
        original = _bytes(target)

        st.set_key("llm.request_timeout_s", "240")
        assert _bytes(target) != original, "the add must actually write"

        st.unset_key("llm.request_timeout_s")
        assert _bytes(target) == original, (
            "add+unset must round-trip exactly; a byte difference here means "
            "comments, indentation or line endings were rewritten"
        )

    def test_set_changes_exactly_one_line(self, cfg):
        change = st.set_key("deploy_mode", "rhoai")
        assert len(change.diff) == 2, f"expected one -/+ pair, got {change.diff}"
        assert change.diff[0].startswith("-deploy_mode: standalone")
        assert change.diff[1].startswith("+deploy_mode: rhoai")

    def test_set_keeps_the_trailing_comment_and_its_alignment(self, cfg):
        st.set_key("deploy_mode", "rhoai")
        line = next(
            ln
            for ln in (cfg / "acc-config.yaml").read_text(encoding="utf-8").splitlines()
            if ln.startswith("deploy_mode:")
        )
        assert line == "deploy_mode: rhoai   # standalone | rhoai", (
            "the comment and the whitespace that aligned it must both survive"
        )

    @pytest.mark.parametrize("newline", ["\n", "\r\n"])
    def test_line_endings_are_preserved(self, cfg, newline):
        target = cfg / "acc-config.yaml"
        target.write_bytes(ACC_CONFIG.replace("\n", newline).encode("utf-8"))

        st.set_key("deploy_mode", "rhoai")

        data = target.read_bytes()
        crlf = data.count(b"\r\n")
        bare_lf = data.count(b"\n") - crlf
        if newline == "\r\n":
            assert bare_lf == 0, "an LF crept into a CRLF file"
        else:
            assert crlf == 0, (
                "a CR crept into an LF file — the writer translated newlines, "
                "which makes every line read as changed"
            )

    def test_comments_are_never_dropped(self, cfg):
        before = (cfg / "acc-config.yaml").read_text(encoding="utf-8")
        st.set_key("agent.collective_id", "sol-02")
        after = (cfg / "acc-config.yaml").read_text(encoding="utf-8")
        for comment in ("# ACC Configuration", "# standalone | rhoai", "# ingester |"):
            assert comment in after, f"lost comment: {comment}"
        assert before.count("#") == after.count("#")


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


class TestUnresolvableReferencesAreRefused:
    def test_unknown_model_id_is_refused_and_names_the_role(self, cfg):
        with pytest.raises(st.ConfigError) as exc:
            st.set_key("role_models.compliance_officer", "no-such-model")
        message = str(exc.value)
        assert "compliance_officer" in message
        assert "no-such-model" in message
        assert "claude-haiku" in message, "the message should list what IS valid"

    def test_known_model_id_is_accepted(self, cfg):
        change = st.set_key("role_models.compliance_officer", "local-llama")
        assert change.after == "local-llama"

    def test_registry_field_is_model_id_not_id(self, cfg):
        """A guard that always passes is worse than no guard.

        ``models.yaml`` entries key on ``model_id``.  Reading ``id`` returns an
        empty set, and an empty set makes the reference check short-circuit —
        so every binding, valid or not, would be accepted.
        """
        assert st._known_model_ids() == {"claude-haiku", "local-llama"}

    def test_no_registry_does_not_block_a_first_run(self, cfg):
        """Binding roles before adding models is legitimate ordering."""
        (cfg / "models.yaml").write_text("role_models: {}\n", encoding="utf-8")
        st.validate_reference("role_models.analyst", "anything")  # must not raise


class TestSecretsAreNeverWritten:
    def test_env_file_is_not_writable(self, cfg):
        with pytest.raises(st.ConfigError, match="not writable"):
            st.set_key("env.ANTHROPIC_API_KEY", "sk-test")

    def test_secret_bearing_key_is_refused(self, cfg):
        with pytest.raises(st.ConfigError, match="secret-bearing"):
            st.set_key("working_memory.password", "placeholder-not-a-real-secret")

    def test_env_values_are_never_read_into_memory(self, cfg):
        (cfg / ".env").write_text("REDIS_PASSWORD=placeholder-not-a-real-secret\n", encoding="utf-8")
        data = st.read("env")
        assert data == {"REDIS_PASSWORD": True}, (
            "only presence may be reported; a surface that cannot obtain a "
            "value cannot leak one"
        )

    def test_env_var_name_keys_are_not_treated_as_secrets(self):
        """``llm.api_key_env`` holds a variable *name*, not a credential."""
        index = cs.by_path()
        assert not index["llm.api_key_env"].secret


class TestSchemaValidationOnWrite:
    def test_unknown_key_is_refused(self, cfg):
        with pytest.raises(st.ConfigError, match="unknown key"):
            st.set_key("llm.no_such_option", "x")

    def test_value_outside_the_enum_is_refused(self, cfg):
        with pytest.raises(st.ConfigError) as exc:
            st.set_key("llm.backend", "not_a_backend")
        assert "ollama" in str(exc.value), "list the permitted values"

    def test_non_integer_for_an_int_key_is_refused(self, cfg):
        with pytest.raises(st.ConfigError, match="not a valid int"):
            st.set_key("llm.request_timeout_s", "soon")

    def test_unset_of_an_absent_key_is_refused(self, cfg):
        with pytest.raises(st.ConfigError, match="nothing to unset"):
            st.unset_key("llm.max_retries")


# --------------------------------------------------------------------------
# Duplicate top-level keys
# --------------------------------------------------------------------------


class TestDuplicateDetection:
    def test_two_blocks_of_the_same_key_are_reported(self):
        """The failure that made marker-fencing necessary in the profile tooling.

        Two ``role_models:`` blocks is valid YAML; the last one wins and the
        first is discarded without a word.
        """
        text = textwrap.dedent(
            """\
            role_models:
              analyst: a

            models: []

            role_models:
              analyst: b
            """
        )
        assert st.duplicate_top_level_keys(text) == ["role_models"]

    def test_a_clean_file_reports_nothing(self):
        assert st.duplicate_top_level_keys(MODELS) == []

    def test_nested_repetition_is_not_a_duplicate(self):
        """The same child name under two parents is ordinary, not a fault."""
        text = textwrap.dedent(
            """\
            agent:
              role: a
            llm:
              role: b
            """
        )
        assert st.duplicate_top_level_keys(text) == []


# --------------------------------------------------------------------------
# check / migrate
# --------------------------------------------------------------------------


class TestCheck:
    def test_backend_without_its_credential_is_an_error(self, cfg):
        st.set_key("llm.backend", "anthropic")
        errors = [f for f in st.check() if f.level == "error"]
        assert any("ANTHROPIC_API_KEY" in f.message for f in errors), (
            "selecting a backend whose credential is absent must be reported "
            "before the first task fails"
        )

    def test_credential_present_clears_the_error(self, cfg, monkeypatch):
        st.set_key("llm.backend", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        errors = [f for f in st.check() if "ANTHROPIC_API_KEY" in f.message]
        assert not errors

    def test_sandbox_enabled_without_a_gateway_is_an_error(self, cfg):
        """The OpenShell version of backend-without-a-credential.

        With delegation switched on and no gateway, the agent believes its code
        execution is sandboxed while the target is absent — and nothing says so
        until a task actually tries to run code.
        """
        (cfg / ".env").write_text("ACC_SANDBOX_ENABLED=true\n", encoding="utf-8")
        errors = [f for f in st.check() if f.level == "error"]
        assert any("OPENSHELL_GATEWAY" in f.message for f in errors)

    def test_sandbox_enabled_with_a_gateway_is_clean(self, cfg):
        (cfg / ".env").write_text(
            "ACC_SANDBOX_ENABLED=true\nOPENSHELL_GATEWAY=https://openshell:8080\n",
            encoding="utf-8",
        )
        assert not [f for f in st.check() if "OPENSHELL_GATEWAY" in f.message]

    def test_sandbox_disabled_does_not_require_a_gateway(self, cfg):
        (cfg / ".env").write_text("ACC_SANDBOX_NAME=unused\n", encoding="utf-8")
        assert not [f for f in st.check() if "OPENSHELL_GATEWAY" in f.message]

    def test_role_bound_to_a_missing_model_is_an_error(self, cfg):
        (cfg / "models.yaml").write_text(
            "models:\n  - model_id: only-this\n    backend: ollama\n"
            "role_models:\n  analyst: vanished\n",
            encoding="utf-8",
        )
        errors = [f for f in st.check() if f.level == "error"]
        assert any("vanished" in f.message for f in errors)

    def test_namespace_containers_are_not_reported_as_unknown(self, cfg):
        """``agent`` is a namespace; only its leaves are schema keys."""
        unknown = [
            f for f in st.check() if f.level == "warning" and f.path in ("agent", "llm")
        ]
        assert not unknown, f"nested-model containers flagged as typos: {unknown}"

    def test_unset_options_are_reported_as_notes(self, cfg):
        notes = [f for f in st.check() if f.level == "note"]
        assert notes, "options this file has never seen should be discoverable"
        assert all(f.level == "note" for f in notes)


class TestMigrate:
    def test_adds_only_and_never_removes(self, cfg):
        target = cfg / "acc-config.yaml"
        before = target.read_text(encoding="utf-8")
        st.migrate("acc-config")
        after = target.read_text(encoding="utf-8")
        for line in before.splitlines():
            assert line in after.splitlines(), f"migrate removed: {line!r}"

    def test_existing_values_are_untouched(self, cfg):
        st.migrate("acc-config")
        data = yaml.safe_load((cfg / "acc-config.yaml").read_text(encoding="utf-8"))
        assert data["deploy_mode"] == "standalone"
        assert data["agent"]["collective_id"] == "sol-01"
        assert data["llm"]["backend"] == "ollama"

    def test_result_still_validates_against_the_model(self, cfg):
        from acc.config import ACCConfig

        st.migrate("acc-config")
        data = yaml.safe_load((cfg / "acc-config.yaml").read_text(encoding="utf-8"))
        ACCConfig.model_validate(data)

    def test_is_idempotent(self, cfg):
        target = cfg / "acc-config.yaml"
        st.migrate("acc-config")
        once = _bytes(target)
        assert st.migrate("acc-config") == []
        assert _bytes(target) == once

    def test_defaults_are_not_double_quoted(self, cfg):
        """A default round-tripped through the formatter twice becomes ``''''''``."""
        st.migrate("acc-config")
        data = yaml.safe_load((cfg / "acc-config.yaml").read_text(encoding="utf-8"))
        assert data["agent"]["hub_collective_id"] == ""

    def test_env_is_never_migrated(self, cfg):
        assert st.migrate("env") == []


# --------------------------------------------------------------------------
# Schema derivation
# --------------------------------------------------------------------------


class TestSchema:
    def test_is_derived_from_the_runtime_models(self):
        """The schema must not be a second, hand-maintained description.

        Every ``ACCConfig`` leaf should appear without anyone curating a list,
        which is what keeps it from drifting the first time a field is added.
        """
        index = cs.by_path()
        for dotted in ("llm.backend", "agent.role", "deploy_mode", "security.signing_mode"):
            assert dotted in index, f"{dotted} missing from the derived schema"

    def test_enum_choices_come_from_the_annotation(self):
        key = cs.by_path()["llm.backend"]
        assert set(key.choices) == {
            "ollama",
            "anthropic",
            "vllm",
            "llama_stack",
            "openai_compat",
        }

    def test_every_key_names_an_owning_file(self):
        for key in cs.schema():
            assert key.file, f"{key.path} has no owning file"
            assert cs.file_by_id(key.file)

    def test_operator_keyed_maps_resolve_to_their_container(self):
        """``role_models.<role>`` is data; the container carries the rules."""
        key = cs.find("role_models.anything_at_all")
        assert key is not None and key.path == "role_models"
        assert key.dynamic

    def test_json_schema_exports_every_file(self):
        exported = cs.json_schema()
        for spec in cs.FILES:
            assert spec.id in exported, f"{spec.id} missing from the JSON Schema export"

    def test_env_is_declared_read_only_and_secret_bearing(self):
        spec = cs.file_by_id("env")
        assert not spec.writable
        assert spec.secret_bearing


class TestOwnership:
    def test_get_reports_the_file_a_key_lives_in(self, cfg):
        assert st.get("llm.backend").file == "acc-config"
        assert st.get("role_models.compliance_officer").file == "models"

    def test_an_unset_key_falls_back_to_its_default(self, cfg):
        resolved = st.get("llm.max_retries")
        assert resolved.present is False
        assert resolved.value == 3
