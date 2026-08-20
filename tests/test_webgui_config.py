"""Configuration through the web interface, and the line it must not cross.

The governance rule is the feature: **posture is not writable from a browser.**
The security floor, the deploy mode, whether compliance evaluation runs — a
control that can be edited from a browser session is not a control, so
``/set`` refuses those keys outright and ``/propose`` turns one into an
oversight item instead.

Two other properties are tested because both fail silently:

* the schema drives the choices, so the browser cannot offer a value the
  runtime will reject;
* a secret is never returned and never writable through this surface.

And the authorisation split: a viewer reads, an operator writes.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from acc.webgui import routes_config  # noqa: E402
from acc.webgui.auth import Principal  # noqa: E402

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
llm:
  backend: ollama
  ollama_model: llama3.2:3b
working_memory:
  password: "planted-secret-value"
"""


def _app(role: str) -> TestClient:
    """An app whose auth always resolves to *role*."""
    from acc.webgui import auth

    app = FastAPI()
    principal = Principal(user=f"{role}-user", role=role)

    def _as_principal():
        return principal

    def _operator_only():
        if role != "operator":
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="operator role required")
        return principal

    app.dependency_overrides[auth.require_viewer] = _as_principal
    app.dependency_overrides[auth.require_operator] = _operator_only
    app.include_router(routes_config.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def site(tmp_path, monkeypatch):
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
# Posture cannot be written from a browser
# --------------------------------------------------------------------------


class TestPostureIsNotWritableHere:
    def test_setting_a_posture_key_is_refused(self, site):
        """A governance control editable from a browser session is not a control."""
        response = _app("operator").post(
            "/api/config/set", json={"key": "operator_mode", "value": "dev"}
        )
        assert response.status_code == 403
        assert "oversight" in response.json()["detail"]

    def test_the_refusal_does_not_write(self, site):
        from acc import configstore as store

        _app("operator").post(
            "/api/config/set", json={"key": "operator_mode", "value": "dev"}
        )
        assert store.get("operator_mode").value == "prod"

    def test_deploy_mode_is_posture(self, site):
        response = _app("operator").post(
            "/api/config/set", json={"key": "deploy_mode", "value": "rhoai"}
        )
        assert response.status_code == 403

    def test_propose_returns_an_oversight_item_and_writes_nothing(self, site):
        from acc import configstore as store

        response = _app("operator").post(
            "/api/config/propose",
            json={"key": "operator_mode", "value": "dev", "rationale": "local demo"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["risk_level"] == "HIGH"
        assert body["params"]["current"] == "prod"
        assert "Not applied" in body["note"]
        assert store.get("operator_mode").value == "prod"

    def test_propose_refuses_a_non_posture_key(self, site):
        response = _app("operator").post(
            "/api/config/propose", json={"key": "llm.backend", "value": "anthropic"}
        )
        assert response.status_code == 400
        assert "use /api/config/set" in response.json()["detail"]

    def test_propose_still_validates_the_value(self, site):
        response = _app("operator").post(
            "/api/config/propose", json={"key": "operator_mode", "value": "nonsense"}
        )
        assert response.status_code == 400

    def test_the_posture_set_is_not_restated_here(self):
        """Two lists of 'what counts as posture' would eventually disagree."""
        from acc.profiles import POSTURE_KEYS

        assert routes_config._posture_keys() is POSTURE_KEYS


# --------------------------------------------------------------------------
# Ordinary configuration
# --------------------------------------------------------------------------


class TestOrdinaryConfiguration:
    def test_an_operator_can_change_a_model_binding(self, site):
        response = _app("operator").post(
            "/api/config/set", json={"key": "llm.backend", "value": "anthropic"}
        )
        assert response.status_code == 200
        assert response.json()["after"] == "anthropic"

    def test_an_invalid_value_cannot_be_saved(self, site):
        """The schema is the same one the runtime validates against."""
        response = _app("operator").post(
            "/api/config/set", json={"key": "llm.backend", "value": "not_a_backend"}
        )
        assert response.status_code == 400
        assert "ollama" in response.json()["detail"]

    def test_an_unresolvable_reference_cannot_be_saved(self, site):
        response = _app("operator").post(
            "/api/config/set",
            json={"key": "role_models.analyst", "value": "no-such-model"},
        )
        assert response.status_code == 400
        assert "analyst" in response.json()["detail"]

    def test_preview_shows_the_change_without_making_it(self, site):
        from acc import configstore as store

        response = _app("operator").post(
            "/api/config/preview", json={"key": "llm.backend", "value": "anthropic"}
        )
        assert response.status_code == 200
        assert response.json()["before"] == "ollama"
        assert store.get("llm.backend").value == "ollama"

    def test_a_change_is_attributed(self, site):
        body = _app("operator").post(
            "/api/config/set", json={"key": "llm.backend", "value": "anthropic"}
        ).json()
        assert body["changed_by"] == "operator-user"
        assert body["at"] > 0

    def test_the_restart_requirement_is_stated(self, site):
        body = _app("operator").post(
            "/api/config/set", json={"key": "llm.backend", "value": "anthropic"}
        ).json()
        assert "restart" in body["note"]


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


class TestReading:
    def test_a_viewer_can_read_the_surface(self, site):
        response = _app("viewer").get("/api/config")
        assert response.status_code == 200
        assert response.json()["entries"]

    def test_choices_come_from_the_schema(self, site):
        entries = {e["key"]: e for e in _app("viewer").get("/api/config").json()["entries"]}
        assert "ollama" in entries["llm.backend"]["choices"]

    def test_posture_entries_are_marked_and_not_writable(self, site):
        entries = {e["key"]: e for e in _app("viewer").get("/api/config").json()["entries"]}
        assert entries["operator_mode"]["posture"] is True
        assert entries["operator_mode"]["writable"] is False

    def test_a_secret_value_is_never_returned(self, site):
        body = _app("viewer").get("/api/config").text
        assert "planted-secret-value" not in body

    def test_a_secret_is_reported_as_set_or_unset(self, site):
        entries = {e["key"]: e for e in _app("viewer").get("/api/config").json()["entries"]}
        assert entries["working_memory.password"]["value"] in ("<set>", "<unset>")
        assert entries["working_memory.password"]["writable"] is False

    def test_env_keys_are_absent_from_the_surface(self, site):
        keys = {e["key"] for e in _app("viewer").get("/api/config").json()["entries"]}
        assert not any(k.startswith("env.") for k in keys)

    def test_role_bindings_offer_only_known_models(self, site):
        body = _app("viewer").get("/api/config/roles").json()
        assert {m["model_id"] for m in body["available"]} == {"local", "cloud"}
        assert body["bindings"]["analyst"] == ["local"]


# --------------------------------------------------------------------------
# Authorisation
# --------------------------------------------------------------------------


class TestAuthorisation:
    def test_a_viewer_cannot_write(self, site):
        response = _app("viewer").post(
            "/api/config/set", json={"key": "llm.backend", "value": "anthropic"}
        )
        assert response.status_code == 403

    def test_a_viewer_cannot_preview_either(self, site):
        """Preview reports current values; it is a write-path endpoint."""
        assert _app("viewer").post(
            "/api/config/preview", json={"key": "llm.backend", "value": "anthropic"}
        ).status_code == 403

    def test_a_viewer_cannot_propose(self, site):
        assert _app("viewer").post(
            "/api/config/propose", json={"key": "operator_mode", "value": "dev"}
        ).status_code == 403
