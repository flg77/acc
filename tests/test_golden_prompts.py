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
    evaluate,
    format_summary,
    load_all,
    load_one,
)


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
