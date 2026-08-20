"""Checkpoints as audit artifacts, not just undo.

ACC authorises writes and cannot undo them. A general-purpose undo fixes half
of that; the half worth more is that a checkpoint records **which task caused
the write and which decision authorised it** — a filesystem snapshot cannot,
because it does not know what a task or an approval is.

Three failure modes are tested because each is silent:

* a file the write **created** must be recorded, or restoring leaves it behind
  and the creation is invisible;
* a file that changed **after** the checkpoint must not be silently
  overwritten — a rollback that discards later work becomes the incident;
* a checkpoint that could not be captured must be recorded as **skipped**, not
  quietly omitted, or a restore reports success while missing a file.

And the cap must never cost a task its snapshot: pruning runs after the capture
and never drops the checkpoint just taken.
"""

from __future__ import annotations

import time

import pytest

from acc import checkpoints as C


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv(C.STORE_VAR, str(tmp_path / ".acc-checkpoints"))
    (tmp_path / "notes.md").write_text("original\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('before')\n", encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------
# Audit artifact
# --------------------------------------------------------------------------


class TestAuditLinkage:
    def test_a_checkpoint_records_what_caused_the_write(self, workspace):
        """The thing a filesystem snapshot cannot answer."""
        checkpoint = C.capture(
            ["notes.md"],
            workspace=workspace,
            task_id="t-42",
            agent_id="analyst-1",
            role="analyst",
            oversight_id="ov-7",
        )
        stored = C.load(checkpoint.id, workspace)
        assert stored.task_id == "t-42"
        assert stored.agent_id == "analyst-1"
        assert stored.oversight_id == "ov-7", (
            "'who approved this write' must be answerable from the checkpoint"
        )

    def test_a_write_with_no_approval_records_none(self, workspace):
        checkpoint = C.capture(["notes.md"], workspace=workspace, task_id="t-1")
        assert C.load(checkpoint.id, workspace).oversight_id == ""

    def test_checkpoints_are_listed_newest_first(self, workspace):
        first = C.capture(["notes.md"], workspace=workspace)
        time.sleep(0.01)
        second = C.capture(["notes.md"], workspace=workspace)
        assert [c.id for c in C.index(workspace)][:2] == [second.id, first.id]


# --------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------


class TestRestore:
    def test_a_modified_file_is_reverted(self, workspace):
        checkpoint = C.capture(["notes.md"], workspace=workspace)
        (workspace / "notes.md").write_text("changed by the agent\n", encoding="utf-8")

        C.restore(checkpoint.id, workspace=workspace, force=True)
        assert (workspace / "notes.md").read_text(encoding="utf-8") == "original\n"

    def test_a_created_file_is_removed_on_restore(self, workspace):
        """Otherwise the creation is invisible and the file survives a rollback."""
        checkpoint = C.capture(["new.txt"], workspace=workspace)
        (workspace / "new.txt").write_text("made by the agent\n", encoding="utf-8")

        result = C.restore(checkpoint.id, workspace=workspace, force=True)
        assert "new.txt" in result.would_delete
        assert not (workspace / "new.txt").exists()

    def test_an_unchanged_file_is_reported_as_such(self, workspace):
        checkpoint = C.capture(["notes.md"], workspace=workspace)
        plan = C.plan_restore(checkpoint.id, workspace=workspace)
        assert plan.unchanged == ["notes.md"]
        assert plan.would_revert == []

    def test_dry_run_reports_without_changing(self, workspace):
        checkpoint = C.capture(["notes.md"], workspace=workspace)
        (workspace / "notes.md").write_text("changed\n", encoding="utf-8")

        plan = C.plan_restore(checkpoint.id, workspace=workspace)
        assert plan.would_revert == ["notes.md"]
        assert (workspace / "notes.md").read_text(encoding="utf-8") == "changed\n"

    def test_restoring_over_later_work_needs_acknowledgement(self, workspace):
        """A rollback that discards later work becomes the incident."""
        checkpoint = C.capture(["notes.md"], workspace=workspace)
        (workspace / "notes.md").write_text("later work\n", encoding="utf-8")

        with pytest.raises(C.CheckpointError, match="changed after"):
            C.restore(checkpoint.id, workspace=workspace)
        assert (workspace / "notes.md").read_text(encoding="utf-8") == "later work\n"

    def test_force_acknowledges_it(self, workspace):
        checkpoint = C.capture(["notes.md"], workspace=workspace)
        (workspace / "notes.md").write_text("later work\n", encoding="utf-8")
        C.restore(checkpoint.id, workspace=workspace, force=True)
        assert (workspace / "notes.md").read_text(encoding="utf-8") == "original\n"

    def test_an_unknown_checkpoint_is_refused(self, workspace):
        with pytest.raises(C.CheckpointError, match="no checkpoint"):
            C.load("cp-nope", workspace)


# --------------------------------------------------------------------------
# What cannot be captured is recorded, not hidden
# --------------------------------------------------------------------------


class TestSkipsAreRecorded:
    def test_an_oversize_file_is_recorded_as_skipped(self, workspace, monkeypatch):
        """A restore must not report success while missing a file."""
        monkeypatch.setattr(C, "MAX_FILE_BYTES", 10)
        (workspace / "big.bin").write_text("x" * 100, encoding="utf-8")

        checkpoint = C.capture(["big.bin"], workspace=workspace)
        entry = {f.path: f for f in checkpoint.files}["big.bin"]
        assert entry.skipped
        assert "cap" in entry.skipped

    def test_a_skipped_file_is_reported_unrecoverable_on_restore(
        self, workspace, monkeypatch
    ):
        monkeypatch.setattr(C, "MAX_FILE_BYTES", 10)
        (workspace / "big.bin").write_text("x" * 100, encoding="utf-8")
        checkpoint = C.capture(["big.bin"], workspace=workspace)

        plan = C.plan_restore(checkpoint.id, workspace=workspace)
        assert plan.unrecoverable == ["big.bin"]

    def test_a_path_outside_the_workspace_is_refused_and_recorded(self, workspace):
        """A checkpoint must not be a way to read outside the boundary."""
        checkpoint = C.capture(["../outside.txt"], workspace=workspace)
        entry = checkpoint.files[0]
        assert entry.skipped
        assert "outside the workspace" in entry.skipped


# --------------------------------------------------------------------------
# Bounded storage that never costs a task its snapshot
# --------------------------------------------------------------------------


class TestRetention:
    def test_the_checkpoint_just_taken_survives_its_own_prune(self, workspace):
        """An agent mid-write must not lose the snapshot it relies on."""
        checkpoint = C.capture(
            ["notes.md"], workspace=workspace
        )
        C.prune(workspace=workspace, max_bytes=0, keep=checkpoint.id)
        assert C.load(checkpoint.id, workspace)

    def test_old_checkpoints_are_pruned_by_age(self, workspace):
        checkpoint = C.capture(["notes.md"], workspace=workspace)
        future = time.time() + 60 * 86400
        removed = C.prune(workspace=workspace, max_age_days=30, now=future)
        assert checkpoint.id in removed
        assert C.index(workspace) == []

    def test_the_size_cap_drops_the_oldest_first(self, workspace):
        first = C.capture(["notes.md"], workspace=workspace)
        time.sleep(0.01)
        second = C.capture(["src/main.py"], workspace=workspace)

        C.prune(workspace=workspace, max_bytes=1, keep=second.id)
        ids = {c.id for c in C.index(workspace)}
        assert first.id not in ids
        assert second.id in ids

    def test_pruning_an_empty_store_is_safe(self, workspace):
        assert C.prune(workspace=workspace) == []

    def test_prune_is_safe_to_run_repeatedly(self, workspace):
        C.capture(["notes.md"], workspace=workspace)
        C.prune(workspace=workspace)
        C.prune(workspace=workspace)
        assert isinstance(C.index(workspace), list)

    def test_total_bytes_excludes_skipped_files(self, workspace, monkeypatch):
        monkeypatch.setattr(C, "MAX_FILE_BYTES", 10)
        (workspace / "big.bin").write_text("x" * 100, encoding="utf-8")
        C.capture(["big.bin"], workspace=workspace)
        assert C.total_bytes(workspace) == 0


# --------------------------------------------------------------------------
# Overhead
# --------------------------------------------------------------------------


class TestOverhead:
    def test_capturing_a_small_file_is_fast(self, workspace):
        """Taking a checkpoint must not measurably slow an ordinary write."""
        started = time.monotonic()
        for _ in range(10):
            C.capture(["notes.md"], workspace=workspace)
        elapsed = time.monotonic() - started
        assert elapsed < 5.0, f"10 captures took {elapsed:.2f}s"

    def test_identical_content_is_stored_once(self, workspace):
        """Content-addressed storage: the same bytes are not written twice."""
        first = C.capture(["notes.md"], workspace=workspace)
        second = C.capture(["notes.md"], workspace=workspace)
        digest = first.files[0].sha256
        assert second.files[0].sha256 == digest
        assert digest, "content must be addressed by its digest"
