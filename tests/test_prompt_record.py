"""Model-visible-means-logged invariant (ACC Roadmap DS-01).

The invariant is only worth anything if a bypass is impossible rather than
merely discouraged, so these tests attack it from both ends:

* the digest actually distinguishes corpora (including the framing bug that
  makes ``("ab","c")`` and ``("a","bc")`` collide);
* **every** backend the factory builds records — including the paths that
  had no audit record at all before this change (failover chain clients,
  memory reflection, the operator CLI), because they all construct through
  ``acc.config.build_llm_backend``;
* the record reaches the tamper-evident audit chain and is covered by its
  evidence hash.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

import pytest

from acc import prompt_record
from acc.audit import AuditRecord, _compute_evidence_hash
from acc.prompt_record import (
    corpus_sha256,
    drain,
    record,
    recording_backend,
    snapshot,
)


@pytest.fixture(autouse=True)
def _clean_ledger(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_RECORD_FULL", raising=False)
    prompt_record.reset()
    yield
    prompt_record.reset()


class _FakeBackend:
    """Minimal LLMBackend double with an extra attribute to prove passthrough."""

    model = "fake-model-7b"

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def complete(self, system, user, response_schema=None, cache_prefix=False):
        self.calls.append((system, user, response_schema, cache_prefix))
        return {"content": "ok", "usage": {"total_tokens": 3}}

    async def embed(self, text):
        return [0.0, 1.0]


# ---------------------------------------------------------------------------
# The digest
# ---------------------------------------------------------------------------


def test_digest_is_stable_and_part_sensitive() -> None:
    a = corpus_sha256("sys", "usr")
    assert a == corpus_sha256("sys", "usr")
    assert a != corpus_sha256("sys", "usr!")
    assert a != corpus_sha256("sys!", "usr")


def test_digest_framing_prevents_boundary_collision() -> None:
    """Naive concatenation would make these two corpora identical.

    They are genuinely different — different system prompt, different user
    content — in a record whose whole purpose is telling corpora apart.
    """
    assert corpus_sha256("ab", "c") != corpus_sha256("a", "bc")


def test_empty_parts_are_still_distinguishable() -> None:
    assert corpus_sha256("", "x") != corpus_sha256("x", "")
    assert corpus_sha256("", "") != corpus_sha256("", "x")


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def test_digest_only_by_default() -> None:
    rec = record("secret system", "secret user", source="test")
    assert rec.system_text == ""
    assert rec.user_text == ""
    # ...but the corpus is still provable against a candidate reconstruction.
    assert rec.corpus_sha256 == corpus_sha256("secret system", "secret user")
    assert rec.system_chars == len("secret system")


def test_full_retention_is_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("ACC_PROMPT_RECORD_FULL", "1")
    rec = record("sys text", "usr text", source="test")
    assert rec.system_text == "sys text"
    assert rec.user_text == "usr text"


# ---------------------------------------------------------------------------
# The enforcement point
# ---------------------------------------------------------------------------


def test_wrapper_records_before_dispatch_and_passes_through() -> None:
    base = _FakeBackend()
    wrapped = recording_backend(base, source="unit")

    out = asyncio.run(wrapped.complete("SYS", "USR", None, True))

    assert out["content"] == "ok"
    assert base.calls == [("SYS", "USR", None, True)], "args must pass through"
    pending = snapshot()
    assert len(pending) == 1
    assert pending[0].corpus_sha256 == corpus_sha256("SYS", "USR")
    assert pending[0].source == "unit"
    assert pending[0].model == "fake-model-7b"


def test_wrapper_preserves_the_cache_prefix_probe() -> None:
    """`_call_llm` probes for the cache_prefix kwarg and falls back on TypeError.

    A wrapper that re-declared complete()'s signature would absorb that probe
    and silently disable prompt caching for every legacy backend.
    """

    class _LegacyBackend:
        model = "legacy"

        def __init__(self):
            self.calls = []

        async def complete(self, system, user):  # no cache_prefix
            self.calls.append((system, user))
            return {"content": "ok"}

    wrapped = recording_backend(_LegacyBackend(), source="unit")
    with pytest.raises(TypeError):
        asyncio.run(wrapped.complete("S", "U", cache_prefix=True))
    # The fallback call still works, and both attempts recorded.
    assert asyncio.run(wrapped.complete("S", "U"))["content"] == "ok"


def test_wrapper_delegates_unknown_attributes() -> None:
    wrapped = recording_backend(_FakeBackend(), source="unit")
    assert wrapped.model == "fake-model-7b"
    assert asyncio.run(wrapped.embed("x")) == [0.0, 1.0]


def test_wrapping_is_idempotent() -> None:
    """A double wrap would double-record one call."""
    once = recording_backend(_FakeBackend(), source="unit")
    twice = recording_backend(once, source="unit")
    assert once is twice
    asyncio.run(twice.complete("S", "U"))
    assert len(snapshot()) == 1


# ---------------------------------------------------------------------------
# No bypass: the factory is the choke point
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "backend_name",
    ["ollama", "anthropic", "vllm", "openai_compat", "llama_stack"],
)
def test_every_factory_built_backend_records(backend_name, monkeypatch) -> None:
    """build_llm_backend is the ONE place ACC builds an LLM client.

    llm_failover.backend_for_entry routes through it deliberately, so
    covering it here covers the failover chain, memory reflection and the
    operator CLI -- the three paths that reached a model with no audit
    record at all before this change.
    """
    from acc import config as config_mod
    from acc.prompt_record import _RecordingLLMBackend

    monkeypatch.setattr(
        config_mod, "_build_llm_backend_unrecorded", lambda cfg: _FakeBackend()
    )
    cfg = object()  # never inspected -- the concrete build is stubbed out
    built = config_mod.build_llm_backend(cfg)

    assert isinstance(built, _RecordingLLMBackend), (
        f"{backend_name}: factory returned an unwrapped backend -- "
        "the model-visible invariant has a hole"
    )
    asyncio.run(built.complete("S", "U"))
    assert len(snapshot()) == 1


def test_cli_builder_records_too() -> None:
    """`acc-cli llm` is ACC's SECOND construction site.

    It deliberately mirrors the factory branch-by-branch to keep LanceDB and
    pymilvus out of the CLI image, so it does NOT inherit the factory's
    enforcement and needs its own wrapper.
    """
    from acc.cli import llm_cmd
    from acc.prompt_record import _RecordingLLMBackend

    original = llm_cmd._build_llm_only_unrecorded
    try:
        llm_cmd._build_llm_only_unrecorded = lambda cfg: _FakeBackend()
        built = llm_cmd._build_llm_only(object())
    finally:
        llm_cmd._build_llm_only_unrecorded = original

    assert isinstance(built, _RecordingLLMBackend)
    asyncio.run(built.complete("S", "U"))
    assert snapshot()[0].source == "cli"


def test_no_third_unrecorded_construction_site() -> None:
    """Pin the set of places ACC instantiates a concrete LLM backend.

    Every concrete backend class is imported at exactly two sites, both of
    which wrap their result.  A new site would silently reopen the hole this
    module exists to close.
    """
    import pathlib

    repo = pathlib.Path(__file__).resolve().parent.parent
    allowed = {
        pathlib.Path("acc/config.py"),
        pathlib.Path("acc/cli/llm_cmd.py"),
    }
    offenders: list[str] = []
    for path in (repo / "acc").rglob("*.py"):
        rel = path.relative_to(repo)
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for cls in (
            "OllamaBackend", "AnthropicBackend", "VLLMBackend",
            "OpenAICompatBackend", "LlamaStackBackend",
        ):
            if f"import {cls}" in text:
                offenders.append(f"{rel} imports {cls}")
    assert not offenders, (
        "new LLM construction site(s) outside the two wrapped builders — "
        "these would bypass prompt recording:\n  " + "\n  ".join(offenders)
    )


def test_failover_entry_builder_goes_through_the_factory() -> None:
    """Guards the comment in backend_for_entry that this change relies on."""
    import inspect

    from acc import llm_failover

    src = inspect.getsource(llm_failover.backend_for_entry)
    assert "build_llm_backend" in src, (
        "backend_for_entry no longer routes through build_llm_backend -- "
        "failover clients would bypass prompt recording"
    )


# ---------------------------------------------------------------------------
# Reaching the tamper-evident chain
# ---------------------------------------------------------------------------


def test_prompt_records_are_covered_by_the_evidence_hash() -> None:
    """Tampering with the recorded corpus must break the audit hash."""
    rec = record("SYS", "USR", source="test")
    audit = AuditRecord(task_id="t1", prompt_records=[rec.as_dict()])
    original = _compute_evidence_hash(audit)

    audit.prompt_records[0]["corpus_sha256"] = "0" * 64
    assert _compute_evidence_hash(audit) != original


def test_audit_record_serialises_with_prompt_records() -> None:
    """AuditBroker.record() json-dumps asdict(rec) -- it must not choke."""
    rec = record("SYS", "USR", source="test")
    audit = AuditRecord(task_id="t1", prompt_records=[rec.as_dict()])
    blob = json.dumps(asdict(audit), sort_keys=True, separators=(",", ":"))
    assert rec.corpus_sha256 in blob


def test_digest_reaches_the_tamper_evident_chain_on_disk(tmp_path) -> None:
    """End-to-end: a recorded corpus lands in the signed audit JSONL.

    The whole DS-01 claim is that what the model saw becomes evidence on the
    same terms as what the runtime decided — so assert it against the real
    broker and the real file backend, not a mock.
    """
    from acc.audit import AuditBroker, FileAuditBackend

    broker = AuditBroker(
        backend=FileAuditBackend(base_path=str(tmp_path), retention_days=1),
        agent_id="agent-ds01",
    )

    rec = record("SYSTEM PROMPT", "USER CONTENT", source="cognitive_core")
    audit = AuditRecord(
        agent_id="agent-ds01",
        task_id="task-1",
        prompt_records=[r.as_dict() for r in drain("cognitive_core")],
    )
    asyncio.run(broker.record(audit))

    written = list(tmp_path.glob("audit-*.jsonl"))
    assert written, "no audit file produced"
    line = json.loads(written[0].read_text(encoding="utf-8").strip().splitlines()[0])

    assert len(line["prompt_records"]) == 1
    assert line["prompt_records"][0]["corpus_sha256"] == rec.corpus_sha256
    # Digest-only by default: the prompt text itself is not on disk.
    assert line["prompt_records"][0]["system_text"] == ""
    # And it is inside the integrity envelope.
    assert line["evidence_hash"] and line["chain_hash"]


def test_drain_empties_so_records_do_not_leak_across_tasks() -> None:
    record("A", "B", source="test")
    assert len(drain()) == 1
    assert drain() == []


# ---------------------------------------------------------------------------
# Attribution: the out-of-band reflection loop shares the task loop's backend
# ---------------------------------------------------------------------------


def test_source_context_tags_the_call_path() -> None:
    wrapped = recording_backend(_FakeBackend(), source="fallback")

    with prompt_record.source("memory_reflection"):
        asyncio.run(wrapped.complete("S", "U"))

    assert snapshot()[0].source == "memory_reflection"


def test_source_falls_back_outside_any_context() -> None:
    wrapped = recording_backend(_FakeBackend(), source="fallback")
    asyncio.run(wrapped.complete("S", "U"))
    assert snapshot()[0].source == "fallback"


def test_scoped_drain_leaves_other_sources_pending() -> None:
    """The reflection loop's prompts are its own evidence, not a task's.

    `_reflection_loop` runs out-of-band on the SAME backend object as the
    task loop.  An unscoped drain would attribute its calls to whichever
    task happened to write an audit record next.
    """
    record("task", "one", source="cognitive_core")
    record("reflect", "note", source="memory_reflection")
    record("task", "two", source="cognitive_core")

    taken = drain("cognitive_core")

    assert [r.user_text or r.user_sha256 for r in taken] and len(taken) == 2
    assert all(r.source == "cognitive_core" for r in taken)
    remaining = snapshot()
    assert len(remaining) == 1
    assert remaining[0].source == "memory_reflection"


def test_scoped_drain_preserves_order() -> None:
    for i in range(5):
        record(f"s{i}", f"u{i}", source="cognitive_core" if i % 2 == 0 else "other")
    taken = drain("cognitive_core")
    assert [r.seq for r in taken] == sorted(r.seq for r in taken)
    assert [r.source for r in snapshot()] == ["other", "other"]


def test_ledger_is_bounded() -> None:
    """An unattended process that never drains must not grow without limit."""
    for i in range(prompt_record._MAX_LEDGER + 50):
        record(f"s{i}", f"u{i}", source="test")
    assert len(snapshot()) == prompt_record._MAX_LEDGER
