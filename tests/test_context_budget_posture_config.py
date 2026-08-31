"""The posture's config surface: yaml, environment, and named profiles.

`test_context_budget.py` covers the packer, `test_context_budget_wiring.py` the
seam into `cognitive_core`. This covers the third side: how an operator *says*
what the posture should be. Three routes have to agree -- `acc-config.yaml`,
the two environment variables, and a named profile -- and the precedence
between them has to be the same as every other setting in ACC, or an operator
who has learned one part of the system is misled by this one.

The malformed-value tests are the point of the file. A typo must fall back to
the posture, never to zero: a silently-accepted empty margin removes the only
cushion the estimator has, which is the exact failure this change exists to
prevent.

Change: ``openspec/changes/20260826-context-budget`` Phase 1.4.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import acc.cognitive_core as cc
from acc.config import _ENV_MAP, ACCConfig, ContextBudgetConfig, load_config
from acc.context_budget import posture_for, resolve_ceiling
from acc.profiles import SETTABLE

_ENV_VARS = (
    "ACC_CONTEXT_BUDGET",
    "ACC_CONTEXT_WINDOW_DEFAULT",
    "ACC_CONTEXT_RESERVE_OUTPUT",
    "ACC_CONTEXT_SAFETY_MARGIN",
    "ACC_LLM_CONTEXT_WINDOW",
    "ACC_DEPLOY_MODE",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# The config model
# ---------------------------------------------------------------------------

class TestContextBudgetConfig:
    def test_defaults_are_the_undeclared_sentinel(self):
        """Zero means "use the posture", so a fresh config changes nothing."""
        cfg = ACCConfig()
        assert cfg.context_budget.reserve_output == 0
        assert cfg.context_budget.safety_margin_pct == 0.0

    def test_margin_at_or_above_one_is_refused(self):
        """It would reserve the whole window. A config error, not a tight budget."""
        with pytest.raises(ValidationError):
            ContextBudgetConfig(safety_margin_pct=1.0)

    def test_negative_values_are_refused(self):
        with pytest.raises(ValidationError):
            ContextBudgetConfig(safety_margin_pct=-0.01)
        with pytest.raises(ValidationError):
            ContextBudgetConfig(reserve_output=-1)

    def test_env_map_carries_both_keys(self):
        assert _ENV_MAP["ACC_CONTEXT_RESERVE_OUTPUT"] == (
            "context_budget", "reserve_output",
        )
        assert _ENV_MAP["ACC_CONTEXT_SAFETY_MARGIN"] == (
            "context_budget", "safety_margin_pct",
        )


class TestYamlAndEnvPrecedence:
    def _write(self, tmp_path, body: str) -> str:
        path = tmp_path / "acc-config.yaml"
        path.write_text(body, encoding="utf-8")
        return str(path)

    _YAML = (
        "deploy_mode: standalone\n"
        "context_budget:\n"
        "  reserve_output: 900\n"
        "  safety_margin_pct: 0.25\n"
    )

    def test_settable_from_yaml(self, tmp_path):
        cfg = load_config(self._write(tmp_path, self._YAML))
        assert cfg.context_budget.reserve_output == 900
        assert cfg.context_budget.safety_margin_pct == 0.25

    def test_env_overrides_yaml(self, tmp_path, monkeypatch):
        """Same precedence as every other setting -- env last."""
        path = self._write(tmp_path, self._YAML)
        monkeypatch.setenv("ACC_CONTEXT_RESERVE_OUTPUT", "256")
        monkeypatch.setenv("ACC_CONTEXT_SAFETY_MARGIN", "0.05")
        cfg = load_config(path)
        assert cfg.context_budget.reserve_output == 256
        assert cfg.context_budget.safety_margin_pct == 0.05


class TestProfilesCarryThePosture:
    def test_keys_are_settable(self):
        assert "context_budget.reserve_output" in SETTABLE
        assert "context_budget.safety_margin_pct" in SETTABLE


# ---------------------------------------------------------------------------
# posture_for overrides
# ---------------------------------------------------------------------------

class TestPostureOverrides:
    def test_absent_overrides_return_the_table_entry_unchanged(self):
        """Identity, not equality: no copy is made when nothing is overridden."""
        assert posture_for("edge") is posture_for("edge")
        assert posture_for("edge", reserve_output=None, margin_pct=None).name == "edge"

    def test_reserve_override_applies_and_renames(self):
        base = posture_for("standalone")
        custom = posture_for("standalone", reserve_output=256)
        assert custom.reserve_output == 256
        assert custom.name == "standalone+custom"
        assert base.reserve_output == 1024, "the table must not be mutated"

    def test_margin_override_applies(self):
        custom = posture_for("edge", margin_pct=0.05)
        assert custom.margin_pct == 0.05
        assert custom.reserve_output == 512, "the un-overridden half is kept"

    def test_shares_and_caps_survive_an_override(self):
        base = posture_for("rhoai")
        custom = posture_for("rhoai", reserve_output=1)
        assert custom.shares == base.shares
        assert custom.caps == base.caps

    def test_zero_is_a_real_override_not_an_absent_one(self):
        """``0`` and ``None`` must not collapse -- the config layer maps its own
        zero sentinel to ``None`` before it ever reaches here."""
        assert posture_for("edge", reserve_output=0).reserve_output == 0
        assert posture_for("edge").reserve_output == 512

    def test_override_reaches_the_ceiling(self):
        window, system = 8192, 1000
        base = resolve_ceiling(
            window, posture=posture_for("standalone"), system_tokens=system,
        )
        tighter = resolve_ceiling(
            window,
            posture=posture_for("standalone", reserve_output=2048),
            system_tokens=system,
        )
        assert base - tighter == 1024


# ---------------------------------------------------------------------------
# What the running core reads
# ---------------------------------------------------------------------------

class TestPostureOverridesFromEnv:
    def test_unset_is_no_override(self):
        assert cc._posture_overrides() == (None, None)

    def test_valid_values_parse(self, monkeypatch):
        monkeypatch.setenv("ACC_CONTEXT_RESERVE_OUTPUT", "256")
        monkeypatch.setenv("ACC_CONTEXT_SAFETY_MARGIN", "0.05")
        assert cc._posture_overrides() == (256, 0.05)

    @pytest.mark.parametrize("bad", ["abc", "", "  ", "1.5.2"])
    def test_malformed_reserve_falls_back_to_the_posture(self, monkeypatch, bad):
        monkeypatch.setenv("ACC_CONTEXT_RESERVE_OUTPUT", bad)
        assert cc._posture_overrides()[0] is None

    def test_negative_reserve_is_ignored(self, monkeypatch):
        monkeypatch.setenv("ACC_CONTEXT_RESERVE_OUTPUT", "-1")
        assert cc._posture_overrides()[0] is None

    @pytest.mark.parametrize("bad", ["20", "1.0", "-0.1", "nope"])
    def test_out_of_range_margin_is_ignored(self, monkeypatch, bad):
        """``20`` is the trap: a percentage where a fraction was wanted."""
        monkeypatch.setenv("ACC_CONTEXT_SAFETY_MARGIN", bad)
        assert cc._posture_overrides()[1] is None

    def test_zero_margin_is_honoured(self, monkeypatch):
        """In range, so it is a deliberate choice rather than a typo."""
        monkeypatch.setenv("ACC_CONTEXT_SAFETY_MARGIN", "0")
        assert cc._posture_overrides()[1] == 0.0


class TestResolvedBudgetHonoursOverrides:
    def _budget(self, monkeypatch, **env):
        monkeypatch.setenv("ACC_DEPLOY_MODE", "standalone")
        monkeypatch.setenv("ACC_LLM_CONTEXT_WINDOW", "8192")
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return cc._resolve_context_budget(1000)

    def test_reserve_override_tightens_the_ceiling(self, monkeypatch):
        base = self._budget(monkeypatch)
        tighter = self._budget(monkeypatch, ACC_CONTEXT_RESERVE_OUTPUT="2048")
        assert base.ceiling - tighter.ceiling == 1024
        assert tighter.posture.name == "standalone+custom"

    def test_margin_override_widens_the_ceiling(self, monkeypatch):
        base = self._budget(monkeypatch)
        wider = self._budget(monkeypatch, ACC_CONTEXT_SAFETY_MARGIN="0.05")
        assert wider.ceiling > base.ceiling

    def test_overrides_apply_to_the_kill_switch_override_path_too(self, monkeypatch):
        """``ACC_CONTEXT_BUDGET`` sets the window; the posture still applies."""
        budget = self._budget(
            monkeypatch,
            ACC_CONTEXT_BUDGET="4096",
            ACC_CONTEXT_RESERVE_OUTPUT="256",
        )
        assert budget.source == "override"
        assert budget.posture.reserve_output == 256

    def test_a_typo_never_disables_budgeting(self, monkeypatch):
        """The whole reason the parse is defensive."""
        budget = self._budget(
            monkeypatch,
            ACC_CONTEXT_RESERVE_OUTPUT="not-a-number",
            ACC_CONTEXT_SAFETY_MARGIN="also-not",
        )
        assert budget is not None
        assert budget.posture.name == "standalone"
        assert budget.posture.reserve_output == 1024
