"""Unit tests for the models.yaml write path (Configuration-pane CRUD).

`acc.models` was load-only; these cover the new `load_registry` / `save_registry`
/ `upsert_model` / `delete_model` / `set_role_model` helpers: round-trip,
in-place replace, role_models cleanup on delete, header preservation, and the
"drop empty optional fields" tidy-write.
"""

from __future__ import annotations

from pathlib import Path

from acc.models import (
    ModelEntry,
    ModelRegistry,
    delete_model,
    load_registry,
    save_registry,
    set_role_model,
    upsert_model,
)

_HEADER = "# my hand-written header\n# keep me\n\n"


def _seed(path: Path) -> None:
    path.write_text(
        _HEADER
        + "models:\n"
        + "  - model_id: a\n    backend: vllm\n    model: m-a\n    base_url: http://a\n"
        + "  - model_id: b\n    backend: anthropic\n    model: m-b\n"
        + "role_models:\n    coding: a\n    reviewer: b\n",
        encoding="utf-8",
    )


def test_load_registry_reads_models_and_role_models(tmp_path):
    p = tmp_path / "models.yaml"
    _seed(p)
    reg = load_registry(p)
    assert [e.model_id for e in reg.models] == ["a", "b"]
    assert reg.role_models == {"coding": "a", "reviewer": "b"}


def test_upsert_adds_then_replaces_in_place(tmp_path):
    p = tmp_path / "models.yaml"
    _seed(p)
    # add new
    upsert_model(ModelEntry(model_id="c", backend="ollama", model="m-c"), p)
    assert [e.model_id for e in load_registry(p).models] == ["a", "b", "c"]
    # replace existing (a) in place — order preserved, fields updated
    upsert_model(ModelEntry(model_id="a", backend="vllm", model="m-a2"), p)
    reg = load_registry(p)
    assert [e.model_id for e in reg.models] == ["a", "b", "c"]
    assert reg.models[0].model == "m-a2"


def test_delete_model_drops_entry_and_dangling_role_maps(tmp_path):
    p = tmp_path / "models.yaml"
    _seed(p)
    delete_model("a", p)
    reg = load_registry(p)
    assert [e.model_id for e in reg.models] == ["b"]
    # role_models pointing at the deleted model is cleaned up; others stay
    assert reg.role_models == {"reviewer": "b"}


def test_set_role_model_maps_and_unmaps(tmp_path):
    p = tmp_path / "models.yaml"
    _seed(p)
    set_role_model("ingester", "b", p)
    assert load_registry(p).role_models.get("ingester") == "b"
    set_role_model("coding", None, p)  # unmap → falls back to default
    assert "coding" not in load_registry(p).role_models


def test_save_preserves_header_and_drops_empty_fields(tmp_path):
    p = tmp_path / "models.yaml"
    _seed(p)
    # entry b has no base_url/api_key_env/label — save must not emit empty keys
    save_registry(load_registry(p), p)
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# my hand-written header")
    assert "base_url: ''" not in text
    assert "api_key_env: ''" not in text
    # a real value is still present
    assert "base_url: http://a" in text


def test_save_fresh_file_gets_default_header(tmp_path):
    p = tmp_path / "models.yaml"  # does not exist yet
    reg = ModelRegistry(
        models=[ModelEntry(model_id="x", backend="vllm", model="mx")],
        role_models={"coding": "x"},
    )
    save_registry(reg, p)
    text = p.read_text(encoding="utf-8")
    assert text.lstrip().startswith("# Central model registry")
    assert load_registry(p).role_models == {"coding": "x"}
