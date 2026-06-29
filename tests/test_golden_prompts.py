"""PR-K (D-005) — golden-prompt suite: schema + loader + assertion engine.

Per D-005's three-runner-mode design, every mode (CLI, TUI
Diagnostics, scheduled maintenance agent) shares the same loader
and evaluator (``acc.golden_prompts``).  These tests pin the
schema, the loader's behaviour on malformed input, and the
assertion engine — once green here, the CLI runner and any future
TUI runner are automatically correct.

The integration-style "actually run against a live stack" test is
opt-in (``@pytest.mark.e2e``) and skipped in unit CI; see
``docs/DECISIONS.md`` D-005 for how it gets wired into the
nightly job.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from acc.golden_prompts import (
    ExpectsBlock,
    GoldenPrompt,
    GoldenResult,
    add_watch_dir,
    append_run_record,
    append_save_history,
    attached_watch_dirs,
    candidate_from_task,
    diff_versions,
    dump_prompt,
    evaluate,
    export_store,
    format_summary,
    import_store,
    list_versions,
    load_all,
    load_merged,
    load_one,
    load_one_md,
    parse_markdown_prompt,
    read_run_history,
    read_version,
    save_candidate,
    save_prompt,
    version_count,
)


# ---------------------------------------------------------------------------
# PR-Y-2 — markdown import, serialization, capture, watch-dir registry
# ---------------------------------------------------------------------------


class TestMarkdownImport:
    def test_front_matter_and_body(self):
        text = (
            "---\n"
            "target_role: coding_agent\n"
            "expects:\n"
            "  output_contains: ['def ']\n"
            "---\n"
            "Write a Python function that returns the nth Fibonacci number.\n"
        )
        p = parse_markdown_prompt(text, name_hint="fib")
        assert p.name == "fib"
        assert p.target_role == "coding_agent"
        assert "Fibonacci" in p.prompt
        assert p.expects.output_contains == ["def "]

    def test_no_front_matter_defaults(self):
        p = parse_markdown_prompt("Just write a scraper.", name_hint="scrape")
        assert p.name == "scrape"
        assert p.target_role == "coding_agent"  # default
        assert p.prompt == "Just write a scraper."

    def test_front_matter_name_wins_over_hint(self):
        text = "---\nname: explicit\ntarget_role: analyst\n---\nbody\n"
        p = parse_markdown_prompt(text, name_hint="filename_stem")
        assert p.name == "explicit"

    def test_load_one_md_from_file(self, tmp_path):
        f = tmp_path / "smoke.md"
        f.write_text("---\ntarget_role: analyst\n---\nSay hi.\n", encoding="utf-8")
        p = load_one_md(f)
        assert p.name == "smoke"
        assert p.target_role == "analyst"


class TestDumpAndSave:
    def test_dump_roundtrips(self, tmp_path):
        p = GoldenPrompt(name="x", prompt="hello", target_role="analyst")
        path = dump_prompt(p, tmp_path / "x.yaml")
        assert path.is_file()
        assert load_one(path).prompt == "hello"
        # No temp file left behind.
        assert not any(n.name.endswith(".tmp") for n in tmp_path.iterdir())

    def test_save_prompt_to_writable_root(self, tmp_path):
        p = GoldenPrompt(name="mine", prompt="p", target_role="analyst")
        out = save_prompt(p, root=tmp_path)
        assert out == tmp_path / "mine.yaml"
        assert load_one(out).name == "mine"


class TestCapture:
    def test_candidate_from_task_slugifies(self):
        c = candidate_from_task(
            "Write a Python web scraper!", "coding_agent",
        )
        assert c.target_role == "coding_agent"
        assert c.prompt.startswith("Write a Python")
        # slug is lowercase alnum+underscore.
        assert c.name and all(ch.isalnum() or ch == "_" for ch in c.name)

    def test_save_candidate_unique_names(self, tmp_path):
        a = save_candidate("same prompt", "analyst", root=tmp_path)
        b = save_candidate("same prompt", "analyst", root=tmp_path)
        assert a != b
        assert a.is_file() and b.is_file()
        # Both load + are part of a merged view.
        merged = load_merged([tmp_path])
        assert len(merged) == 2


class TestWatchDirs:
    def test_add_and_list(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        store.mkdir()
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(store))
        watch = tmp_path / "watched"
        watch.mkdir()
        add_watch_dir(watch)
        assert Path(watch) in attached_watch_dirs()

    def test_add_is_idempotent(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        store.mkdir()
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(store))
        watch = tmp_path / "watched"
        add_watch_dir(watch)
        add_watch_dir(watch)
        assert attached_watch_dirs().count(Path(watch)) == 1


# ---------------------------------------------------------------------------
# Proposal 044 O2 — save history (VC) + durable export/import
# ---------------------------------------------------------------------------


class TestSaveHistory:
    def test_version_count_increments_per_save(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        assert version_count("p") == 0
        assert append_save_history("p", "v1") == 1
        assert append_save_history("p", "v2") == 2
        # A different name has its own count.
        assert append_save_history("other", "x") == 1
        assert version_count("p") == 2

    def test_history_row_has_hash_and_action(self, tmp_path, monkeypatch):
        import hashlib
        import json

        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        append_save_history("p", "content", action="save")
        rows = [
            json.loads(ln)
            for ln in (tmp_path / "history.jsonl").read_text(
                encoding="utf-8"
            ).splitlines() if ln.strip()
        ]
        assert rows[0]["name"] == "p"
        assert rows[0]["action"] == "save"
        assert rows[0]["sha256"] == hashlib.sha256(b"content").hexdigest()
        assert rows[0]["bytes"] == len(b"content")

    def test_history_write_failure_does_not_raise(self, tmp_path, monkeypatch):
        # Point the writable root at a path whose parent is a FILE so the
        # mkdir fails — append must swallow the OSError, not raise.
        blocker = tmp_path / "afile"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(blocker / "sub"))
        # Returns 0 (count read also fails) but never raises.
        assert append_save_history("p", "v1") == 0

    def test_history_jsonl_not_loaded_as_a_prompt(self, tmp_path, monkeypatch):
        """The history log lives in the writable root but must never be
        picked up by the prompt loader (it's .jsonl, not .yaml/.md)."""
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        append_save_history("p", "v1")
        save_prompt(
            GoldenPrompt(name="real", prompt="hi", target_role="analyst"),
            root=tmp_path,
        )
        merged = load_merged([tmp_path])
        assert [m.name for m in merged] == ["real"]


class TestExportImport:
    def test_export_writes_every_writable_prompt(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        save_prompt(GoldenPrompt(name="a", prompt="A", target_role="analyst"),
                    root=store)
        save_prompt(GoldenPrompt(name="b", prompt="B", target_role="analyst"),
                    root=store)
        dest = tmp_path / "backup"
        n = export_store(dest, root=store)
        assert n == 2
        assert (dest / "a.yaml").is_file() and (dest / "b.yaml").is_file()

    def test_export_skips_history_and_watch_config(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        save_prompt(GoldenPrompt(name="a", prompt="A", target_role="analyst"),
                    root=store)
        (store / "history.jsonl").write_text('{"name":"a"}\n', encoding="utf-8")
        (store / ".watch-dirs").write_text("/somewhere\n", encoding="utf-8")
        dest = tmp_path / "backup"
        export_store(dest, root=store)
        names = sorted(p.name for p in dest.iterdir())
        assert names == ["a.yaml"]

    def test_import_copies_into_writable_store(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "imported.md").write_text(
            "---\ntarget_role: analyst\n---\nDo a thing.\n", encoding="utf-8",
        )
        store = tmp_path / "store"
        store.mkdir()
        n = import_store(src, root=store)
        assert n == 1
        # COPIED into the store as yaml — survives src going away.
        assert (store / "imported.yaml").is_file()

    def test_export_import_roundtrip(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        save_prompt(GoldenPrompt(name="keep", prompt="KEEP",
                                 target_role="analyst"), root=store)
        backup = tmp_path / "backup"
        export_store(backup, root=store)
        # Simulate a volume reset: a brand-new empty store.
        fresh = tmp_path / "fresh"
        fresh.mkdir()
        assert import_store(backup, root=fresh) == 1
        restored = load_merged([fresh])
        assert [p.name for p in restored] == ["keep"]
        assert restored[0].prompt == "KEEP"


# ---------------------------------------------------------------------------
# Proposal G P1 — per-run execution history + prompt version control
# ---------------------------------------------------------------------------


class TestRunHistory:
    def test_result_carries_join_keys(self):
        r = GoldenResult(
            name="x", passed=True, elapsed_ms=1, task_id="t-1", agent_id="a-1",
        )
        assert r.task_id == "t-1" and r.agent_id == "a-1"

    def test_append_and_read_newest_first(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        append_run_record(
            GoldenResult(name="p", passed=True, elapsed_ms=10, task_id="t1"),
            collective_id="sol-01",
        )
        append_run_record(GoldenResult(
            name="p", passed=False, elapsed_ms=20, task_id="t2",
            failures=["latency"],
        ))
        rows = read_run_history("p")
        assert [r["task_id"] for r in rows] == ["t2", "t1"]   # newest-first
        assert rows[0]["passed"] is False and rows[0]["failures"] == ["latency"]
        assert rows[1]["collective_id"] == "sol-01"

    def test_read_filters_by_name_and_limit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        for i in range(3):
            append_run_record(GoldenResult(name="a", passed=True, elapsed_ms=i))
        append_run_record(GoldenResult(name="b", passed=True, elapsed_ms=9))
        assert len(read_run_history("a")) == 3
        assert len(read_run_history("a", limit=2)) == 2
        assert len(read_run_history()) == 4          # no filter → all

    def test_read_empty_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        assert read_run_history("nope") == []

    def test_append_returns_run_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        rid = append_run_record(GoldenResult(name="p", passed=True, elapsed_ms=1))
        assert rid and read_run_history("p")[0]["run_id"] == rid


class TestPromptVersions:
    def test_save_creates_restorable_version_blobs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        assert append_save_history("vc", "name: vc\nv: 1\n") == 1
        assert append_save_history("vc", "name: vc\nv: 2\n") == 2
        assert list_versions("vc") == [1, 2]
        assert "v: 1" in read_version("vc", 1)
        assert "v: 2" in read_version("vc", 2)

    def test_diff_versions(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        append_save_history("d", "line1\nline2\n")
        append_save_history("d", "line1\nCHANGED\n")
        diff = diff_versions("d", 1, 2)
        assert "-line2" in diff and "+CHANGED" in diff

    def test_version_blobs_not_loaded_as_prompts(self, tmp_path, monkeypatch):
        """Version blobs live in a versions/<slug>/ subdir, so the top-level
        prompt loader must never pick them up as prompts."""
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        append_save_history(
            "real", "name: real\nprompt: hi\ntarget_role: analyst\n",
        )
        save_prompt(
            GoldenPrompt(name="real", prompt="hi", target_role="analyst"),
            root=tmp_path,
        )
        assert [m.name for m in load_merged([tmp_path])] == ["real"]

    def test_list_versions_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        assert list_versions("never") == []


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_minimal_prompt_validates(self):
        p = GoldenPrompt.model_validate({
            "name": "minimal",
            "prompt": "hi",
            "target_role": "analyst",
        })
        assert p.name == "minimal"
        assert p.timeout_s == 60.0
        assert p.expects.reply_non_empty is True
        assert p.expects.blocked is False

    def test_unknown_top_level_key_rejected(self):
        with pytest.raises(Exception):
            GoldenPrompt.model_validate({
                "name": "x", "prompt": "p", "target_role": "r",
                "totally_made_up_key": True,
            })

    def test_unknown_expects_key_rejected(self):
        with pytest.raises(Exception):
            GoldenPrompt.model_validate({
                "name": "x", "prompt": "p", "target_role": "r",
                "expects": {"i_made_this_up": True},
            })

    def test_operating_mode_defaults_to_auto(self):
        p = GoldenPrompt.model_validate({
            "name": "x", "prompt": "p", "target_role": "r",
        })
        assert p.operating_mode == "AUTO"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class TestLoader:
    def test_load_all_skips_malformed_files(self, tmp_path):
        good = tmp_path / "good.yaml"
        good.write_text(
            "name: good\nprompt: hi\ntarget_role: analyst\n",
            encoding="utf-8",
        )
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "name: bad\n# missing prompt + target_role\n",
            encoding="utf-8",
        )
        prompts = load_all(tmp_path)
        names = [p.name for p in prompts]
        assert names == ["good"]

    def test_load_all_sorts_by_name(self, tmp_path):
        for name in ("zebra", "alpha", "mango"):
            (tmp_path / f"{name}.yaml").write_text(
                f"name: {name}\nprompt: p\ntarget_role: r\n",
                encoding="utf-8",
            )
        prompts = load_all(tmp_path)
        assert [p.name for p in prompts] == ["alpha", "mango", "zebra"]

    def test_load_all_empty_root(self, tmp_path):
        assert load_all(tmp_path) == []

    def test_load_all_missing_root_warns(self, tmp_path, caplog):
        missing = tmp_path / "nope"
        with caplog.at_level("WARNING", logger="acc.golden_prompts"):
            prompts = load_all(missing)
        assert prompts == []

    def test_load_merged_overrides_by_name(self, tmp_path):
        """A prompt in a later root overrides the same name in an
        earlier one (writable/attached beats shipped)."""
        from acc.golden_prompts import load_merged
        shipped = tmp_path / "shipped"
        writable = tmp_path / "writable"
        shipped.mkdir()
        writable.mkdir()
        (shipped / "dup.yaml").write_text(
            "name: dup\nprompt: shipped\ntarget_role: analyst\n",
            encoding="utf-8",
        )
        (writable / "dup.yaml").write_text(
            "name: dup\nprompt: writable\ntarget_role: analyst\n",
            encoding="utf-8",
        )
        merged = load_merged([shipped, writable])
        assert len(merged) == 1
        assert merged[0].prompt == "writable"

    def test_load_merged_includes_markdown(self, tmp_path):
        from acc.golden_prompts import load_merged
        (tmp_path / "from_md.md").write_text(
            "---\ntarget_role: coding_agent\n---\nWrite a function.\n",
            encoding="utf-8",
        )
        merged = load_merged([tmp_path])
        names = {p.name for p in merged}
        assert "from_md" in names

    def test_shipped_suite_loads(self):
        """PR-Y — the repo ships a golden-prompt suite that must load.

        Guards two regressions at once: (1) someone deleting/renaming
        the shipped YAMLs, and (2) the loader's default repo-anchor
        resolution drifting.  The Diagnostics pane (#9) showed "No
        golden prompts found" in the container because the image didn't
        COPY examples/golden_prompts; this asserts the source-of-truth
        suite is present so the baked image has something to copy."""
        prompts = load_all()  # default root = <repo>/examples/golden_prompts
        names = {p.name for p in prompts}
        assert len(prompts) >= 6, names
        # A couple of stable anchors so a rename is caught.
        assert "smoke_basic_reply" in names
        assert "coding_webscraper_basic" in names


# ---------------------------------------------------------------------------
# Assertion engine
# ---------------------------------------------------------------------------


def _prompt(**expects_overrides) -> GoldenPrompt:
    return GoldenPrompt.model_validate({
        "name": "t",
        "prompt": "p",
        "target_role": "r",
        "expects": expects_overrides,
    })


class TestEvaluate:
    def test_happy_path_all_checks_pass(self):
        prompt = _prompt(
            output_contains=["hello"],
            output_matches_regex="hello\\s+world",
            latency_max_ms=10000,
        )
        result = evaluate(
            prompt,
            {"output": "hello world", "blocked": False, "invocations": []},
            elapsed_ms=200,
        )
        assert result.passed
        assert result.failures == []

    def test_blocked_mismatch_fails(self):
        result = evaluate(
            _prompt(),
            {"output": "x", "blocked": True, "block_reason": "Cat-A"},
            elapsed_ms=10,
        )
        assert not result.passed
        assert any("blocked" in f for f in result.failures)

    def test_empty_reply_fails(self):
        result = evaluate(
            _prompt(),
            {"output": "", "blocked": False},
            elapsed_ms=10,
        )
        assert not result.passed
        assert any("empty" in f for f in result.failures)

    def test_latency_budget_enforced(self):
        result = evaluate(
            _prompt(latency_max_ms=100),
            {"output": "ok", "blocked": False},
            elapsed_ms=500,
        )
        assert not result.passed
        assert any("latency" in f for f in result.failures)

    def test_output_contains_substring_case_insensitive(self):
        prompt = _prompt(output_contains=["IBM", "Yahoo"])
        result = evaluate(
            prompt,
            {"output": "scraping ibm from yahoo finance", "blocked": False},
            elapsed_ms=100,
        )
        assert result.passed

    def test_output_contains_missing_substring_fails(self):
        prompt = _prompt(output_contains=["AAPL"])
        result = evaluate(
            prompt,
            {"output": "this talks about IBM only", "blocked": False},
            elapsed_ms=100,
        )
        assert not result.passed
        assert any("AAPL" in f for f in result.failures)

    def test_regex_pass(self):
        prompt = _prompt(
            output_matches_regex="import\\s+(requests|httpx)",
        )
        result = evaluate(
            prompt,
            {"output": "import requests\nprint('hi')", "blocked": False},
            elapsed_ms=100,
        )
        assert result.passed

    def test_regex_fail(self):
        prompt = _prompt(output_matches_regex="^DELETE")
        result = evaluate(
            prompt,
            {"output": "select * from t", "blocked": False},
            elapsed_ms=100,
        )
        assert not result.passed

    def test_invocations_kind_check(self):
        prompt = _prompt(invocations_kind_contains=["skill"])
        result = evaluate(
            prompt,
            {
                "output": "ok",
                "blocked": False,
                "invocations": [
                    {"kind": "skill", "target": "code_generate", "ok": True},
                ],
            },
            elapsed_ms=100,
        )
        assert result.passed

    def test_invocations_kind_missing_fails(self):
        prompt = _prompt(invocations_kind_contains=["mcp"])
        result = evaluate(
            prompt,
            {
                "output": "ok",
                "blocked": False,
                "invocations": [{"kind": "skill", "target": "x", "ok": True}],
            },
            elapsed_ms=100,
        )
        assert not result.passed

    def test_invocations_target_check(self):
        prompt = _prompt(invocations_target_contains=["code_generate"])
        result = evaluate(
            prompt,
            {
                "output": "ok",
                "blocked": False,
                "invocations": [
                    {"kind": "skill", "target": "code_generate", "ok": True},
                ],
            },
            elapsed_ms=100,
        )
        assert result.passed

    def test_output_excerpt_truncated_at_200(self):
        result = evaluate(
            _prompt(),
            {"output": "x" * 1000, "blocked": False},
            elapsed_ms=10,
        )
        assert len(result.output_excerpt) <= 201
        assert result.output_excerpt.endswith("…")


# ---------------------------------------------------------------------------
# Shipped library
# ---------------------------------------------------------------------------


class TestShippedLibrary:
    """Schema-check the 6 prompts we ship under examples/golden_prompts/.
    A malformed YAML here would silently load_all-skip in production —
    CI fails loudly instead."""

    def test_all_shipped_prompts_load_cleanly(self):
        repo_root = Path(__file__).resolve().parent.parent
        root = repo_root / "examples" / "golden_prompts"
        files = sorted(root.glob("*.yaml"))
        assert files, "expected at least one shipped golden prompt"
        for path in files:
            p = load_one(path)  # raises on schema mismatch
            assert p.name == path.stem, (
                f"prompt name {p.name!r} does not match filename {path.stem!r}"
            )

    def test_shipped_prompts_have_descriptions(self):
        repo_root = Path(__file__).resolve().parent.parent
        prompts = load_all(repo_root / "examples" / "golden_prompts")
        for p in prompts:
            assert p.description.strip(), (
                f"prompt {p.name!r} missing description"
            )


# ---------------------------------------------------------------------------
# format_summary
# ---------------------------------------------------------------------------


class TestFormatSummary:
    def test_empty_results(self):
        assert format_summary([]) == "(no prompts)"

    def test_renders_pass_and_fail_markers(self):
        from acc.golden_prompts import GoldenResult
        results = [
            GoldenResult(name="a", passed=True, elapsed_ms=100),
            GoldenResult(
                name="b", passed=False, elapsed_ms=200,
                failures=["reply was empty"],
            ),
        ]
        text = format_summary(results)
        assert "[PASS] a" in text
        assert "[FAIL] b" in text
        assert "reply was empty" in text
        assert "1/2 passed" in text


# ---------------------------------------------------------------------------
# CLI dispatcher pins (no network — `list`, `validate`, `show`)
# ---------------------------------------------------------------------------


class TestCLIDispatcher:
    def test_e2e_commands_registered(self):
        from acc.cli import _build_parser
        parser = _build_parser()
        # argparse exposes subparsers via the special private attr;
        # parsing `e2e list` should not raise.
        ns = parser.parse_args(["e2e", "list"])
        assert ns.command == "e2e"
        assert ns.e2e_command == "list"

    def test_e2e_validate_exits_zero_on_clean_root(self, tmp_path, capsys):
        (tmp_path / "ok.yaml").write_text(
            "name: ok\nprompt: hi\ntarget_role: analyst\n",
            encoding="utf-8",
        )
        from acc.cli.e2e_cmd import _cmd_validate
        import argparse
        rc = _cmd_validate(argparse.Namespace(root=str(tmp_path)))
        assert rc == 0

    def test_e2e_validate_exits_nonzero_on_bad_yaml(self, tmp_path, capsys):
        (tmp_path / "bad.yaml").write_text(
            "name: bad\n# missing prompt\n",
            encoding="utf-8",
        )
        from acc.cli.e2e_cmd import _cmd_validate
        import argparse
        rc = _cmd_validate(argparse.Namespace(root=str(tmp_path)))
        assert rc == 1

    def test_e2e_list_succeeds_on_empty_root(self, tmp_path, capsys):
        from acc.cli.e2e_cmd import _cmd_list
        import argparse
        rc = _cmd_list(argparse.Namespace(root=str(tmp_path)))
        assert rc == 0

    def test_e2e_run_accepts_history_and_loop_flags(self):
        """PR-O — `run` grows --history and --loop without breaking
        the existing positional/optional args."""
        from acc.cli import _build_parser
        parser = _build_parser()
        ns = parser.parse_args([
            "e2e", "run", "smoke_basic_reply",
            "--history", "test/history/golden.jsonl",
            "--loop", "1800",
        ])
        assert ns.e2e_command == "run"
        assert ns.name == "smoke_basic_reply"
        assert ns.history == "test/history/golden.jsonl"
        assert ns.loop == 1800.0


# ---------------------------------------------------------------------------
# PR-O — persist_results (JSONL history)
# ---------------------------------------------------------------------------


class TestPersistResults:
    def test_appends_one_row_per_result(self, tmp_path):
        from acc.golden_prompts import GoldenResult, persist_results
        import json
        path = tmp_path / "history" / "golden.jsonl"
        results = [
            GoldenResult(name="a", passed=True, elapsed_ms=100),
            GoldenResult(name="b", passed=False, elapsed_ms=200,
                         failures=["empty"]),
        ]
        persist_results(results, path, run_meta={"host": "lighthouse"})
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        row0 = json.loads(lines[0])
        assert row0["name"] == "a"
        assert row0["passed"] is True
        assert row0["host"] == "lighthouse"
        assert "run_ts" in row0

    def test_appends_across_multiple_runs(self, tmp_path):
        from acc.golden_prompts import GoldenResult, persist_results
        path = tmp_path / "golden.jsonl"
        persist_results([GoldenResult(name="a", passed=True, elapsed_ms=1)], path)
        persist_results([GoldenResult(name="a", passed=True, elapsed_ms=2)], path)
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    def test_creates_parent_dir(self, tmp_path):
        from acc.golden_prompts import GoldenResult, persist_results
        path = tmp_path / "deeply" / "nested" / "golden.jsonl"
        persist_results([GoldenResult(name="a", passed=True, elapsed_ms=1)], path)
        assert path.is_file()

    def test_write_failure_does_not_raise(self, tmp_path, monkeypatch):
        """A broken history path must not crash a scheduled run."""
        from acc.golden_prompts import GoldenResult, persist_results
        # Point at a path whose parent can't be created (a file, not a dir).
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        bad_path = blocker / "sub" / "golden.jsonl"
        # Should log + return, not raise.
        persist_results(
            [GoldenResult(name="a", passed=True, elapsed_ms=1)], bad_path,
        )


class TestRunEnrichment:
    """Proposal G P2 — per-run signals (tokens/compliance/verdict) flow onto
    GoldenResult and into the run-history record."""

    def test_record_carries_p2_signals(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        append_run_record(GoldenResult(
            name="p", passed=True, elapsed_ms=12, task_id="tk-9",
            input_tokens=140, cache_read_tokens=20,
            compliance_health_score=0.92, eval_verdict="GOOD",
        ), collective_id="sol-01")
        row = read_run_history("p")[0]
        assert row["input_tokens"] == 140
        assert row["cache_read_tokens"] == 20
        assert row["compliance_health_score"] == 0.92
        assert row["eval_verdict"] == "GOOD"

    def test_defaults_are_unreported_sentinels(self):
        r = GoldenResult(name="x", passed=True, elapsed_ms=1)
        assert r.input_tokens == 0
        assert r.compliance_health_score == -1.0
        assert r.eval_verdict == ""
