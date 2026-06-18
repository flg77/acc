"""033 WS-E regression tests — Ecosystem role caps subview + roll-a-release.

Two surfaces land on the Ecosystem screen's Roles tab in WS-E:

Part 1 — a per-role "Skills & MCPs (per-role)" collapsible whose
``#role-caps-table`` lists the selected role's ``allowed_skills`` +
``allowed_mcps``, each annotated with whether it is installed on this
deploy (allowed∩installed via
``acc.capability_index.get_allowed_installed_capabilities``) and its
provenance (core baseline vs pack).

Part 2 — a warning-variant "Roll a release" button (``#btn-roll-release``)
armed only when a role is selected.  Its worker validates the role
(WS-A ``validate_roles_dir``), builds a single-role ``.accpkg`` (reusing
``tools/build_family_pkg``), signs it (``acc.pkg.publish.sign_blob``), and
renders a USER-GATED publish gate (``#roll-release-gate``) showing the
tarball / signature / cert paths, the signer identity and the exact
``acc-pkg publish`` command.  The TUI never pushes to a registry.

Build + sign are mocked so the suite is hermetic (no cosign / network /
real role tree required).  Harness shape mirrors
``tests/test_ecosystem_screen_pilot.py``.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, DataTable, Static

from acc.tui.messages import RolePreloadMessage
from acc.tui.screens.ecosystem import EcosystemScreen


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_role_with_caps(
    roles_root: Path,
    role_name: str,
    *,
    allowed_skills: list[str],
    allowed_mcps: list[str],
) -> None:
    """Drop a role.yaml carrying explicit allowed_skills / allowed_mcps."""
    role_dir = roles_root / role_name
    role_dir.mkdir(parents=True, exist_ok=True)
    skills_yaml = "".join(f"    - '{s}'\n" for s in allowed_skills) or "    []\n"
    mcps_yaml = "".join(f"    - '{m}'\n" for m in allowed_mcps) or "    []\n"
    # When non-empty, render the list under the key on following lines;
    # when empty, render an inline empty list.
    if allowed_skills:
        skills_block = "  allowed_skills:\n" + "".join(
            f"    - '{s}'\n" for s in allowed_skills
        )
    else:
        skills_block = "  allowed_skills: []\n"
    if allowed_mcps:
        mcps_block = "  allowed_mcps:\n" + "".join(
            f"    - '{m}'\n" for m in allowed_mcps
        )
    else:
        mcps_block = "  allowed_mcps: []\n"
    (role_dir / "role.yaml").write_text(
        "role_definition:\n"
        f"  purpose: 'caps fixture for {role_name}'\n"
        "  persona: 'concise'\n"
        "  task_types: ['pilot_test']\n"
        "  domain_id: 'pilot_domain'\n"
        "  version: '0.1.0'\n"
        + skills_block
        + mcps_block,
        encoding="utf-8",
    )


@pytest.fixture
def caps_manifests(tmp_path, monkeypatch):
    """Isolated skills/mcps/roles/packages roots with one caps-bearing role.

    ``test_caps_role`` allows two skills (one core-baseline ``fs_read``,
    one pack ``code_search``) and two MCPs (one core ``arxiv``, one pack
    ``web_fetch``).  The skills/mcps roots get manifests ONLY for the
    pack caps so the allowed∩installed intersection is meaningful (the
    core caps resolve via the CORE_BASELINE floor, not the on-disk
    registry).
    """
    skills_root = tmp_path / "skills"
    mcps_root = tmp_path / "mcps"
    roles_root = tmp_path / "roles"
    packages_root = tmp_path / "packages"
    for d in (skills_root, mcps_root, roles_root, packages_root):
        d.mkdir()

    # Install one pack skill manifest so `code_search` is "installed".
    skill_dir = skills_root / "code_search"
    skill_dir.mkdir()
    (skill_dir / "skill.yaml").write_text(
        "purpose: 'pilot'\n"
        "version: '0.1.0'\n"
        "adapter_module: 'adapter'\n"
        "adapter_class: 'CodeSearchSkill'\n"
        "input_schema: {}\n"
        "output_schema: {}\n"
        "risk_level: 'LOW'\n",
        encoding="utf-8",
    )
    (skill_dir / "adapter.py").write_text(
        "from acc.skills import Skill\n"
        "class CodeSearchSkill(Skill):\n"
        "    async def invoke(self, args):\n"
        "        return {}\n",
        encoding="utf-8",
    )

    # Install one pack MCP manifest so `web_fetch` is "installed".
    mcp_dir = mcps_root / "web_fetch"
    mcp_dir.mkdir()
    (mcp_dir / "mcp.yaml").write_text(
        "purpose: 'pilot'\n"
        "version: '0.1.0'\n"
        "transport: 'http'\n"
        "url: 'http://acc-mcp-web:8080/rpc'\n"
        "allowed_tools: ['fetch']\n"
        "risk_level: 'LOW'\n",
        encoding="utf-8",
    )

    _write_role_with_caps(
        roles_root,
        "test_caps_role",
        allowed_skills=["fs_read", "code_search"],
        allowed_mcps=["arxiv", "web_fetch"],
    )

    monkeypatch.setenv("ACC_SKILLS_ROOT", str(skills_root))
    monkeypatch.setenv("ACC_MCPS_ROOT", str(mcps_root))
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(packages_root))
    return {
        "skills_root": skills_root,
        "mcps_root": mcps_root,
        "roles_root": roles_root,
        "packages_root": packages_root,
    }


class _Harness(App):
    """Minimal app — hosts EcosystemScreen + captures messages."""

    def __init__(self) -> None:
        super().__init__()
        self.captured: list[RolePreloadMessage] = []

    def on_mount(self) -> None:
        self.push_screen(EcosystemScreen())

    def on_role_preload_message(self, message: RolePreloadMessage) -> None:
        self.captured.append(message)


# ---------------------------------------------------------------------------
# Part 1 — per-role capabilities subview
# ---------------------------------------------------------------------------


def _caps_rows(table: DataTable) -> list[tuple[str, ...]]:
    """Snapshot every row of the caps table as a tuple of cell strings."""
    out: list[tuple[str, ...]] = []
    for row_key in table.rows.keys():
        cells = table.get_row(row_key)
        out.append(tuple(str(c) for c in cells))
    return out


@pytest.mark.asyncio
async def test_caps_subview_lists_allowed_caps_with_source(caps_manifests):
    """Part 1: selecting a role renders its allowed skills + MCPs with a
    Source (core/pack) column and an installed (✓/✗) marker."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        # On-mount auto-selects the (only) role; force a deterministic
        # render against it.
        screen._load_role_capabilities("test_caps_role")
        await pilot.pause()

        table = screen.query_one("#role-caps-table", DataTable)
        rows = _caps_rows(table)
        # One row per allowed cap: 2 skills + 2 mcps.
        caps = {r[0]: r for r in rows}
        assert "fs_read" in caps, rows
        assert "code_search" in caps, rows
        assert "arxiv" in caps, rows
        assert "web_fetch" in caps, rows

        # Kind column.
        assert caps["fs_read"][1] == "skill"
        assert caps["web_fetch"][1] == "mcp"

        # Source column — fs_read + arxiv are core baseline; the pack
        # caps are "pack".
        assert caps["fs_read"][3] == "core", caps["fs_read"]
        assert caps["arxiv"][3] == "core", caps["arxiv"]
        assert caps["code_search"][3] == "pack", caps["code_search"]
        assert caps["web_fetch"][3] == "pack", caps["web_fetch"]


