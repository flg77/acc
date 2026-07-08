"""OKF P5 — knowledge packs: an OKF bundle ships in a `.accpkg`, installs, and
is discovered + indexed into a collective's document store for retrieval.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from acc.docstore import DocumentStore
from acc.pkg.build import MANIFEST_NAME, build
from acc.pkg.install import install
from acc.pkg.knowledge import index_installed_bundles, installed_okf_bundle_dirs
from acc.pkg.manifest import AccPkgManifest
from acc.pkg.registry import Registry


class _FakeVec:
    """Fallback-path backend: stores rows, returns every chunk (ranking is
    irrelevant — we assert presence + the boundary/scope filters)."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, dict]] = []

    def insert(self, table: str, records: list[dict]) -> int:
        self.rows.extend((table, dict(r)) for r in records)
        return len(records)

    def search(self, table: str, embedding: list[float], top_k: int) -> list[dict]:
        return [r for (t, r) in self.rows if t == table]


async def _embed(_text: str) -> list[float]:
    return [0.1] * 384


def _write_knowledge_source(root: Path, *, name="@acc/okf-finance",
                            version="0.1.0") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "name": name,
        "version": version,
        "description": "Sample OKF finance knowledge bundle",
        "bundles": [{"name": "finance", "path": "bundles/finance/"}],
    }
    (root / MANIFEST_NAME).write_text(yaml.safe_dump(manifest), encoding="utf-8")
    b = root / "bundles" / "finance"
    b.mkdir(parents=True)
    (b / "Ratios.md").write_text(
        "---\ntype: Reference\ndomain: finance\ntitle: Ratios\n---\n"
        "alpha current ratio = current assets / current liabilities\n",
        encoding="utf-8")
    (b / "index.md").write_text("# Index\n", encoding="utf-8")
    return root


# --- manifest --------------------------------------------------------------

def test_manifest_accepts_bundles():
    m = AccPkgManifest(schema_version=1, name="@acc/okf-x", version="1.0.0",
                       bundles=[{"name": "b1", "path": "bundles/b1/"}])
    assert m.bundles[0].name == "b1" and m.bundles[0].path == "bundles/b1/"


def test_manifest_rejects_duplicate_bundle_names():
    with pytest.raises(Exception):
        AccPkgManifest(schema_version=1, name="@acc/okf-x", version="1.0.0",
                       bundles=[{"name": "b", "path": "bundles/b/"},
                                {"name": "b", "path": "bundles/b2/"}])


# --- build → install → discover -------------------------------------------

@pytest.fixture
def installed_registry(tmp_path: Path) -> Registry:
    src = _write_knowledge_source(tmp_path / "src")
    out = tmp_path / "dist" / "okf-finance-0.1.0.accpkg"
    out.parent.mkdir(parents=True)
    build(src, out)
    reg = Registry(root=tmp_path / "pkgs")
    install(out, registry=reg)
    return reg


def test_installed_bundle_is_discovered(installed_registry: Registry):
    dirs = installed_okf_bundle_dirs(installed_registry)
    assert [d.name for d in dirs] == ["finance"]
    assert (dirs[0] / "Ratios.md").is_file()      # the concept rode along in the pack


# --- index into the store + idempotent -------------------------------------

@pytest.mark.asyncio
async def test_index_installed_bundles_then_retrieve(installed_registry: Registry):
    store = DocumentStore(vector=_FakeVec(), embed_fn=_embed, collective_id="c1")

    res = await index_installed_bundles(store, collective_id="c1",
                                        registry=installed_registry)
    assert res["indexed_bundles"] == 1 and res["documents"] >= 1

    out = await store.retrieve("alpha", top_k=5)
    assert any("Ratios" in r["title"] for r in out["results"])

    # Second call is a no-op (marker-guarded).
    res2 = await index_installed_bundles(store, collective_id="c1",
                                         registry=installed_registry)
    assert res2["indexed_bundles"] == 0 and res2["skipped"] >= 1


@pytest.mark.asyncio
async def test_index_is_per_collective(installed_registry: Registry):
    # A different collective indexes independently (its own marker).
    store = DocumentStore(vector=_FakeVec(), embed_fn=_embed, collective_id="c2")
    res = await index_installed_bundles(store, collective_id="c2",
                                        registry=installed_registry)
    assert res["indexed_bundles"] == 1


def test_no_packages_installed_is_empty(tmp_path: Path):
    assert installed_okf_bundle_dirs(Registry(root=tmp_path / "empty")) == []
