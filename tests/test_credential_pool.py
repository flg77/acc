"""Several credentials per provider, rotated on throttle — never past a fault.

The dangerous case is the one that looks like success: rotating past a 401. The
pool quietly runs on three of four keys and nobody learns the fourth was revoked
until renewal, when it is far more expensive to discover. So a throttle rests a
key and an auth rejection **faults** it, loudly and visibly.

The second thing under test is that nothing here ever touches a credential
value. The pool holds variable names and health; a pool that handled values
would be a new place for them to leak, and the test plants a recognisable
secret and asserts it never appears in any output.
"""

from __future__ import annotations

import time

import pytest

from acc import credential_pool as C
from acc.credential_pool import Health

POOLS = """\
pools:
  anthropic:
    env_vars:
      - ANTHROPIC_KEY_A
      - ANTHROPIC_KEY_B
    cooldown_s: 60
  groq:
    env_vars:
      - GROQ_KEY
"""

SECRET = "sk-planted-value-must-never-appear"


@pytest.fixture
def pools(tmp_path, monkeypatch):
    (tmp_path / "credential-pools.yaml").write_text(POOLS, encoding="utf-8")
    monkeypatch.setenv(C.POOLS_PATH_VAR, str(tmp_path / "credential-pools.yaml"))
    for name in ("ANTHROPIC_KEY_A", "ANTHROPIC_KEY_B", "GROQ_KEY"):
        monkeypatch.setenv(name, SECRET)
    return tmp_path


# --------------------------------------------------------------------------
# Classification — the distinction everything turns on
# --------------------------------------------------------------------------


class TestClassification:
    @pytest.mark.parametrize(
        "code,message,expected",
        [
            (429, "", "throttle"),
            (None, "rate limit exceeded", "throttle"),
            (None, "Too Many Requests", "throttle"),
            (401, "", "auth"),
            (403, "", "auth"),
            (None, "invalid api key", "auth"),
            (None, "this key was revoked", "auth"),
            (503, "", "server"),
            (500, "", "server"),
            (400, "bad request", "other"),
        ],
    )
    def test_classification(self, code, message, expected):
        assert C.classify(code, message) == expected

    def test_a_provider_outage_does_not_blame_the_credential(self, pools):
        """Resting a key for a 503 would punish it for someone else's outage."""
        pool = C.load_pools()["anthropic"]
        entry = pool.entries[0]
        C.record_failure(pool, entry, status_code=503)
        assert entry.health == Health.HEALTHY


# --------------------------------------------------------------------------
# Rotation
# --------------------------------------------------------------------------