@pytest.mark.asyncio
async def test_caps_subview_marks_installed_pack_caps(caps_manifests):
    """Part 1: the pack caps with on-disk manifests show installed=✓
    (allowed∩installed), proving the WS-G helper is wired in."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen._load_role_capabilities("test_caps_role")
        await pilot.pause()

        table = screen.query_one("#role-caps-table", DataTable)
        caps = {r[0]: r for r in _caps_rows(table)}
        # code_search + web_fetch have manifests in the isolated roots →
        # installed.  Installed marker lives in column 2.
        assert caps["code_search"][2] == "✓", caps["code_search"]
        assert caps["web_fetch"][2] == "✓", caps["web_fetch"]


@pytest.mark.asyncio
async def test_caps_subview_called_from_show_role_detail(caps_manifests):
    """Part 1: ``_show_role_detail`` drives the caps table (wired through
    the normal selection path, not just the direct helper call)."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        # Clear, then exercise the public detail-render entrypoint.
        screen.query_one("#role-caps-table", DataTable).clear()
        screen._show_role_detail("test_caps_role")
        await pilot.pause()

        table = screen.query_one("#role-caps-table", DataTable)
        names = {r[0] for r in _caps_rows(table)}
        assert {"fs_read", "code_search", "arxiv", "web_fetch"} <= names, names


