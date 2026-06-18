"""033 WS-C — MODEL REGISTRY overview on the Config LLM tab.

The 2026-06-16 TUI review asked for an "all configured LLM endpoints"
overview in the config section.  The models.yaml registry (PR-MM1) is
that source of truth, but the LLM Endpoints tab previously surfaced only
the global default backend + the live per-agent table.  These tests pin
the pure row-prep helper that feeds the new MODEL REGISTRY table.
"""
from __future__ import annotations

from acc.models import ModelEntry
from acc.tui.screens.configuration import ConfigurationScreen


def test_empty_registry_yields_one_explanatory_row():
    # A fresh corpus has no models.yaml — show why the table is empty
    # rather than a silent blank (a review complaint about empty tables).
    rows = ConfigurationScreen._model_registry_rows([])
    assert len(rows) == 1
    assert len(rows[0]) == 5
    assert "no models.yaml" in rows[0][1]


def test_registry_rows_render_entries():
    entries = [
        ModelEntry(
            model_id="claude-sonnet", backend="anthropic",
            model="claude-sonnet-4-6", label="Claude Sonnet (reviewer)",
        ),
        ModelEntry(
            model_id="ollama-llama32", backend="ollama",
            model="llama3.2:3b", base_url="http://host:11434",
        ),
    ]
    rows = ConfigurationScreen._model_registry_rows(entries)
    assert len(rows) == 2
    assert rows[0][:3] == ("claude-sonnet", "anthropic", "claude-sonnet-4-6")
    assert rows[0][4] == "Claude Sonnet (reviewer)"
    assert rows[1][0] == "ollama-llama32"
    assert rows[1][3] == "http://host:11434"
    # A missing label falls back to an em-dash, never a blank cell.
    assert rows[1][4] == "—"


def test_long_fields_are_truncated():
    entries = [ModelEntry(
        model_id="x" * 50, backend="openai_compat", model="m", label="L" * 100,
    )]
    rows = ConfigurationScreen._model_registry_rows(entries)
    assert len(rows[0][0]) <= 24
    assert len(rows[0][4]) <= 40
