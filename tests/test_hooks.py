"""Operator hooks: they observe, and they cannot take the collective down.

Two properties matter more than the feature itself.

**A hook cannot stall a collective.** A hook that hangs is the obvious way an
observability feature turns into an outage, so the timeout is tested with a
command that really does sleep, not a mock that pretends to.

**A hook file is not arbitrary code execution.** Definitions live in a writable
YAML file. Without an allowlist, write access to that file would mean running
anything as whoever owns the dispatcher — so an un-allowlisted command is
refused at registration *and* refused again at execution, because a file can be
edited after the fact.
"""

from __future__ import annotations

import json
import sys
import time

import pytest

from acc import hooks as H


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(H.HOOKS_PATH_VAR, str(tmp_path / "hooks.yaml"))
    monkeypatch.setenv(H.ALLOWLIST_VAR, sys.executable)
    return tmp_path


_SCRIPTS: dict[str, object] = {}


def py(code: str, tmp_path=None) -> str:
    """A hook command that runs *code* from a script file.

    A file rather than `python -c "..."`: embedding source in a quoted command
    line makes these tests about shell quoting instead of about hooks.
    """
    import hashlib
    import pathlib
    import tempfile

    root = pathlib.Path(tmp_path) if tmp_path else pathlib.Path(tempfile.gettempdir())
    name = "hook_" + hashlib.sha1(code.encode()).hexdigest()[:12] + ".py"
    script = root / name
    script.write_text(code, encoding="utf-8")
    _SCRIPTS[name] = script
    return f"{sys.executable} {script}"


# --------------------------------------------------------------------------
# Registration and the allowlist
# --------------------------------------------------------------------------


class TestAllowlist:
    def test_an_unlisted_command_is_refused_at_registration(self, store, monkeypatch):
        monkeypatch.setenv(H.ALLOWLIST_VAR, "echo")
        with pytest.raises(H.HookError, match=H.ALLOWLIST_VAR):
            H.add("bad", "TASK_COMPLETE", "/bin/rm -rf /")

    def test_an_empty_allowlist_refuses_everything(self, store, monkeypatch):
        monkeypatch.setenv(H.ALLOWLIST_VAR, "")
        with pytest.raises(H.HookError, match="no hook allowlist"):
            H.add("any", "*", "echo hi")

    def test_execution_re_checks_the_allowlist(self, store, monkeypatch):
        """A definition file can be edited after registration.

        Checking only at registration would make the allowlist advisory.
        """
        H.add("ok", "TASK_COMPLETE", py("pass"))
        monkeypatch.setenv(H.ALLOWLIST_VAR, "something-else")
        hook = H.load()[0]
        run = H.run_hook(hook, "TASK_COMPLETE", {})
        assert not run.ok
        assert "refused" in run.detail

    def test_a_basename_match_is_enough(self, store, monkeypatch):
        monkeypatch.setenv(H.ALLOWLIST_VAR, "python.exe,python3,python")
        from pathlib import Path

        if Path(sys.executable).name in ("python.exe", "python3", "python"):
            H.check_allowed(py("pass"))

    def test_duplicate_names_are_refused(self, store):
        H.add("one", "*", py("pass"))
        with pytest.raises(H.HookError, match="already exists"):
            H.add("one", "*", py("pass"))


# --------------------------------------------------------------------------
# A hook cannot stall the collective
# --------------------------------------------------------------------------


class TestCannotStall:
    def test_a_hanging_hook_is_killed(self, store):
        """The load-bearing test.

        A real sleep, not a mock: the point is that the timeout actually fires.
        """
        H.add("slow", "TASK_COMPLETE", py("import time; time.sleep(30)"), timeout_s=1.0)
        started = time.monotonic()
        runs = H.Dispatcher().dispatch("TASK_COMPLETE", {})
        elapsed = time.monotonic() - started

        assert elapsed < 10, f"dispatch took {elapsed:.1f}s — the timeout did not fire"
        assert runs and not runs[0].ok
        assert "timed out" in runs[0].detail

    def test_a_failing_hook_does_not_stop_the_others(self, store):
        H.add("bad", "*", py("raise SystemExit(3)"))
        H.add("good", "*", py("pass"))
        runs = {r.name: r for r in H.Dispatcher().dispatch("TASK_COMPLETE", {})}
        assert runs["bad"].ok is False
        assert runs["good"].ok is True

    def test_a_missing_executable_is_reported_not_raised(self, store, monkeypatch):
        monkeypatch.setenv(H.ALLOWLIST_VAR, "definitely-not-a-real-binary")
        H.save([H.Hook(name="ghost", event="*", command="definitely-not-a-real-binary")])
        runs = H.Dispatcher().dispatch("TASK_COMPLETE", {})
        assert runs and not runs[0].ok

    def test_repeated_failures_disable_a_hook(self, store):
        """A hook failing forever is noise that trains people to ignore the record."""
        H.add("always-bad", "*", py("raise SystemExit(1)"))
        d = H.Dispatcher()
        for _ in range(H.FAILURE_LIMIT):
            d.dispatch("TASK_COMPLETE", {})
        assert "always-bad" in d.disabled()

        before = len(d.runs)
        d.dispatch("TASK_COMPLETE", {})
        assert len(d.runs) == before, "a disabled hook must stop running"

    def test_a_success_resets_the_failure_count(self, store, monkeypatch):
        H.add("flaky", "*", py("raise SystemExit(1)"))
        d = H.Dispatcher()
        d.dispatch("TASK_COMPLETE", {})
        H.save([H.Hook(name="flaky", event="*", command=py("pass"))])
        d.dispatch("TASK_COMPLETE", {})
        assert d.disabled() == []


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


