"""Resume is ergonomics; retention is governed. They are not the same feature.

Losing an investigation because a terminal closed is friction. Deleting the
record of what an agent did is a governance event. Treating them alike is how
a convenience command ends up with the authority to erase an audit trail.

So the load-bearing test is the absence of something: **no removal path that
leaves no trace.** A session that vanished silently would leave the tracelog
claiming a history that is no longer there — and the function that gets added
later "just for cleanup" is exactly how that happens, which is why the module's
surface is asserted rather than assumed.

Retention defaults to keep-forever because that is what deployments do today.
An upgrade must not start deleting records because a new default said so.
"""

from __future__ import annotations

import json
import time

import pytest

from acc import sessions as S
from acc import tracelog


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    monkeypatch.setenv("ACC_TRACELOG", "1")
    monkeypatch.setenv(S.RETENTION_PATH_VAR, str(tmp_path / "retention.yaml"))
    return tmp_path


def make_session(root, session_id, *, role="analyst", blocked=False, age_days=0.0):
    """A session with one turn, optionally aged and blocked."""
    tracelog.emit(session_id, "session_start", root=root)
    tracelog.emit(
        session_id, "prompt_in", root=root, task_id="t1", role=role, prompt="a question"
    )
    tracelog.emit(
        session_id, "reply_out", root=root, task_id="t1", role=role, reply="an answer"
    )
    if blocked:
        tracelog.emit(
            session_id, "governance", root=root, task_id="t1",
            category="A", verdict="block",
        )
    tracelog.emit(session_id, "session_end", root=root)

    if age_days:
        path = tracelog.session_path(session_id, root=root)
        old = time.time() - age_days * 86400
        import os

        os.utime(path, (old, old))
        # Age the recorded timestamps too, since the summary reads them.
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            record["ts"] = old
            lines.append(json.dumps(record))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return session_id


# --------------------------------------------------------------------------
# No untraced removal
# --------------------------------------------------------------------------


class TestNoUntracedRemoval:
    def test_the_module_exposes_no_bare_delete(self, store):
        """The function added later 'just for cleanup' is how a trail dies."""
        for name in ("delete", "delete_session", "purge", "wipe", "drop"):
            assert not hasattr(S, name), f"acc.sessions.{name} bypasses the journal"

    def test_removal_writes_a_record(self, store):
        make_session(store, "old-one", age_days=90)
        (store / "retention.yaml").write_text(
            "retention:\n  name: ninety-days\n  keep_days: 30\n", encoding="utf-8"
        )

        removed = S.apply_retention(by="tester", root=store)
        assert [r["session_id"] for r in removed] == ["old-one"]

        journal = S.removals(root=store)
        assert len(journal) == 1
        assert journal[0]["session_id"] == "old-one"
        assert journal[0]["removed_by"] == "tester"
        assert journal[0]["policy"]["name"] == "ninety-days"

    def test_the_record_carries_a_digest_of_what_was_removed(self, store):
        make_session(store, "old-one", age_days=90)
        (store / "retention.yaml").write_text(
            "retention:\n  keep_days: 30\n", encoding="utf-8"
        )
        S.apply_retention(root=store)
        assert len(S.removals(root=store)[0]["sha256"]) == 64

    def test_the_session_file_is_actually_gone(self, store):
        make_session(store, "old-one", age_days=90)
        (store / "retention.yaml").write_text(
            "retention:\n  keep_days: 30\n", encoding="utf-8"
        )
        S.apply_retention(root=store)
        assert not tracelog.session_path("old-one", root=store).is_file()

    def test_dry_run_removes_nothing_and_journals_nothing(self, store):
        make_session(store, "old-one", age_days=90)
        (store / "retention.yaml").write_text(
            "retention:\n  keep_days: 30\n", encoding="utf-8"
        )
        S.apply_retention(root=store, dry_run=True)
        assert tracelog.session_path("old-one", root=store).is_file()
        assert S.removals(root=store) == []


# --------------------------------------------------------------------------
# Retention is conservative by default
# --------------------------------------------------------------------------


