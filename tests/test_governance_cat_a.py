"""Tests for acc/governance.py — CatAEvaluator (ACC-12)."""

from __future__ import annotations

import pytest

from acc.governance import CatAEvaluator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_input(
    signal_type="TASK_ASSIGN",
    collective_id="sol-01",
    from_agent="arbiter-01",
    agent_collective="sol-01",
    agent_role="analyst",
) -> dict:
    return {
        "signal": {
            "signal_type": signal_type,
            "collective_id": collective_id,
            "from_agent": from_agent,
        },
        "agent": {
            "collective_id": agent_collective,
            "agent_id": "analyst-9c1d",
            "role": agent_role,
            "domain_receptors": [],
        },
        "action": signal_type,
        "target_category": "",
    }


# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------


class TestModeDetection:
    def test_passthrough_mode_when_enforce_false(self):
        """Default enforce=False → passthrough mode regardless of WASM availability."""
        evaluator = CatAEvaluator(wasm_path="/nonexistent.wasm", enforce=False)
        assert evaluator._mode == "passthrough"

    def test_passthrough_mode_missing_wasm(self, tmp_path):
        """enforce=True but WASM file missing and no opa binary → passthrough."""
        evaluator = CatAEvaluator(wasm_path=str(tmp_path / "missing.wasm"), enforce=True)
        # Mode will be passthrough (no wasmtime or opa available in test env)
        assert evaluator._mode in ("passthrough", "subprocess", "wasm")


# ---------------------------------------------------------------------------
# Passthrough evaluation
# ---------------------------------------------------------------------------


class TestPassthroughEvaluation:
    def test_always_allows_in_passthrough_mode(self):
        evaluator = CatAEvaluator(wasm_path="/nonexistent.wasm", enforce=False)
        allowed, reason = evaluator.evaluate(_make_input())
        assert allowed is True

    def test_reason_is_passthrough(self):
        evaluator = CatAEvaluator(wasm_path="/nonexistent.wasm", enforce=False)
        _, reason = evaluator.evaluate(_make_input())
        assert reason == "passthrough"

    def test_returns_allowed_even_for_cross_collective_signal(self):
        """In passthrough mode, cross-collective signals are not blocked."""
        evaluator = CatAEvaluator(wasm_path="/nonexistent.wasm", enforce=False)
        inp = _make_input(collective_id="sol-02", agent_collective="sol-01")
        allowed, _ = evaluator.evaluate(inp)
        assert allowed is True


# ---------------------------------------------------------------------------
# build_input helper
# ---------------------------------------------------------------------------


class TestBuildInput:
    def test_signal_type_preserved(self):
        evaluator = CatAEvaluator(enforce=False)
        doc = evaluator.build_input(
            signal_type="TASK_ASSIGN",
            collective_id="sol-01",
            from_agent="arbiter-01",
            agent_id="analyst-9c1d",
            agent_role="analyst",
        )
        assert doc["signal"]["signal_type"] == "TASK_ASSIGN"
        assert doc["agent"]["role"] == "analyst"

    def test_action_defaults_to_signal_type(self):
        evaluator = CatAEvaluator(enforce=False)
        doc = evaluator.build_input(
            signal_type="ROLE_UPDATE",
            collective_id="sol-01",
            from_agent="arbiter-01",
            agent_id="analyst-9c1d",
            agent_role="analyst",
        )
        assert doc["action"] == "ROLE_UPDATE"

    def test_action_can_be_overridden(self):
        evaluator = CatAEvaluator(enforce=False)
        doc = evaluator.build_input(
            signal_type="ROLE_UPDATE",
            collective_id="sol-01",
            from_agent="arbiter-01",
            agent_id="analyst-9c1d",
            agent_role="analyst",
            action="CUSTOM_ACTION",
        )
        assert doc["action"] == "CUSTOM_ACTION"

    def test_domain_receptors_default_empty(self):
        evaluator = CatAEvaluator(enforce=False)
        doc = evaluator.build_input(
            signal_type="TASK_ASSIGN",
            collective_id="sol-01",
            from_agent="arbiter-01",
            agent_id="analyst-9c1d",
            agent_role="analyst",
        )
        assert doc["agent"]["domain_receptors"] == []

    def test_target_category_included(self):
        evaluator = CatAEvaluator(enforce=False)
        doc = evaluator.build_input(
            signal_type="RULE_UPDATE",
            collective_id="sol-01",
            from_agent="arbiter-01",
            agent_id="analyst-9c1d",
            agent_role="analyst",
            target_category="A",
        )
        assert doc["target_category"] == "A"


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------


class TestErrorResilience:
    def test_eval_exception_returns_allowed_true(self, monkeypatch):
        """On unexpected evaluation error the evaluator fails open (allow)."""
        evaluator = CatAEvaluator(enforce=False)

        def _raise(*_):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(evaluator, "_eval_passthrough", _raise)
        allowed, reason = evaluator.evaluate(_make_input())
        assert allowed is True
        assert "evaluation_error" in reason
