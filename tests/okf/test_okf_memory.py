"""OKF P2 — indexing an OKF bundle into the collective document store.

Uses a duck-typed stub store (records ingest calls) so the test needs no vector
backend or embedding model — it asserts the *mapping*: one document per
concept, OKF ``type``/``path`` carried forward as tags.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.lib.okf import from_obsidian, index_bundle, index_bundle_path, load_bundle

NOW = "2026-07-08T00:00:00Z"


class StubStore:
    def __init__(self):
        self.calls: list[dict] = []

    async def ingest(self, *, title, text, source="", tags=None):
        self.calls.append({"title": title, "text": text,
                           "source": source, "tags": tags or []})
        return {"doc_id": f"doc{len(self.calls)}", "chunks": 1, "title": title}


@pytest.mark.asyncio
async def test_index_bundle_ingests_per_concept_with_okf_tags(tmp_path: Path):
    vault = tmp_path / "v"
    (vault / "runbooks").mkdir(parents=True)
    (vault / "runbooks" / "Restart.md").write_text(
        "---\ntags: [ops]\n---\nRestart it.\n", encoding="utf-8")
    (vault / "Idea.md").write_text("raw idea\n", encoding="utf-8")
    bundle = from_obsidian(vault, tmp_path / "b", now=NOW)

    store = StubStore()
    res = await index_bundle(store, bundle)
    assert res["indexed"] == 2 and res["skipped"] == 0
    assert len(store.calls) == 2

    ops = next(c for c in store.calls if c["source"].endswith("runbooks/Restart.md"))
    assert "okf-type:Runbook" in ops["tags"]                 # folder-inferred type
    assert "ops" in ops["tags"]                              # front-matter tag preserved
    assert "okf-path:runbooks/Restart.md" in ops["tags"]     # provenance for P3 filter
    assert ops["source"] == "okf:runbooks/Restart.md"


@pytest.mark.asyncio
async def test_index_skip_nonconformant(tmp_path: Path):
    (tmp_path / "good.md").write_text("---\ntype: Reference\n---\nok\n",
                                      encoding="utf-8")
    (tmp_path / "bad.md").write_text("no front matter\n", encoding="utf-8")
    store = StubStore()
    res = await index_bundle(store, load_bundle(tmp_path), skip_nonconformant=True)
    assert res["indexed"] == 1 and res["skipped"] == 1
    assert store.calls[0]["source"].endswith("good.md")


@pytest.mark.asyncio
async def test_index_bundle_path_with_extra_tags(tmp_path: Path):
    (tmp_path / "a.md").write_text("---\ntype: Reference\n---\nx\n",
                                   encoding="utf-8")
    store = StubStore()
    res = await index_bundle_path(store, tmp_path, extra_tags=["corpus:demo"])
    assert res["indexed"] == 1
    assert "corpus:demo" in store.calls[0]["tags"]
    assert "okf-type:Reference" in store.calls[0]["tags"]