class TestRetentionDefaults:
    def test_the_default_keeps_everything(self, store):
        """An upgrade must not start deleting because a new default said so."""
        make_session(store, "ancient", age_days=3650)
        assert S.load_policy().keep_days == S.KEEP_FOREVER
        assert S.apply_retention(root=store) == []

    def test_an_unreadable_policy_keeps_everything(self, store):
        """Failing towards deletion on a parse error would destroy records."""
        (store / "retention.yaml").write_text("retention: [oh no", encoding="utf-8")
        make_session(store, "ancient", age_days=3650)
        assert S.apply_retention(root=store) == []

    def test_a_blocked_session_is_kept_regardless_of_age(self, store):
        """These are the records an incident review needs most."""
        make_session(store, "blocked-one", blocked=True, age_days=900)
        (store / "retention.yaml").write_text(
            "retention:\n  keep_days: 30\n  keep_blocked: true\n", encoding="utf-8"
        )
        assert S.apply_retention(root=store) == []

    def test_keep_blocked_can_be_switched_off_deliberately(self, store):
        make_session(store, "blocked-one", blocked=True, age_days=900)
        (store / "retention.yaml").write_text(
            "retention:\n  keep_days: 30\n  keep_blocked: false\n", encoding="utf-8"
        )
        assert [r["session_id"] for r in S.apply_retention(root=store)] == ["blocked-one"]

    def test_a_young_session_is_not_removed(self, store):
        make_session(store, "recent", age_days=1)
        (store / "retention.yaml").write_text(
            "retention:\n  keep_days: 30\n", encoding="utf-8"
        )
        assert S.apply_retention(root=store) == []

    def test_due_for_removal_reports_without_removing(self, store):
        make_session(store, "old-one", age_days=90)
        (store / "retention.yaml").write_text(
            "retention:\n  keep_days: 30\n", encoding="utf-8"
        )
        due = S.due_for_removal(root=store)
        assert [i.session_id for i in due] == ["old-one"]
        assert tracelog.session_path("old-one", root=store).is_file()


# --------------------------------------------------------------------------
# Resume never rewrites history
# --------------------------------------------------------------------------


class TestResume:
    def test_resuming_creates_a_new_session_with_a_parent_link(self, store):
        make_session(store, "first")
        child = S.resume("first", root=store)

        assert child != "first"
        assert tracelog.session_path("first", root=store).is_file(), (
            "the original must survive; a resume that overwrote it would destroy "
            "the record the tracelog exists to keep"
        )
        info = {i.session_id: i for i in S.index(root=store)}[child]
        assert info.parent == "first"

    def test_resuming_an_unknown_session_is_refused(self, store):
        with pytest.raises(S.SessionError, match="no session"):
            S.resume("never-existed", root=store)

    def test_context_is_reconstructable(self, store):
        make_session(store, "first")
        context = S.context_for("first", root=store)
        assert "a question" in context
        assert "an answer" in context

    def test_rename_appends_rather_than_rewrites(self, store):
        make_session(store, "first")
        before = len(tracelog.load_session("first", root=store))
        S.rename("first", "the interesting one", root=store)

        after = tracelog.load_session("first", root=store)
        assert len(after) == before + 1, "history is appended to, never edited"
        assert {i.session_id: i for i in S.index(root=store)}["first"].title == (
            "the interesting one"
        )

    def test_export_includes_governance_verdicts(self, store):
        make_session(store, "first", blocked=True)
        exported = S.export("first", root=store)
        assert "governance" in exported
        assert "block" in exported
        assert all(json.loads(line) for line in exported.splitlines())


# --------------------------------------------------------------------------
# Index and search
# --------------------------------------------------------------------------


class TestIndex:
    def test_sessions_are_summarised(self, store):
        make_session(store, "first", role="analyst")
        info = S.index(root=store)[0]
        assert info.session_id == "first"
        assert info.roles == ("analyst",)
        assert info.turns == 1

    def test_a_blocked_session_is_flagged(self, store):
        make_session(store, "bad", blocked=True)
        assert {i.session_id: i for i in S.index(root=store)}["bad"].blocked

    def test_search_filters_by_role(self, store):
        make_session(store, "a", role="analyst")
        make_session(store, "b", role="reviewer")
        assert [i.session_id for i in S.search(role="reviewer", root=store)] == ["b"]

    def test_search_filters_by_text(self, store):
        make_session(store, "alpha")
        make_session(store, "beta")
        assert [i.session_id for i in S.search("alph", root=store)] == ["alpha"]

    def test_most_recent_is_the_newest(self, store):
        make_session(store, "older", age_days=5)
        make_session(store, "newer")
        assert S.most_recent(root=store).session_id == "newer"

    def test_an_empty_store_has_no_sessions(self, store):
        assert S.index(root=store) == []
        assert S.most_recent(root=store) is None