# ---------------------------------------------------------------------------
# Part 2 — roll-a-release
# ---------------------------------------------------------------------------


def _install_fake_validator(monkeypatch, *, findings=None, errors=False):
    """Inject a fake ``acc.capability_validator`` module into sys.modules.

    The real module isn't present on this branch (it's a WS-A deliverable
    on a sibling lineage); the roll worker imports it defensively.  We
    provide a stand-in so the validate step runs deterministically.
    """
    mod = types.ModuleType("acc.capability_validator")
    mod.validate_roles_dir = lambda *a, **k: (findings or [])  # type: ignore[attr-defined]
    mod.has_errors = lambda f: bool(errors)  # type: ignore[attr-defined]
    mod.format_findings = lambda f: "\n".join(str(x) for x in (f or [])) or "no findings"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "acc.capability_validator", mod)
    return mod


@pytest.fixture
def roll_manifests(tmp_path, monkeypatch):
    """Isolated roots with one role + empty package root (no pack bleed)."""
    skills_root = tmp_path / "skills"
    mcps_root = tmp_path / "mcps"
    roles_root = tmp_path / "roles"
    packages_root = tmp_path / "packages"
    for d in (skills_root, mcps_root, roles_root, packages_root):
        d.mkdir()
    _write_role_with_caps(
        roles_root, "test_roll_role", allowed_skills=[], allowed_mcps=[],
    )
    monkeypatch.setenv("ACC_SKILLS_ROOT", str(skills_root))
    monkeypatch.setenv("ACC_MCPS_ROOT", str(mcps_root))
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(packages_root))
    return {"roles_root": roles_root, "tmp_path": tmp_path}


def _mock_build_and_sign(monkeypatch, tmp_path):
    """Mock the build chain + cosign signing so the roll is hermetic.

    Returns the fake artefact paths the publish gate should surface.
    """
    import acc.pkg.publish as publish_mod
    import tools.build_family_pkg as bfp

    tarball = tmp_path / "dist" / "acc-test_roll_role-role-0.1.0.accpkg"
    tarball.parent.mkdir(parents=True, exist_ok=True)
    tarball.write_bytes(b"fake-tarball-bytes")

    sig = Path(str(tarball) + ".sig")
    cert = Path(str(tarball) + ".pem")
    sig.write_text("fake-sig", encoding="utf-8")
    cert.write_text("fake-cert", encoding="utf-8")

    # Mock build_family (the manifest-synthesis + acc.pkg.build.build
    # chain) to skip the real role-tree copy + tarball write.
    def fake_build_family(name, **kwargs):
        return tarball

    monkeypatch.setattr(bfp, "build_family", fake_build_family)

    # Mock the cosign-backed sign_blob + the OIDC token resolver.
    artefacts = publish_mod.SignArtefacts(
        signature_path=sig,
        certificate_path=cert,
        rekor_log_index=4242,
    )
    monkeypatch.setattr(
        publish_mod, "sign_blob", lambda *a, **k: artefacts,
    )
    monkeypatch.setattr(
        publish_mod, "resolve_oidc_token",
        lambda: "fake.jwt.token",
    )
    return {"tarball": tarball, "sig": sig, "cert": cert}


@pytest.mark.asyncio
async def test_roll_release_disabled_until_role_selected(roll_manifests, monkeypatch):
    """Part 2: with NO roles loaded, the Roll-a-release button stays
    disabled (the arm logic only fires on a selection)."""
    # Point ACC_ROLES_ROOT at an empty dir so on_mount has nothing to
    # auto-select.  Empty package root too (already set).
    empty_roles = roll_manifests["tmp_path"] / "empty_roles"
    empty_roles.mkdir()
    monkeypatch.setenv("ACC_ROLES_ROOT", str(empty_roles))

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        # No role auto-selected → button disabled, gate hidden.
        assert screen._selected_role == ""
        assert screen.query_one("#btn-roll-release", Button).disabled is True
        assert screen.query_one("#roll-release-gate", Static).display is False


