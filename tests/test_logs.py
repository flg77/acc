"""Reading across the collective instead of one container at a time.

The command exists to replace a six-terminal sweep, so the tests are about the
things that sweep gets wrong when done by hand:

* a task's lines are spread across agents and must come back **in time order**,
  not grouped by whichever container was read first;
* half the collective is often down when someone runs this, so a missing source
  must be **reported**, never fatal;
* tracebacks are multi-line and unstructured, and dropping what does not parse
  would discard exactly the output an incident needs.
"""

from __future__ import annotations

import pytest

from acc import logs as L
from acc.logs import LogLine, Query


def line(text, origin="acc-analyst", source="container"):
    return L.parse_line(text, source, origin)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


class TestParsing:
    def test_a_standard_acc_line_yields_time_and_level(self):
        parsed = line("2026-08-20 11:33:42,123 WARNING acc.agent: something odd")
        assert parsed.level == "WARNING"
        assert parsed.ts is not None

    def test_an_unparseable_line_is_kept(self):
        """Tracebacks are the output that matters most and parse worst."""
        parsed = line('  File "acc/agent.py", line 42, in _handle_task')
        assert parsed.ts is None
        assert parsed.level == "INFO"
        assert "agent.py" in parsed.text

    def test_iso_timestamps_are_understood(self):
        assert line("2026-08-20T11:33:42 ERROR boom").ts is not None

    def test_sub_second_precision_is_kept(self):
        a = line("2026-08-20 11:33:42,100 INFO first")
        b = line("2026-08-20 11:33:42,900 INFO second")
        assert a.ts < b.ts, "same-second lines must still order correctly"


# --------------------------------------------------------------------------
# Merging
# --------------------------------------------------------------------------


class TestMerge:
    def test_lines_from_several_agents_interleave_by_time(self):
        collected = [
            line("2026-08-20 11:00:03 INFO third", origin="acc-analyst"),
            line("2026-08-20 11:00:01 INFO first", origin="acc-ingester"),
            line("2026-08-20 11:00:02 INFO second", origin="acc-arbiter"),
        ]
        merged = L.merge(collected, Query())
        assert [m.text.split()[-1] for m in merged] == ["first", "second", "third"]

    def test_undated_lines_sort_last_not_first(self):
        """An unparsed traceback belongs beside its error, not at the top.

        Sorting missing timestamps as 0 would put every traceback at the start
        of the report, reading as the oldest thing that happened.
        """
        collected = [
            line("no timestamp here"),
            line("2026-08-20 11:00:01 INFO real"),
        ]
        merged = L.merge(collected, Query())
        assert merged[0].text.endswith("real")
        assert merged[-1].text == "no timestamp here"

    def test_the_limit_keeps_the_most_recent(self):
        collected = [
            line(f"2026-08-20 11:00:{i:02d} INFO line{i}") for i in range(10)
        ]
        merged = L.merge(collected, Query(limit=3))
        assert [m.text.split()[-1] for m in merged] == ["line7", "line8", "line9"]


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


class TestFilters:
    def test_task_filter_crosses_agents(self):
        """The filter that earns the command."""
        collected = [
            line("2026-08-20 11:00:01 INFO task t-42 assigned", origin="acc-ingester"),
            line("2026-08-20 11:00:02 INFO task t-99 assigned", origin="acc-analyst"),
            line("2026-08-20 11:00:03 INFO task t-42 complete", origin="acc-arbiter"),
        ]
        merged = L.merge(collected, Query(task="t-42"))
        assert len(merged) == 2
        assert {m.origin for m in merged} == {"acc-ingester", "acc-arbiter"}

    def test_role_filters_on_the_origin_not_the_text(self):
        collected = [
            line("2026-08-20 11:00:01 INFO mentions analyst", origin="acc-ingester"),
            line("2026-08-20 11:00:02 INFO unrelated", origin="acc-analyst"),
        ]
        merged = L.merge(collected, Query(role="analyst"))
        assert [m.origin for m in merged] == ["acc-analyst"]

    def test_level_filters_below_the_threshold(self):
        collected = [
            line("2026-08-20 11:00:01 DEBUG noisy"),
            line("2026-08-20 11:00:02 ERROR real"),
        ]
        merged = L.merge(collected, Query(level="WARNING"))
        assert [m.level for m in merged] == ["ERROR"]

    def test_an_unparsed_line_survives_a_level_filter_at_info(self):
        merged = L.merge([line("raw traceback text")], Query(level="INFO"))
        assert len(merged) == 1

    @pytest.mark.parametrize(
        "text,expected",
        [("30m", 1800), ("2h", 7200), ("90s", 90), ("1d", 86400), ("120", 120)],
    )
    def test_since_parsing(self, text, expected):
        assert L.parse_since(text) == expected

    @pytest.mark.parametrize("text", ["", "later", "abcm"])
    def test_unparseable_since_is_none_not_an_error(self, text):
        assert L.parse_since(text) is None


# --------------------------------------------------------------------------
# Missing sources
# --------------------------------------------------------------------------


class TestMissingSourcesAreReported:
    def test_an_absent_runtime_is_reported_not_raised(self):
        """Half the collective down is exactly when this gets run."""
        report = L.gather(Query(sources=("container",)), runtime="not-a-real-runtime")
        assert report.lines == []
        assert "container" in report.unavailable
        assert "not-a-real-runtime" in report.unavailable["container"]

    def test_a_failing_container_does_not_lose_the_others(self, monkeypatch):
        monkeypatch.setattr(L, "container_names", lambda runtime="podman": ["acc-a", "acc-b"])

        class Result:
            def __init__(self, rc, out, err):
                self.returncode, self.stdout, self.stderr = rc, out, err

        def fake_run(argv, **kw):
            if argv[-1] == "acc-a":
                return Result(1, "", "container is gone")
            return Result(0, "2026-08-20 11:00:01 INFO from b", "")

        monkeypatch.setattr(L.subprocess, "run", fake_run)
        lines, unavailable = L.collect_container(Query())

        assert [x.origin for x in lines] == ["acc-b"]
        assert "acc-a" in unavailable

    def test_no_containers_is_reported_clearly(self, monkeypatch):
        monkeypatch.setattr(L, "container_names", lambda runtime="podman": [])
        _, unavailable = L.collect_container(Query())
        assert "no ACC containers" in unavailable["container"]

    def test_gather_never_raises_with_nothing_available(self):
        report = L.gather(Query(), runtime="not-a-real-runtime")
        assert isinstance(report.unavailable, dict)
        assert report.as_dict()["count"] == 0


# --------------------------------------------------------------------------
# Sources stay distinct
# --------------------------------------------------------------------------


class TestSourcesAreLabelled:
    def test_every_line_carries_its_source(self):
        collected = [
            LogLine(1.0, "container", "acc-a", "INFO", "stack trace"),
            LogLine(2.0, "tracelog", "sess-1", "INFO", '{"verdict": "allow"}'),
        ]
        merged = L.merge(collected, Query())
        assert [m.source for m in merged] == ["container", "tracelog"]

    def test_a_source_can_be_excluded(self, monkeypatch):
        monkeypatch.setattr(
            L, "collect_container", lambda q, runtime="podman": ([], {"container": "skipped"})
        )
        report = L.gather(Query(sources=("tracelog",)))
        assert "container" not in report.unavailable

    def test_the_json_report_round_trips(self):
        import json

        report = L.Report(lines=[LogLine(1.0, "container", "acc-a", "INFO", "x")])
        data = json.loads(json.dumps(report.as_dict()))
        assert data["lines"][0]["source"] == "container"
        assert data["count"] == 1
