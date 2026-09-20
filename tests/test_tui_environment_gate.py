"""The TUI knows where it runs (OpenSpec ``20260920-surfaces-detect-environment``).

In a cluster pod every control that changes the deployment is disabled with
the reason, the Agentset tab says what the agentset is there, and the LLM
*Save* tells the agents even though nothing can be written.  In a checkout
nothing is touched.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Button, Static, TextArea

from acc import deploy
from acc.tui.screens.catalogs import CatalogsScreen
from acc.tui.screens.configuration import ConfigurationScreen
from acc.tui.screens.ecosystem import EcosystemScreen
from acc.tui.widgets.nav_bar import NavigationBar


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """An empty working directory and empty manifest roots — the screens must
    mount on nothing, which is what a pod gives them."""
    for var, name in (("ACC_ROLES_ROOT", "roles"), ("ACC_SKILLS_ROOT", "skills"),
                      ("ACC_MCPS_ROOT", "mcps"), ("ACC_PACKAGES_ROOT", "packages")):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(var, str(tmp_path / name))
    monkeypatch.delenv("ACC_COLLECTIVE_PATH", raising=False)
    monkeypatch.setenv("ACC_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def cluster(workdir, monkeypatch):
    monkeypatch.setenv("ACC_DEPLOY_MODE", "rhoai")
    monkeypatch.setenv("ACC_CORPUS_NAME", "mortgage-agents-corpus")
    deploy._reset()


def _host(screen_cls):
    class _Harness(App):
        def on_mount(self) -> None:
            self.push_screen(screen_cls())

    return _Harness()


# ---------------------------------------------------------------------------
# where am I — on every screen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nav_bar_names_the_cluster(cluster):
    app = _host(EcosystemScreen)
    async with app.run_test() as pilot:
        await pilot.pause()
        nav = app.screen.query_one(NavigationBar)
        assert nav.border_subtitle == "cluster · mortgage-agents-corpus"


@pytest.mark.asyncio
async def test_nav_bar_names_a_checkout(workdir):
    app = _host(EcosystemScreen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.query_one(NavigationBar).border_subtitle == "standalone"


# ---------------------------------------------------------------------------
# Ecosystem → Agentset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agentset_in_a_cluster_names_the_agentcollective(cluster):
    app = _host(EcosystemScreen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        text = screen.query_one("#collective-editor", TextArea).text
        assert "AgentCollective" in text
        assert "acc-deploy.sh" not in text          # no advice a pod cannot follow
        assert screen.query_one("#collective-editor", TextArea).read_only
        for btn_id in ("#btn-collective-save", "#btn-collective-apply",
                       "#btn-agentset-set-model"):
            btn = screen.query_one(btn_id, Button)
            assert btn.disabled, btn_id
            assert "AgentCollective" in str(btn.tooltip)
        # Validate reads nothing and writes nothing — it stays.
        assert not screen.query_one("#btn-collective-validate", Button).disabled


@pytest.mark.asyncio
async def test_agentset_in_a_checkout_is_untouched(workdir):
    app = _host(EcosystemScreen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert "acc-deploy.sh setup" in screen.query_one("#collective-editor", TextArea).text
        assert not screen.query_one("#btn-collective-save", Button).disabled
        assert not screen.query_one("#btn-collective-apply", Button).disabled


@pytest.mark.asyncio
async def test_arming_a_role_does_not_undo_the_gate(cluster):
    app = _host(EcosystemScreen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen._arm_infusion_button("assistant")     # what selecting a role does
        for btn_id in ("#btn-save-yaml", "#btn-edit-yaml", "#btn-roll-release"):
            assert screen.query_one(btn_id, Button).disabled, btn_id


# ---------------------------------------------------------------------------
# Configuration → LLM Endpoints
# ---------------------------------------------------------------------------


class _Client:
    collective_id = "mortgage-agents"

    def __init__(self):
        self.reloads = []

    def publish_config_reload(self, changes):
        self.reloads.append(changes)


@pytest.mark.asyncio
async def test_llm_save_in_a_cluster_tells_the_agents_and_says_it_is_not_saved(cluster, workdir):
    app = _host(ConfigurationScreen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        client = _Client()
        screen._get_observer_client = lambda: client
        screen._on_save_llm_config()
        await pilot.pause()
        assert len(client.reloads) == 1                       # the agents were told
        assert not (workdir / ".env").exists()                # nothing was written
        shown = str(screen.query_one("#llm-save-result", Static).render())
        assert "reload broadcast" in shown and "not saved" in shown
        for btn_id in ("#btn-model-add", "#btn-rolemodel-assign", "#btn-upload-skill"):
            assert screen.query_one(btn_id, Button).disabled, btn_id


@pytest.mark.asyncio
async def test_llm_save_broadcasts_even_when_the_file_cannot_be_written(workdir, monkeypatch):
    """The bug that made Save a no-op wherever ``.env`` is read-only: the
    failed write returned before the broadcast."""
    import acc.tui.env_writeback as wb

    def _boom(path, updates):
        raise PermissionError("read-only")

    monkeypatch.setattr(wb, "upsert_env", _boom)
    app = _host(ConfigurationScreen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        client = _Client()
        screen._get_observer_client = lambda: client
        screen._on_save_llm_config()
        await pilot.pause()
        assert len(client.reloads) == 1
        shown = str(screen.query_one("#llm-save-result", Static).render())
        assert "Not saved" in shown and "reload broadcast" in shown


# ---------------------------------------------------------------------------
# Catalogs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalogs_form_is_disabled_in_a_cluster(cluster):
    app = _host(CatalogsScreen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.query_one("#catalogs-form-collapsible").disabled
        assert "AccCatalog" in str(screen.query_one("#catalogs-status", Static).render())


# ---------------------------------------------------------------------------
# the words
# ---------------------------------------------------------------------------


def test_worker_pool_hint_names_the_right_place(monkeypatch):
    from acc.tui import outcomes

    assert "acc-deploy.sh" in outcomes.worker_pool_hint()
    monkeypatch.setenv("ACC_CORPUS_NAME", "demo")
    deploy._reset()
    hint = outcomes.worker_pool_hint()
    assert "AgentCollective" in hint and "acc-deploy.sh" not in hint


def test_infuse_writes_where_the_operator_pointed(tmp_path, monkeypatch):
    """An explicit ACC_ROLES_ROOT is a write target taken at its word — also
    when the directory does not exist yet.  The shared resolver would fall
    back to the repo's roles/ there, and Apply would write into the checkout."""
    from acc.tui.screens import infuse

    monkeypatch.setenv("ACC_ROLES_ROOT", str(tmp_path / "not-there-yet"))
    assert infuse._roles_root() == str(tmp_path / "not-there-yet")


def test_infuse_without_the_variable_uses_the_shared_resolver(tmp_path, monkeypatch):
    from acc.tui.path_resolution import resolve_manifest_root
    from acc.tui.screens import infuse

    monkeypatch.delenv("ACC_ROLES_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)               # not "./roles" of wherever we are
    assert infuse._roles_root() == str(resolve_manifest_root("ACC_ROLES_ROOT", "roles"))
    assert infuse._roles_root() != "roles"


@pytest.mark.asyncio
async def test_llm_endpoints_leads_with_what_runs(workdir):
    """The operator asked twice: the overview of the running agents and their
    models first, the configured-backend summary and the form after — in a pod
    those are empty, and an overview below the fold is not one."""
    app = _host(ConfigurationScreen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        ids = [w.id for w in screen.query("#llm-live-table, #llm-config-summary, #llm-edit-form")]
        assert ids == ["llm-live-table", "llm-config-summary", "llm-edit-form"]
