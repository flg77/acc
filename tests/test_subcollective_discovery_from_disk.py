"""Tests for OpenSpec `20260602-assistant-blindspots` Phase 1.3 —
filesystem-discovery of sibling sub-collectives.

When ``ACC_DISCOVER_SUBCOLLECTIVES_ROOT`` points at a directory of
``*/collective.yaml`` files, ``load_collective`` merges those siblings
into the hub spec's ``managed_sub_collectives`` so the Assistant's
perception block surfaces them without editing the hub yaml.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from acc.collective import (
    CollectiveSpec,
    _merge_discovered_subcollectives,
    load_collective,
)


def _write_sibling(
    root: Path, name: str, collective_id: str,
    *, role_def: dict | None = None,
) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    body: dict = {"collective_id": collective_id, "agents": []}
    if role_def:
        body["role_definition"] = role_def
    (d / "collective.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


def _write_hub(path: Path, *, sub_collectives: dict | None = None) -> Path:
    body: dict = {"collective_id": "hub-01", "agents": []}
    if sub_collectives:
        body["managed_sub_collectives"] = sub_collectives
    p = path / "collective.yaml"
    p.write_text(yaml.safe_dump(body), encoding="utf-8")
    return p


class TestDiscoveryDisabledByDefault:
    def test_env_unset_no_discovery(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("ACC_DISCOVER_SUBCOLLECTIVES_ROOT", raising=False)
        hub = _write_hub(tmp_path)
        spec = load_collective(hub)
        assert spec.managed_sub_collectives == {}

    def test_env_set_to_missing_dir_no_discovery(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv(
            "ACC_DISCOVER_SUBCOLLECTIVES_ROOT", str(tmp_path / "nope")
        )
        hub = _write_hub(tmp_path)
        spec = load_collective(hub)
        assert spec.managed_sub_collectives == {}


class TestDiscoverySurfacing:
    def test_three_siblings_become_entries(
        self, tmp_path, monkeypatch
    ) -> None:
        root = tmp_path / "discover"
        root.mkdir()
        _write_sibling(root, "deep-research", "deep-research")
        _write_sibling(root, "coding", "coding-cluster")
        _write_sibling(root, "analytics", "analytics-cluster")
        monkeypatch.setenv(
            "ACC_DISCOVER_SUBCOLLECTIVES_ROOT", str(root)
        )
        hub = _write_hub(tmp_path)
        spec = load_collective(hub)
        assert set(spec.managed_sub_collectives.keys()) == {
            "deep-research", "coding-cluster", "analytics-cluster",
        }

    def test_domain_and_description_lifted_from_role_definition(
        self, tmp_path, monkeypatch
    ) -> None:
        root = tmp_path / "discover"
        root.mkdir()
        _write_sibling(
            root, "deep-research", "deep-research",
            role_def={
                "domain_id": "research",
                "purpose": "Deep arxiv investigation and synthesis.",
            },
        )
        monkeypatch.setenv(
            "ACC_DISCOVER_SUBCOLLECTIVES_ROOT", str(root)
        )
        hub = _write_hub(tmp_path)
        spec = load_collective(hub)
        entry = spec.managed_sub_collectives["deep-research"]
        assert entry.domain == "research"
        assert "Deep arxiv investigation" in entry.description


class TestHubEntriesWin:
    def test_explicit_hub_entry_overrides_discovered(
        self, tmp_path, monkeypatch
    ) -> None:
        root = tmp_path / "discover"
        root.mkdir()
        _write_sibling(
            root, "deep-research", "deep-research",
            role_def={"domain_id": "discovered-domain"},
        )
        monkeypatch.setenv(
            "ACC_DISCOVER_SUBCOLLECTIVES_ROOT", str(root)
        )
        hub = _write_hub(
            tmp_path,
            sub_collectives={
                "deep-research": {
                    "role_templates": [],
                    "domain": "hub-curated-domain",
                    "description": "hub-curated",
                    "idle_hibernate_minutes": 30,
                },
            },
        )
        spec = load_collective(hub)
        # The hub's explicit entry takes precedence.
        assert spec.managed_sub_collectives["deep-research"].domain == \
            "hub-curated-domain"

    def test_self_collective_id_excluded(
        self, tmp_path, monkeypatch
    ) -> None:
        # Discovery dir contains a sibling whose collective_id matches
        # the hub's — must not show up under itself.
        root = tmp_path / "discover"
        root.mkdir()
        _write_sibling(root, "self", "hub-01")
        monkeypatch.setenv(
            "ACC_DISCOVER_SUBCOLLECTIVES_ROOT", str(root)
        )
        hub = _write_hub(tmp_path)
        spec = load_collective(hub)
        assert "hub-01" not in spec.managed_sub_collectives


class TestMalformedSiblingsTolerated:
    def test_malformed_yaml_silently_skipped(
        self, tmp_path, monkeypatch
    ) -> None:
        root = tmp_path / "discover"
        root.mkdir()
        _write_sibling(root, "good", "good-cluster")
        bad = root / "bad"
        bad.mkdir()
        (bad / "collective.yaml").write_text(
            "::: not valid yaml :::", encoding="utf-8"
        )
        monkeypatch.setenv(
            "ACC_DISCOVER_SUBCOLLECTIVES_ROOT", str(root)
        )
        hub = _write_hub(tmp_path)
        spec = load_collective(hub)
        assert "good-cluster" in spec.managed_sub_collectives
        # Bad sibling didn't break anything.
        assert len(spec.managed_sub_collectives) == 1

    def test_sibling_dir_without_collective_yaml_skipped(
        self, tmp_path, monkeypatch
    ) -> None:
        root = tmp_path / "discover"
        root.mkdir()
        (root / "empty").mkdir()
        monkeypatch.setenv(
            "ACC_DISCOVER_SUBCOLLECTIVES_ROOT", str(root)
        )
        hub = _write_hub(tmp_path)
        spec = load_collective(hub)
        assert spec.managed_sub_collectives == {}


class TestMergeHelperDirect:
    def test_idempotent_when_env_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("ACC_DISCOVER_SUBCOLLECTIVES_ROOT", raising=False)
        spec = CollectiveSpec(collective_id="hub-01")
        merged = _merge_discovered_subcollectives(spec)
        assert merged.managed_sub_collectives == {}
