"""Every agent container must be able to READ models.yaml (B6, proposal 044).

Regression guard for a silent, high-blast-radius failure: ``role_models`` in
``models.yaml`` maps each role to a ``model_id``, but the lookup runs *inside
the agent container* (``acc.models.apply_role_model_env`` at boot).  If the
registry is not mounted there, :func:`acc.models.models_path` falls back to
``<repo>/models.yaml`` — a path that does not exist in the image —
``load_role_models()`` swallows the ``OSError`` and returns ``{}``, and EVERY
role silently drops to the global ``ACC_LLM_*`` default.

Nothing errors.  The TUI still renders the mapping (acc-tui *does* mount the
file), so the operator sees a configured registry while the agents never
receive it, and the Configuration pane's LIVE BACKENDS table shows every row
on the same model.  That split-brain shipped once; these tests stop it
recurring on both agent paths:

* the **baseline** services in ``container/production/podman-compose.yml``
  (identified by declaring ``ACC_AGENT_ROLE``), and
* the **synthesized** services ``acc.collective.roles_to_compose`` emits from
  ``collective.yaml``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from acc.collective import AgentSpec, CollectiveSpec, roles_to_compose

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "container" / "production" / "podman-compose.yml"

_MOUNT = "../../models.yaml:/app/models.yaml:ro,z"
_ENV_KEY = "ACC_MODELS_PATH"
_ENV_VAL = "/app/models.yaml"


def _agent_services() -> dict[str, dict]:
    """Baseline compose services that run an ACC agent (declare a role)."""
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return {
        name: svc
        for name, svc in (doc.get("services") or {}).items()
        if isinstance(svc, dict)
        and _ENV_KEY not in (name,)
        and "ACC_AGENT_ROLE" in (svc.get("environment") or {})
    }


class TestBaselineCompose:
    def test_there_are_agent_services_to_check(self):
        # Guards the guard: a selector that silently matches nothing would
        # make every assertion below vacuously pass.
        assert _agent_services(), "no ACC_AGENT_ROLE services found in compose"

    def test_every_agent_mounts_the_registry(self):
        missing = [
            name
            for name, svc in _agent_services().items()
            if _MOUNT not in (svc.get("volumes") or [])
        ]
        assert not missing, (
            f"agent services missing the models.yaml mount: {missing} — "
            "role_models cannot resolve in these containers"
        )

    def test_every_agent_points_at_the_mounted_registry(self):
        wrong = {
            name: (svc.get("environment") or {}).get(_ENV_KEY)
            for name, svc in _agent_services().items()
            if (svc.get("environment") or {}).get(_ENV_KEY) != _ENV_VAL
        }
        assert not wrong, f"agent services with a bad {_ENV_KEY}: {wrong}"


class TestRegistryWritability:
    """acc-tui is the only writer; everyone else is read-only.

    The Configuration pane's MODEL REGISTRY is a CRUD surface (Add / Edit /
    Delete / "Set default per role" → ``acc.models.upsert_model`` /
    ``delete_model`` / ``set_role_model``), all of which rewrite models.yaml.
    Mounted ``:ro`` they fail with ``[Errno 30] Read-only file system`` while
    the pane still claims "Edits save to models.yaml".  Conversely no agent
    and not acc-webgui should ever be able to rewrite the registry.
    """

    def _mount(self, service: str) -> str:
        doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        mounts = [
            v for v in (doc["services"][service].get("volumes") or [])
            if isinstance(v, str) and ":/app/models.yaml" in v
        ]
        assert len(mounts) == 1, f"{service}: expected 1 models.yaml mount, got {mounts}"
        return mounts[0]

    def test_tui_mounts_registry_read_write(self):
        mount = self._mount("acc-tui")
        assert ":ro" not in mount, (
            f"acc-tui mounts the registry read-only ({mount}) — the MODEL "
            "REGISTRY CRUD surface will fail with Errno 30"
        )

    def test_webgui_mounts_registry_read_only(self):
        assert ":ro" in self._mount("acc-webgui")

    def test_agents_mount_registry_read_only(self):
        writable = [
            name for name in _agent_services()
            if ":ro" not in self._mount(name)
        ]
        assert not writable, f"agents must not be able to rewrite the registry: {writable}"


class TestSynthesizedOverlay:
    def _svc(self) -> dict:
        spec = CollectiveSpec(
            collective_id="sol-01",
            agents=[AgentSpec(role="research_critic", replicas=1)],
        )
        overlay = roles_to_compose(spec)
        services = overlay["services"]
        assert len(services) == 1, services
        return next(iter(services.values()))

    def test_synthesized_agent_mounts_the_registry(self):
        assert _MOUNT in self._svc()["volumes"]

    def test_synthesized_agent_points_at_the_mounted_registry(self):
        assert self._svc()["environment"][_ENV_KEY] == _ENV_VAL

class TestSignedCatalogReachable:
    """Agents must be able to resolve PROPOSE_INFUSE against a signed catalog.

    ``acc.pkg.catalog`` searches ``/etc/acc/catalogs.yaml`` (override:
    ``ACC_SYSTEM_CATALOG``), ``~/.acc/catalogs.yaml`` and
    ``<cwd>/.acc/catalogs.yaml``.  A ``catalogs.yaml`` at the repo root is NOT
    read on its own — so mounting it is only half the wiring, and without the
    env override infusion fails with "no catalogs are configured" (observed
    live) even though the file is right there in the container.
    """

    def test_every_agent_mounts_the_catalog(self):
        missing = [
            name for name, svc in _agent_services().items()
            if not any(
                isinstance(v, str) and ":/app/catalogs.yaml" in v
                for v in (svc.get("volumes") or [])
            )
        ]
        assert not missing, f"agents missing the catalogs.yaml mount: {missing}"

    def test_every_agent_points_at_the_mounted_catalog(self):
        wrong = {
            name: (svc.get("environment") or {}).get("ACC_SYSTEM_CATALOG")
            for name, svc in _agent_services().items()
            if (svc.get("environment") or {}).get("ACC_SYSTEM_CATALOG")
            != "/app/catalogs.yaml"
        }
        assert not wrong, f"agents with a bad ACC_SYSTEM_CATALOG: {wrong}"

    def test_template_signer_is_anchored_to_the_publishing_repo(self):
        """An unanchored pattern would trust ANY GitHub Actions signature."""
        import re

        import yaml as _yaml
        tmpl = REPO_ROOT / "catalogs.yaml.example"
        cat = _yaml.safe_load(tmpl.read_text(encoding="utf-8"))["catalogs"][0]
        signer = cat["required_signer"]
        assert signer["issuer"] == "https://token.actions.githubusercontent.com"
        pat = signer["subject_pattern"]
        real = ("https://github.com/flg77/acc-ecosystem/.github/workflows/"
                "publish-family-packs.yml@refs/tags/v1.2.4")
        impostor = ("https://github.com/evil/acc-ecosystem/.github/workflows/"
                    "x.yml@refs/tags/v1")
        assert re.search(pat, real), "pattern rejects the real signing identity"
        assert not re.search(pat, impostor), (
            f"pattern {pat!r} accepts an impostor repo — it must be anchored"
        )