class TestRotation:
    def test_a_throttled_key_rests_and_the_other_is_used(self, pools):
        """The criterion: one rate-limited, work continues on the other."""
        pool = C.load_pools()["anthropic"]
        first = C.select(pool)
        C.record_failure(pool, first, status_code=429)

        second = C.select(pool)
        assert second is not None
        assert second.env_var != first.env_var

    def test_a_rested_key_returns_after_its_cooldown(self, pools):
        pool = C.load_pools()["anthropic"]
        entry = pool.entries[0]
        C.record_failure(pool, entry, status_code=429)
        assert entry not in C.available(pool)

        entry.until = time.time() - 1     # cooldown elapsed
        assert entry in C.available(pool)

    def test_a_throttle_rests_rather_than_removes(self, pools):
        """Removing would turn a busy hour into permanent capacity loss."""
        pool = C.load_pools()["anthropic"]
        C.record_failure(pool, pool.entries[0], status_code=429)
        assert len(pool.entries) == 2
        assert pool.entries[0].health == Health.COOLING

    def test_selection_spreads_use(self, pools):
        pool = C.load_pools()["anthropic"]
        for _ in range(4):
            entry = C.select(pool)
            C.record_success(pool, entry)
        assert {e.uses for e in pool.entries} == {2}

    def test_a_single_credential_pool_behaves_normally(self, pools):
        pool = C.load_pools()["groq"]
        assert C.select(pool).env_var == "GROQ_KEY"

    def test_an_absent_variable_is_not_selected(self, pools, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_KEY_A")
        pool = C.load_pools()["anthropic"]
        assert C.select(pool).env_var == "ANTHROPIC_KEY_B"

    def test_no_usable_credential_returns_none(self, pools, monkeypatch):
        for name in ("ANTHROPIC_KEY_A", "ANTHROPIC_KEY_B"):
            monkeypatch.delenv(name)
        assert C.select(C.load_pools()["anthropic"]) is None


# --------------------------------------------------------------------------
# The dangerous case
# --------------------------------------------------------------------------


class TestAuthFaultsAreNeverRotatedPast:
    def test_a_rejected_key_is_faulted_not_rested(self, pools):
        """Rotating past a 401 hides a revoked credential behind normal operation."""
        pool = C.load_pools()["anthropic"]
        entry = pool.entries[0]
        C.record_failure(pool, entry, status_code=401)
        assert entry.health == Health.FAULTED
        assert "authentication" in entry.reason.lower()

    def test_a_faulted_key_stays_out_regardless_of_time(self, pools):
        pool = C.load_pools()["anthropic"]
        entry = pool.entries[0]
        C.record_failure(pool, entry, status_code=401)
        entry.until = 0.0
        assert entry not in C.available(pool)

    def test_a_fault_is_visible_in_status(self, pools):
        pool = C.load_pools()["anthropic"]
        C.record_failure(pool, pool.entries[0], status_code=401)
        C.save_state({"anthropic": pool})

        rows = {r["env_var"]: r for r in C.status("anthropic")}
        assert rows["ANTHROPIC_KEY_A"]["health"] == Health.FAULTED
        assert rows["ANTHROPIC_KEY_A"]["reason"]

    def test_reset_clears_a_fault_deliberately(self, pools):
        pool = C.load_pools()["anthropic"]
        C.record_failure(pool, pool.entries[0], status_code=401)
        C.save_state({"anthropic": pool})

        assert C.reset("anthropic") == 1
        assert all(r["health"] == Health.HEALTHY for r in C.status("anthropic"))


# --------------------------------------------------------------------------
# No value ever leaves
# --------------------------------------------------------------------------


class TestNoValueEverLeaves:
    def test_status_contains_only_names(self, pools):
        blob = repr(C.status())
        assert SECRET not in blob
        assert "ANTHROPIC_KEY_A" in blob

    def test_the_pool_file_contains_only_names(self, pools):
        C.add("openai", "OPENAI_KEY")
        assert SECRET not in C.pools_path().read_text(encoding="utf-8")

    def test_the_state_file_contains_only_names(self, pools):
        loaded = C.load_pools()
        C.record_failure(loaded["anthropic"], loaded["anthropic"].entries[0], status_code=429)
        C.save_state(loaded)
        assert SECRET not in C.state_path().read_text(encoding="utf-8")

    def test_select_returns_a_name_not_a_value(self, pools):
        entry = C.select(C.load_pools()["anthropic"])
        assert entry.env_var.startswith("ANTHROPIC_KEY")
        assert SECRET not in repr(entry)


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


class TestState:
    def test_cooldown_survives_a_reload(self, pools):
        loaded = C.load_pools()
        C.record_failure(loaded["anthropic"], loaded["anthropic"].entries[0], status_code=429)
        C.save_state(loaded)

        again = C.load_pools()["anthropic"]
        assert again.entries[0].health == Health.COOLING

    def test_an_expired_cooldown_does_not_survive_a_reload(self, pools):
        """A key resting yesterday is healthy today."""
        loaded = C.load_pools()
        entry = loaded["anthropic"].entries[0]
        entry.health, entry.until = Health.COOLING, time.time() - 10
        C.save_state(loaded)

        assert C.load_pools()["anthropic"].entries[0].health == Health.HEALTHY

    def test_state_is_written_owner_only(self, pools):
        """It names credentials, even though it holds no values."""
        import os
        import stat

        loaded = C.load_pools()
        C.save_state(loaded)
        mode = stat.S_IMODE(os.stat(C.state_path()).st_mode)
        if os.name != "nt":  # POSIX permissions only
            assert mode == 0o600

    def test_add_and_remove(self, pools):
        C.add("openai", "OPENAI_KEY")
        assert "openai" in C.load_pools()
        assert C.remove("openai", "OPENAI_KEY") is True
        assert C.load_pools()["openai"].entries == []

    def test_a_duplicate_is_refused(self, pools):
        with pytest.raises(C.CredentialPoolError, match="already"):
            C.add("anthropic", "ANTHROPIC_KEY_A")

    def test_removing_from_an_unknown_pool_is_false(self, pools):
        assert C.remove("nope", "X") is False

    def test_reset_on_an_unknown_pool_is_refused(self, pools):
        with pytest.raises(C.CredentialPoolError, match="no pool"):
            C.reset("nope")

    def test_a_malformed_file_yields_no_pools(self, pools, tmp_path):
        (tmp_path / "credential-pools.yaml").write_text("pools: [oh no", encoding="utf-8")
        assert C.load_pools() == {}
