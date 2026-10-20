"""``acc-cli rdf`` and ``acc-cli lessons show --as`` (RDF-01)."""

from __future__ import annotations

import json

import pytest

from acc.cli import _build_parser
from acc.cli import lessons_cmd
from acc.signals import redis_lesson_key


def _run(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


def test_vocab_prints_turtle_with_the_ceiling_scale(capsys):
    assert _run(["rdf", "vocab"]) == 0
    out = capsys.readouterr().out
    assert "@prefix acc: <urn:acc:vocab:> ." in out
    assert "acc:CRITICAL" in out and "acc:rank" in out


def test_context_is_json(capsys):
    assert _run(["rdf", "context"]) == 0
    assert json.loads(capsys.readouterr().out)["@context"]["prov"] == "http://www.w3.org/ns/prov#"


def test_convert_task_keyed_and_unkeyed(tmp_path, capsys, monkeypatch):
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"task_id": "t1", "requester_source": "webgui",
                                "requester_subject": "alice", "requester_ceiling": "LOW"}))
    monkeypatch.delenv("ACC_RDF_PERSON_KEY", raising=False)
    assert _run(["rdf", "convert", "task", str(task), "--collective", "c1",
                 "--format", "ntriples"]) == 0
    unkeyed = capsys.readouterr().out
    assert "_:person0" in unkeyed and "urn:acc:person:" not in unkeyed

    monkeypatch.setenv("ACC_RDF_PERSON_KEY", "k")
    assert _run(["rdf", "convert", "task", str(task), "--collective", "c1"]) == 0
    keyed = capsys.readouterr().out
    assert "urn:acc:person:" in keyed and "alice" not in keyed


def test_convert_okf_uses_the_parent_dir_as_bundle(tmp_path, capsys):
    bundle = tmp_path / "lending"
    bundle.mkdir()
    (bundle / "lp-7.md").write_text("---\ntype: policy\ntitle: LP-7\n---\nSee [LP-6](lp-6.md).\n",
                                     encoding="utf-8")
    assert _run(["rdf", "convert", "okf", str(bundle / "lp-7.md")]) == 0
    out = capsys.readouterr().out
    assert "<urn:acc:okf:lending:lp-7.md>" in out
    assert "dcterms:references <urn:acc:okf:lending:lp-6.md>" in out


def test_convert_bad_input_exits_1(tmp_path, capsys):
    bad = tmp_path / "lesson.json"
    bad.write_text(json.dumps({"summary": "no id"}))
    assert _run(["rdf", "convert", "lesson", str(bad)]) == 1
    assert "cannot convert" in capsys.readouterr().err


class _Redis:
    def __init__(self, data: dict[str, str]) -> None:
        self._data = data

    def get(self, key):
        return self._data.get(key)


@pytest.mark.parametrize("fmt, marker", [("turtle", "a acc:Lesson"),
                                         ("jsonld", '"acc:Lesson"'),
                                         ("ntriples", "<urn:acc:vocab:Lesson>")])
def test_lessons_show_as_rdf(monkeypatch, capsys, fmt, marker):
    lesson = {"lesson_id": "abc123", "from_agent": "a1", "summary": "s", "ceiling": "LOW"}
    redis = _Redis({redis_lesson_key("c1", "abc123"): json.dumps(lesson)})
    monkeypatch.setattr(lessons_cmd, "_redis", lambda: redis)
    assert _run(["lessons", "show", "abc123", "--collective", "c1", "--as", fmt]) == 0
    out = capsys.readouterr().out
    assert marker in out
    # A stored lesson without collective_id is filed under the one it was read from.
    assert "urn:acc:collective:c1:lesson:abc123" in out
