"""Guards for the generated model-facing capability catalog (ACC Roadmap DS-04).

The catalog only means something if it cannot drift and cannot be
incomplete.  Three guards:

* **Drift** — the committed ``docs/tool-catalog.md`` matches what the
  generator produces from a live boot of the registries.
* **Completeness** — every ``skills/*/`` and ``mcps/*/`` directory on
  disk reached the registry.  ``load_from()`` logs-and-drops a capability
  whose adapter will not import (correct at runtime, dangerous at
  inventory time), so this turns a silent drop into a red build.
* **Boundedness** — the ``allowed_tools: []`` count is pinned.  An MCP
  whose tool surface is delegated wholesale to an upstream is threat
  ACC-TM-11; adding another one should be a deliberate, visible act.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tools.gen_tool_catalog import (
    CompletenessError,
    _dirs_on_disk,
    build,
    render,
)

_REPO = Path(__file__).resolve().parent.parent
_CATALOG = _REPO / "docs" / "tool-catalog.md"

# Servers that today delegate their whole tool surface to an upstream.
# Pinned so a NEW one has to be added here consciously — see ACC-TM-11.
_KNOWN_UNBOUNDED = {
    "arxiv",
    "rss_fetch",
    "semantic_scholar",
    "web_archive",
    "wikipedia",
}


@pytest.fixture(scope="module")
def catalog() -> dict:
    return build(_REPO)


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------


def test_every_skill_on_disk_reached_the_registry(catalog: dict) -> None:
    """A skill whose adapter will not import leaves the surface silently."""
    on_disk = set(_dirs_on_disk(_REPO / "skills"))
    catalogued = {s["id"] for s in catalog["skills"]}
    assert on_disk <= catalogued, (
        f"skills on disk missing from the catalog: {sorted(on_disk - catalogued)}"
    )


def test_every_mcp_on_disk_reached_the_registry(catalog: dict) -> None:
    on_disk = set(_dirs_on_disk(_REPO / "mcps"))
    catalogued = {m["id"] for m in catalog["mcps"]}
    assert on_disk <= catalogued, (
        f"mcps on disk missing from the catalog: {sorted(on_disk - catalogued)}"
    )


def test_completeness_guard_actually_fires(tmp_path: Path) -> None:
    """The guard is worthless unless it catches a real silent drop.

    Build a repo-shaped tree with one skill whose adapter cannot import.
    The registry drops it with a warning; ``build()`` must refuse.
    """
    (tmp_path / "mcps").mkdir()
    skills = tmp_path / "skills"
    skills.mkdir()

    # A healthy skill, copied from the real tree so the loader is exercised
    # exactly as it is in production.
    shutil.copytree(_REPO / "skills" / "_base", skills / "_base")
    shutil.copytree(_REPO / "skills" / "date_now", skills / "date_now",
                    ignore=shutil.ignore_patterns("__pycache__"))

    # A broken one: valid manifest, adapter that raises on import.
    broken = skills / "ghost_capability"
    broken.mkdir()
    (broken / "skill.yaml").write_text(
        'purpose: "A capability that will not load."\n'
        'adapter_class: "GhostSkill"\n',
        encoding="utf-8",
    )
    (broken / "adapter.py").write_text(
        "raise ImportError('deliberately broken for the DS-04 guard test')\n",
        encoding="utf-8",
    )

    with pytest.raises(CompletenessError) as exc:
        build(tmp_path)
    assert "ghost_capability" in str(exc.value)


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


def test_committed_catalog_is_not_stale(catalog: dict) -> None:
    """`docs/tool-catalog.md` matches a live boot of the registries."""
    assert _CATALOG.exists(), "docs/tool-catalog.md is missing — run the generator"
    committed = _CATALOG.read_text(encoding="utf-8")
    assert committed == render(catalog), (
        "docs/tool-catalog.md is stale — re-run "
        "`python -m tools.gen_tool_catalog`"
    )


def test_render_produces_intact_table_rows(catalog: dict) -> None:
    """Manifest prose carries newlines; an unescaped one breaks its row."""
    for line in render(catalog).splitlines():
        if line.startswith("| `"):
            assert line.count("|") == 7, f"malformed table row: {line[:80]}"


# ---------------------------------------------------------------------------
# Boundedness (ACC-TM-11)
# ---------------------------------------------------------------------------


def test_unbounded_tool_surfaces_are_the_known_set(catalog: dict) -> None:
    """`allowed_tools: []` delegates the surface to an upstream.

    Not a failure in itself — these are read-only research servers — but
    a NEW one must be a deliberate act, not a default nobody noticed.
    """
    unbounded = {m["id"] for m in catalog["mcps"] if not m["tool_surface_bounded"]}
    new = unbounded - _KNOWN_UNBOUNDED
    assert not new, (
        f"new MCP server(s) with an unbounded tool surface: {sorted(new)}. "
        "Set allowed_tools explicitly, or add to _KNOWN_UNBOUNDED with a "
        "reason. See docs/THREAT-MODEL.md ACC-TM-11."
    )


def test_catalog_records_the_governance_fields(catalog: dict) -> None:
    """Every entry carries what a category decision needs."""
    for s in catalog["skills"]:
        assert s["purpose"], f"skill {s['id']} has no model-visible purpose"
        assert s["risk_level"], f"skill {s['id']} has no risk_level"
    for m in catalog["mcps"]:
        assert m["purpose"], f"mcp {m['id']} has no model-visible purpose"
        assert m["risk_level"], f"mcp {m['id']} has no risk_level"
        assert m["transport"] in ("http", "stdio"), m["id"]