@pytest.mark.asyncio
async def test_roll_release_armed_on_selection(roll_manifests):
    """Part 2: selecting a role arms the Roll-a-release button."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        # The fixture's single role auto-selects on mount.
        assert screen._selected_role == "test_roll_role"
        assert screen.query_one("#btn-roll-release", Button).disabled is False


@pytest.mark.asyncio
async def test_roll_release_validates_builds_signs_shows_gate(
    roll_manifests, monkeypatch,
):
    """Part 2 happy path: roll validates (mock), builds + signs (mock),
    then reveals the publish gate with the artefact paths, the signer
    identity, and the exact ``acc-pkg publish`` command."""
    _install_fake_validator(monkeypatch, findings=[], errors=False)
    paths = _mock_build_and_sign(monkeypatch, roll_manifests["tmp_path"])

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen._selected_role = "test_roll_role"

        screen._handle_roll_release()
        # Let the worker run to completion.
        await app.workers.wait_for_complete()
        await pilot.pause()

        gate = screen.query_one("#roll-release-gate", Static)
        assert gate.display is True
        body = str(gate.render())
        # Artefact paths surfaced.
        assert str(paths["tarball"]) in body, body
        assert str(paths["sig"]) in body, body
        assert str(paths["cert"]) in body, body
        # Signer identity (pre-fetched token → SIGSTORE_ID_TOKEN label).
        assert "SIGSTORE_ID_TOKEN" in body, body
        # Rekor index surfaced.
        assert "4242" in body, body
        # The exact publish command is printed (USER-GATED push).
        assert "acc-pkg publish" in body, body
        assert str(paths["tarball"]) in body


@pytest.mark.asyncio
async def test_roll_release_stops_on_validation_failure(
    roll_manifests, monkeypatch,
):
    """Part 2: a validation ERROR aborts the roll BEFORE build/sign and
    surfaces the findings; the publish gate is never shown."""
    finding = "[ERROR] role:test_roll_role: skill 'ghost' unresolved"
    _install_fake_validator(monkeypatch, findings=[finding], errors=True)

    # Build + sign must NOT be called; wire them to explode if they are.
    import acc.pkg.publish as publish_mod
    import tools.build_family_pkg as bfp

    def _boom(*a, **k):
        raise AssertionError("build/sign must not run after validation fail")

    monkeypatch.setattr(bfp, "build_family", _boom)
    monkeypatch.setattr(publish_mod, "sign_blob", _boom)

    captured: list[tuple[str, str]] = []

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen._selected_role = "test_roll_role"

        def fake_notify(message, *, severity="information", timeout=4.0, **kw):
            captured.append((str(message), severity))

        monkeypatch.setattr(screen, "notify", fake_notify)

        screen._handle_roll_release()
        await app.workers.wait_for_complete()
        await pilot.pause()

        # Gate stayed hidden.
        assert screen.query_one("#roll-release-gate", Static).display is False
        # An error notification carried the findings.
        errors = [m for m, sev in captured if sev == "error"]
        assert any("Validation FAILED" in m for m in errors), captured
        assert any("ghost" in m for m in errors), captured


@pytest.mark.asyncio
async def test_roll_release_no_selection_notifies(roll_manifests, monkeypatch):
    """Part 2: pressing roll with no selection (forced-enabled button)
    notifies rather than rolling."""
    captured: list[tuple[str, str]] = []

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        def fake_notify(message, *, severity="information", timeout=4.0, **kw):
            captured.append((str(message), severity))

        monkeypatch.setattr(screen, "notify", fake_notify)

        screen._selected_role = ""
        screen._handle_roll_release()
        await pilot.pause()

        assert any(
            sev == "warning" and "role" in m.lower() for m, sev in captured
        ), captured
        assert screen.query_one("#roll-release-gate", Static).display is False