class TestMatching:
    def test_only_the_named_event_fires(self, store):
        H.add("only-complete", "TASK_COMPLETE", py("pass"))
        d = H.Dispatcher()
        assert d.dispatch("TASK_ASSIGN", {}) == []
        assert len(d.dispatch("TASK_COMPLETE", {})) == 1

    def test_star_matches_everything(self, store):
        H.add("all", "*", py("pass"))
        d = H.Dispatcher()
        assert len(d.dispatch("TASK_ASSIGN", {})) == 1
        assert len(d.dispatch("ALERT_ESCALATE", {})) == 1

    def test_a_filter_narrows_by_payload_content(self, store):
        H.add("prod-only", "*", py("pass"), filter="production")
        d = H.Dispatcher()
        assert d.dispatch("TASK_COMPLETE", {"env": "staging"}) == []
        assert len(d.dispatch("TASK_COMPLETE", {"env": "production"})) == 1

    def test_a_disabled_hook_never_matches(self, store):
        H.save([H.Hook(name="off", event="*", command=py("pass"), enabled=False)])
        assert H.Dispatcher().dispatch("TASK_COMPLETE", {}) == []


# --------------------------------------------------------------------------
# Payload and storage
# --------------------------------------------------------------------------


class TestPayloadAndStorage:
    def test_the_event_reaches_the_hook_on_stdin(self, store, tmp_path):
        out = tmp_path / "seen.json"
        code = (
            "import sys, pathlib\n"
            "pathlib.Path(__file__).with_name('seen.json')"
            ".write_text(sys.stdin.read(), encoding='utf-8')\n"
        )
        H.add("capture", "TASK_COMPLETE", py(code, tmp_path))
        H.Dispatcher().dispatch("TASK_COMPLETE", {"task_id": "t-42"})

        received = json.loads(out.read_text(encoding="utf-8"))
        assert received["event"] == "TASK_COMPLETE"
        assert received["payload"]["task_id"] == "t-42"

    def test_removal_takes_effect_without_a_restart(self, store):
        """Definitions are read per dispatch, not cached at start."""
        H.add("temp", "*", py("pass"))
        d = H.Dispatcher()
        assert len(d.dispatch("TASK_COMPLETE", {})) == 1
        assert H.remove("temp") is True
        assert d.dispatch("TASK_COMPLETE", {}) == []

    def test_removing_an_absent_hook_reports_false(self, store):
        assert H.remove("never-existed") is False

    def test_a_malformed_file_fires_nothing_rather_than_crashing(self, store, tmp_path):
        (tmp_path / "hooks.yaml").write_text("hooks: [oh dear: [", encoding="utf-8")
        assert H.load() == []
        assert H.Dispatcher().dispatch("TASK_COMPLETE", {}) == []

    def test_a_malformed_entry_is_skipped_not_fatal(self, store, tmp_path):
        (tmp_path / "hooks.yaml").write_text(
            "hooks:\n  - name: broken\n  - name: fine\n    command: x\n",
            encoding="utf-8",
        )
        assert [h.name for h in H.load()] == ["fine"]

    def test_definitions_round_trip(self, store):
        H.add("rt", "TASK_COMPLETE", py("pass"), filter="abc", timeout_s=2.5)
        loaded = H.load()[0]
        assert (loaded.name, loaded.event, loaded.filter, loaded.timeout_s) == (
            "rt", "TASK_COMPLETE", "abc", 2.5,
        )

    def test_the_file_says_hooks_cannot_gate(self, store):
        """Someone will reasonably assume otherwise; the file should say so."""
        H.add("x", "*", py("pass"))
        text = H.hooks_path().read_text(encoding="utf-8")
        assert "oversight" in text.lower()
