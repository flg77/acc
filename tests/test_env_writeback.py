"""Tests for the TUI's .env upsert helper.

`upsert_env()` is the single point of truth for the TUI Configuration
write-back: it must preserve comments / ordering / blank lines,
uncomment example lines for known keys, write atomically with a
backup, and quote tricky values safely.
"""

from __future__ import annotations

from pathlib import Path

from acc.tui.env_writeback import _quote, upsert_env


class TestQuote:
    def test_plain_value_unquoted(self):
        assert _quote("anthropic") == "anthropic"
        assert _quote("http://host.containers.internal:8001/v1") == \
            "http://host.containers.internal:8001/v1"
        assert _quote("") == ""

    def test_value_with_space_is_quoted(self):
        assert _quote("a b") == '"a b"'

    def test_value_with_special_chars_is_escaped(self):
        assert _quote("a$b") == '"a\\$b"'
        assert _quote('he said "hi"') == '"he said \\"hi\\""'


class TestUpsertEnv:
    def test_creates_file_when_missing(self, tmp_path: Path):
        target = tmp_path / ".env"
        upsert_env(target, {"ACC_LLM_BACKEND": "anthropic"})
        assert target.read_text() == "ACC_LLM_BACKEND=anthropic\n"

    def test_replaces_existing_uncommented_key(self, tmp_path: Path):
        target = tmp_path / ".env"
        target.write_text(
            "# header\n"
            "ACC_LLM_BACKEND=openai_compat\n"
            "REDIS_PASSWORD=hunter2\n"
        )
        upsert_env(target, {"ACC_LLM_BACKEND": "anthropic"})
        out = target.read_text()
        assert "ACC_LLM_BACKEND=anthropic\n" in out
        assert "openai_compat" not in out
        # Surrounding lines preserved verbatim.
        assert "# header\n" in out
        assert "REDIS_PASSWORD=hunter2\n" in out

    def test_uncomments_example_line(self, tmp_path: Path):
        target = tmp_path / ".env"
        target.write_text(
            "# header\n"
            "# ACC_LLM_BACKEND=anthropic\n"
            "REDIS_PASSWORD=hunter2\n"
        )
        upsert_env(target, {"ACC_LLM_BACKEND": "vllm"})
        out = target.read_text()
        # Comment is gone — the line is now active.
        assert "ACC_LLM_BACKEND=vllm\n" in out
        assert "# ACC_LLM_BACKEND" not in out

    def test_appends_unseen_key(self, tmp_path: Path):
        target = tmp_path / ".env"
        target.write_text("# header\nREDIS_PASSWORD=hunter2\n")
        upsert_env(target, {"ACC_LLM_BACKEND": "anthropic"})
        out = target.read_text()
        # Original lines preserved at the top.
        assert out.startswith("# header\nREDIS_PASSWORD=hunter2\n")
        # New key landed at the end after a blank separator.
        assert out.endswith("\nACC_LLM_BACKEND=anthropic\n")

    def test_preserves_ordering_of_unrelated_lines(self, tmp_path: Path):
        target = tmp_path / ".env"
        original = (
            "# top\n"
            "A=1\n"
            "B=2\n"
            "# block\n"
            "C=3\n"
        )
        target.write_text(original)
        upsert_env(target, {"B": "two"})
        out = target.read_text()
        # Lines are in the same order; only B's value changed.
        lines = out.splitlines()
        assert lines[0] == "# top"
        assert lines[1] == "A=1"
        assert lines[2] == "B=two"
        assert lines[3] == "# block"
        assert lines[4] == "C=3"

    def test_writes_backup(self, tmp_path: Path):
        target = tmp_path / ".env"
        target.write_text("OLD=value\n")
        upsert_env(target, {"OLD": "new"})
        backup = target.with_suffix(target.suffix + ".bak")
        assert backup.exists()
        assert backup.read_text() == "OLD=value\n"

    def test_atomic_no_temp_left_behind(self, tmp_path: Path):
        target = tmp_path / ".env"
        upsert_env(target, {"A": "1", "B": "2"})
        # No .env.tmp.* file should remain.
        assert not list(tmp_path.glob(".env.tmp.*"))

    def test_multiple_keys_one_call(self, tmp_path: Path):
        target = tmp_path / ".env"
        target.write_text("# example\n# ACC_LLM_BACKEND=anthropic\n")
        upsert_env(target, {
            "ACC_LLM_BACKEND": "vllm",
            "ACC_LLM_MODEL": "qwen3-1.7B",
            "ACC_LLM_BASE_URL": "http://x:8001/v1",
        })
        out = target.read_text()
        assert "ACC_LLM_BACKEND=vllm\n" in out
        assert "ACC_LLM_MODEL=qwen3-1.7B\n" in out
        assert "ACC_LLM_BASE_URL=http://x:8001/v1\n" in out

    def test_quotes_value_with_space(self, tmp_path: Path):
        target = tmp_path / ".env"
        upsert_env(target, {"ACC_LLM_MODEL": "model name with spaces"})
        assert 'ACC_LLM_MODEL="model name with spaces"' in target.read_text()
