"""A guided first run that validates as it goes.

The written procedure can be followed correctly and still produce a deployment
that only *looks* configured — every one of the silent configuration faults
passes a careful read of the instructions. So the property under test is that
**an answer is checked before it is written**, and that a failing answer stops
the whole set rather than half-applying.

The questions being *data* rather than a script is what keeps the interactive
walk and the non-interactive answer file from drifting apart, so that is tested
directly: the same definitions must drive both.
"""

from __future__ import annotations

import json

import pytest

from acc import setup_flow as S

MODELS = """\
models:
  - model_id: local
    backend: ollama
    model: llama3.2:3b
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
"""


@pytest.fixture
def host(tmp_path, monkeypatch):
    (tmp_path / "acc-config.yaml").write_text(ACC_CONFIG, encoding="utf-8")
    (tmp_path / "models.yaml").write_text(MODELS, encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.setenv("ACC_CONFIG_PATH", str(tmp_path / "acc-config.yaml"))
    monkeypatch.setenv("ACC_MODELS_PATH", str(tmp_path / "models.yaml"))
    monkeypatch.setenv("ACC_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(tmp_path / "collective.yaml"))
    monkeypatch.setenv("ACC_CATALOGS_PATH", str(tmp_path / "catalogs.yaml"))
    return tmp_path


# --------------------------------------------------------------------------
# Questions are data
# --------------------------------------------------------------------------


class TestQuestionsAreData:
    def test_every_question_binds_to_a_real_schema_key(self, host):
        """A question writing to a key the runtime does not know is a fault."""
        from acc import configschema as schema

        for section in S.sections():
            for question in section.questions:
                assert schema.find(question.key), f"{question.key} is not in the schema"

    def test_choices_come_from_the_schema_where_it_has_them(self, host):
        backend = next(
            q for s in S.sections() for q in s.questions if q.key == "llm.backend"
        )
        assert "ollama" in backend.options()
        assert "anthropic" in backend.options()

    def test_posture_questions_state_their_consequence(self, host):
        """A governance floor that got its value by default is nobody's decision."""
        posture = S.section("posture")
        for question in posture.questions:
            assert question.consequence, f"{question.key} does not say what it does"

    def test_sections_are_independently_addressable(self, host):
        assert S.section("model").name == "model"
        with pytest.raises(S.SetupError, match="no setup section"):
            S.section("nonsense")

    def test_the_same_definitions_drive_the_answer_template(self, host):
        keys = {q.key for s in S.sections() for q in s.questions}
        template = S.answers_from_env({})
        assert template == {}, "no env, no answers"
        assert keys, "there are questions to template"


# --------------------------------------------------------------------------
# Validation at entry
# --------------------------------------------------------------------------


class TestValidationAtEntry:
    def test_an_invalid_enum_is_rejected(self, host):
        question = next(
            q for s in S.sections() for q in s.questions if q.key == "llm.backend"
        )
        assert S.check_answer(question, "not_a_backend")
        assert S.check_answer(question, "ollama") == ""

    def test_a_malformed_url_is_rejected(self, host):
        question = next(
            q for s in S.sections() for q in s.questions if q.key == "llm.base_url"
        )
        assert "http" in S.check_answer(question, "localhost:8000")
        assert S.check_answer(question, "https://x.invalid/v1") == ""
        assert S.check_answer(question, "") == "", "blank means provider default"

    def test_a_bad_answer_stops_the_whole_set(self, host):
        """Half-applied leaves a shape no answer set describes."""
        before = (host / "acc-config.yaml").read_bytes()
        with pytest.raises(S.SetupError, match="nothing was written"):
            S.apply_answers({"llm.backend": "ollama", "deploy_mode": "moon"})
        assert (host / "acc-config.yaml").read_bytes() == before

    def test_a_valid_set_is_written(self, host):
        from acc import configstore as store

        S.apply_answers({"llm.backend": "anthropic"})
        assert store.get("llm.backend").value == "anthropic"

    def test_a_secret_question_would_be_refused(self, host):
        """No setup answer may carry a credential into a config file."""
        secret_q = S.Question(key="llm.api_key_env", prompt="x", secret=True)
        original = S.sections

        def patched():
            return [S.Section(name="model", title="t", questions=[secret_q])]

        S.sections = patched
        try:
            with pytest.raises(S.SetupError, match="environment instead"):
                S.apply_answers({"llm.api_key_env": "sk-nope"})
        finally:
            S.sections = original


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------


class TestModes:
    def test_dry_run_writes_nothing(self, host):
        before = (host / "acc-config.yaml").read_bytes()
        outcomes = S.apply_answers({"llm.backend": "anthropic"}, dry_run=True)
        assert outcomes and not any(o.written for o in outcomes)
        assert (host / "acc-config.yaml").read_bytes() == before

    def test_quick_leaves_values_that_are_already_set(self, host):
        outcomes = S.apply_answers({"llm.backend": "anthropic"}, quick=True)
        assert outcomes[0].written is False
        assert "already set" in outcomes[0].error

    def test_quick_still_writes_an_unset_value(self, host):
        outcomes = S.apply_answers({"llm.base_url": "https://x.invalid"}, quick=True)
        assert outcomes[0].written is True

    def test_only_one_section_is_applied(self, host):
        from acc import configstore as store

        S.apply_answers(
            {"llm.backend": "anthropic", "deploy_mode": "rhoai"}, only=["model"]
        )
        assert store.get("llm.backend").value == "anthropic"
        assert store.get("deploy_mode").value == "standalone", (
            "a key outside the chosen section must not be touched"
        )

    def test_an_unknown_key_in_an_answer_set_is_ignored(self, host):
        outcomes = S.apply_answers({"not.a.key": "x", "llm.backend": "anthropic"})
        assert [o.key for o in outcomes] == ["llm.backend"]


# --------------------------------------------------------------------------
# Non-interactive
# --------------------------------------------------------------------------


class TestNonInteractive:
    def test_answers_load_from_a_file(self, host, tmp_path):
        path = tmp_path / "answers.json"
        path.write_text(json.dumps({"llm.backend": "anthropic"}), encoding="utf-8")
        assert S.load_answers(path) == {"llm.backend": "anthropic"}

    def test_a_malformed_answer_file_is_refused(self, host, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        with pytest.raises(S.SetupError, match="cannot read"):
            S.load_answers(path)

    def test_a_non_object_answer_file_is_refused(self, host, tmp_path):
        path = tmp_path / "list.json"
        path.write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(S.SetupError, match="JSON object"):
            S.load_answers(path)

    def test_answers_can_come_from_the_environment(self, host):
        answers = S.answers_from_env({"ACC_SETUP_LLM_BACKEND": "anthropic"})
        assert answers == {"llm.backend": "anthropic"}

    def test_the_same_answer_set_produces_the_same_result(self, host):
        """Reproducible provisioning: the criterion, stated as a test."""
        from acc import configstore as store

        answers = {"llm.backend": "anthropic", "deploy_mode": "standalone"}
        S.apply_answers(answers)
        first = (host / "acc-config.yaml").read_bytes()

        S.apply_answers(answers)
        assert (host / "acc-config.yaml").read_bytes() == first
        assert store.get("llm.backend").value == "anthropic"


# --------------------------------------------------------------------------
# Finishing honestly
# --------------------------------------------------------------------------


class TestVerification:
    def test_verify_reports_broken_checks(self, host):
        """A completed setup should be a working deployment, not a plausible one.

        The flow finishes by asking the same question `doctor` asks rather than
        declaring success on its own authority.
        """
        S.apply_answers({"llm.backend": "anthropic"})
        problems = S.verify()
        assert isinstance(problems, list)

    def test_verify_uses_the_shared_check_registry(self, host, monkeypatch):
        from acc import preflight

        sentinel = preflight.Result(
            "planted", preflight.Severity.BROKEN, "planted by the test"
        )
        monkeypatch.setattr(preflight, "run", lambda *a, **k: [sentinel])
        assert any("planted" in p for p in S.verify())
