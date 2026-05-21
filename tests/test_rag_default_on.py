"""PR-I (D-002) — RAG-the-agent: default-on memory retrieval.

The operator-reported gap: agents have `read_vector_db` in their
`allowed_actions` and Soma reports `ICL episodes = N` (past tasks ARE
in LanceDB) but the agent answers "no, I don't retain session history"
when asked because nothing in the system prompt tells it about its
own memory.

PR-I closes this by querying LanceDB's `episodes` table for the
top-K nearest past episodes before each LLM call and folding them
into the system prompt under a `RECENT_RELEVANT_EPISODES:` section.

These tests pin:

1. Default-on — `RoleDefinitionConfig.memory_retrieval=True` by default;
   `process_task` calls `vector.search("episodes", embedding, k)`.
2. Opt-out — `memory_retrieval=False` on the role skips retrieval
   entirely; no `vector.search` call.
3. Retrieved episodes land in the system prompt under the expected
   section header.
4. Robustness — embedding failure, search failure, missing methods
   all swallowed; the LLM call still happens with the legacy prompt.
5. Freshness filter — episodes older than the window are dropped.
6. Same-agent filter — episodes from a different agent are dropped.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.config import RoleDefinitionConfig


# ---------------------------------------------------------------------------
# Fixtures (mirrors test_cognitive_core.py's `_make_core` shape but
# parametrises the vector backend so we can inject search() behaviour)
# ---------------------------------------------------------------------------


DIM = 384


def _unit_vector(d: int = DIM) -> list[float]:
    v = [0.0] * d
    v[0] = 1.0
    return v


def _mock_llm(content: str = "the agent's reply") -> MagicMock:
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={
        "content": content,
        "usage": {"total_tokens": 50},
    })
    llm.embed = AsyncMock(return_value=_unit_vector())
    return llm


def _mock_vector_with_episodes(rows: list[dict]) -> MagicMock:
    """Vector backend whose search() returns *rows*; insert() succeeds."""
    v = MagicMock()
    v.insert.return_value = 1
    v.search.return_value = rows
    return v


def _make_core(*, role_label: str = "analyst", llm=None, vector=None):
    from acc.cognitive_core import CognitiveCore
    return CognitiveCore(
        agent_id="agent-rag-test",
        collective_id="sol-test",
        llm=llm or _mock_llm(),
        vector=vector or _mock_vector_with_episodes([]),
        redis_client=None,
        role_label=role_label,
    )


def _make_role(**kwargs):
    defaults = {
        "purpose": "Analyse signals and surface patterns.",
        "persona": "analytical",
        "seed_context": "Domain: software engineering.",
        "version": "0.1.0",
    }
    defaults.update(kwargs)
    return RoleDefinitionConfig.model_validate(defaults)


# ---------------------------------------------------------------------------
# Default-on contract
# ---------------------------------------------------------------------------


def test_role_default_memory_retrieval_is_true():
    """PR-I — every role boots with RAG on unless role.yaml says
    otherwise."""
    role = _make_role()
    assert role.memory_retrieval is True


def test_role_can_opt_out_via_yaml_field():
    """PR-I — ephemeral roles can opt out by setting the flag to
    False in role.yaml."""
    role = _make_role(memory_retrieval=False)
    assert role.memory_retrieval is False


# ---------------------------------------------------------------------------
# Retrieval happens on the happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_is_called_with_query_embedding():
    """PR-I — process_task with a non-empty user content triggers
    vector.search('episodes', embedding, k=5)."""
    vector = _mock_vector_with_episodes([])
    core = _make_core(vector=vector)
    await core.process_task(
        {"content": "what is the time complexity of mergesort?"},
        role=_make_role(),
    )
    vector.search.assert_called()
    args, kwargs = vector.search.call_args
    table_arg = args[0] if args else kwargs.get("table")
    top_k_arg = args[2] if len(args) >= 3 else kwargs.get("top_k")
    assert table_arg == "episodes"
    assert top_k_arg == 5


@pytest.mark.asyncio
async def test_retrieved_episodes_land_in_system_prompt():
    """PR-I — when search returns rows, they appear in the LLM's
    system_prompt under the RECENT_RELEVANT_EPISODES header."""
    now = time.time()
    rows = [
        {
            "id": "ep-1",
            "agent_id": "agent-rag-test",
            "ts": now - 60,
            "signal_type": "TASK_ASSIGN",
            "payload_json": json.dumps({
                "content": "Earlier you wrote a Fibonacci function",
            }),
            "embedding": _unit_vector(),
        },
    ]
    vector = _mock_vector_with_episodes(rows)
    llm = _mock_llm()
    core = _make_core(llm=llm, vector=vector)
    await core.process_task(
        {"content": "do you remember the Fibonacci task?"},
        role=_make_role(),
    )
    # Inspect the system_prompt passed to llm.complete().
    assert llm.complete.called
    call_args = llm.complete.call_args
    system_prompt = call_args[0][0] if call_args[0] else call_args[1].get("system", "")
    assert "RECENT_RELEVANT_EPISODES" in system_prompt
    assert "Fibonacci function" in system_prompt


# ---------------------------------------------------------------------------
# Opt-out path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_retrieval_false_skips_search():
    """PR-I — role.memory_retrieval=False means vector.search is
    never called.  Saves the ~150-300ms RAG latency for ephemeral
    roles."""
    vector = _mock_vector_with_episodes([])
    core = _make_core(vector=vector)
    await core.process_task(
        {"content": "ephemeral one-shot"},
        role=_make_role(memory_retrieval=False),
    )
    vector.search.assert_not_called()


@pytest.mark.asyncio
async def test_memory_retrieval_false_omits_section_from_prompt():
    """PR-I — opt-out leaves the system prompt clean (no
    RECENT_RELEVANT_EPISODES section)."""
    llm = _mock_llm()
    core = _make_core(llm=llm)
    await core.process_task(
        {"content": "one-shot"},
        role=_make_role(memory_retrieval=False),
    )
    sys_prompt = llm.complete.call_args[0][0]
    assert "RECENT_RELEVANT_EPISODES" not in sys_prompt


# ---------------------------------------------------------------------------
# Robustness — failures must NOT break the LLM call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_exception_swallowed_llm_still_called():
    """PR-I invariant — RAG is additive.  A LanceDB error must NOT
    block the LLM call.  This is what makes default-on safe even on
    fresh-boot agents whose ``episodes`` table hasn't been created
    yet."""
    vector = MagicMock()
    vector.insert.return_value = 1
    vector.search.side_effect = RuntimeError("table 'episodes' does not exist")
    llm = _mock_llm()
    core = _make_core(llm=llm, vector=vector)
    result = await core.process_task(
        {"content": "ask anything"},
        role=_make_role(),
    )
    # LLM was still called; result is not blocked.
    assert llm.complete.called
    assert not result.blocked


@pytest.mark.asyncio
async def test_embed_exception_swallowed_llm_still_called():
    """PR-I — same invariant when the embed() call itself fails."""
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={
        "content": "ok", "usage": {"total_tokens": 10},
    })
    llm.embed = AsyncMock(side_effect=RuntimeError("model not loaded"))
    core = _make_core(llm=llm)
    result = await core.process_task(
        {"content": "ask anything"},
        role=_make_role(),
    )
    assert llm.complete.called
    assert not result.blocked


@pytest.mark.asyncio
async def test_empty_query_short_circuits():
    """PR-I — empty user content skips RAG entirely (nothing to
    embed)."""
    vector = _mock_vector_with_episodes([])
    core = _make_core(vector=vector)
    await core.process_task(
        {"content": ""},
        role=_make_role(),
    )
    vector.search.assert_not_called()


@pytest.mark.asyncio
async def test_vector_backend_without_search_attr_skips():
    """PR-I — defensive against test harnesses or future vector
    backends that haven't implemented ``search``."""
    vector = MagicMock(spec=["insert"])  # no `search` attribute
    vector.insert.return_value = 1
    llm = _mock_llm()
    core = _make_core(llm=llm, vector=vector)
    result = await core.process_task(
        {"content": "anything"},
        role=_make_role(),
    )
    assert llm.complete.called
    assert not result.blocked


# ---------------------------------------------------------------------------
# Direct _retrieve_episodes — filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_drops_stale_episodes():
    """PR-I — episodes older than the freshness window are dropped."""
    now = time.time()
    rows = [
        {
            "id": "ep-fresh",
            "agent_id": "agent-rag-test",
            "ts": now - 10,
            "signal_type": "TASK_ASSIGN",
            "payload_json": json.dumps({"content": "recent work"}),
        },
        {
            "id": "ep-stale",
            "agent_id": "agent-rag-test",
            "ts": now - (86400 * 30),  # 30 days
            "signal_type": "TASK_ASSIGN",
            "payload_json": json.dumps({"content": "ancient work"}),
        },
    ]
    core = _make_core(vector=_mock_vector_with_episodes(rows))
    out = await core._retrieve_episodes(
        "query text", _make_role(), top_k=5,
    )
    assert len(out) == 1
    assert "recent work" in out[0]["excerpt"]


@pytest.mark.asyncio
async def test_retrieve_drops_other_agent_episodes():
    """PR-I — episodes attributed to a different agent_id are dropped
    so the agent's "do you remember?" answer reflects only its own
    history."""
    now = time.time()
    rows = [
        {
            "id": "ep-own",
            "agent_id": "agent-rag-test",
            "ts": now - 60,
            "signal_type": "TASK_ASSIGN",
            "payload_json": json.dumps({"content": "my own work"}),
        },
        {
            "id": "ep-other",
            "agent_id": "agent-someone-else",
            "ts": now - 60,
            "signal_type": "TASK_ASSIGN",
            "payload_json": json.dumps({"content": "sibling's work"}),
        },
    ]
    core = _make_core(vector=_mock_vector_with_episodes(rows))
    out = await core._retrieve_episodes(
        "query text", _make_role(), top_k=5,
    )
    assert len(out) == 1
    assert "my own work" in out[0]["excerpt"]
    assert all("sibling" not in r["excerpt"] for r in out)


@pytest.mark.asyncio
async def test_retrieve_handles_malformed_payload_json():
    """PR-I — a row with bad JSON in payload_json shouldn't crash the
    retrieval; it falls back to a truncated raw-string excerpt."""
    rows = [
        {
            "id": "ep-bad",
            "agent_id": "agent-rag-test",
            "ts": time.time() - 60,
            "signal_type": "TASK_ASSIGN",
            "payload_json": "this is not json {{{",
        },
    ]
    core = _make_core(vector=_mock_vector_with_episodes(rows))
    out = await core._retrieve_episodes(
        "q", _make_role(), top_k=5,
    )
    assert len(out) == 1
    assert "not json" in out[0]["excerpt"]


@pytest.mark.asyncio
async def test_retrieve_renders_ts_str_for_prompt():
    """PR-I — each returned episode carries a HH:MM:SS string ready
    for the build_system_prompt RAG block."""
    rows = [{
        "id": "ep-1",
        "agent_id": "agent-rag-test",
        "ts": time.time() - 60,
        "signal_type": "TASK_ASSIGN",
        "payload_json": json.dumps({"content": "x"}),
    }]
    core = _make_core(vector=_mock_vector_with_episodes(rows))
    out = await core._retrieve_episodes("q", _make_role())
    assert ":" in out[0]["ts_str"]  # HH:MM:SS contains colons
    assert len(out[0]["ts_str"]) == 8  # exactly HH:MM:SS


# ---------------------------------------------------------------------------
# build_system_prompt — accepts retrieved_episodes
# ---------------------------------------------------------------------------


def test_build_system_prompt_skips_block_when_episodes_empty():
    """PR-I — backwards compat: no episodes arg or empty list means
    legacy prompt verbatim (no RAG section)."""
    core = _make_core()
    role = _make_role()
    sp_no_arg = core.build_system_prompt(role)
    sp_empty = core.build_system_prompt(role, retrieved_episodes=[])
    assert "RECENT_RELEVANT_EPISODES" not in sp_no_arg
    assert "RECENT_RELEVANT_EPISODES" not in sp_empty
    # The legacy text is unchanged.
    assert sp_no_arg == sp_empty


def test_build_system_prompt_renders_episode_lines():
    """PR-I — episodes render as `- [HH:MM:SS] [signal_type] excerpt`"""
    core = _make_core()
    role = _make_role()
    eps = [
        {
            "ts_str": "12:34:56",
            "signal_type": "TASK_ASSIGN",
            "excerpt": "wrote Fibonacci function",
        },
        {
            "ts_str": "12:35:10",
            "signal_type": "TASK_ASSIGN",
            "excerpt": "explained mergesort complexity",
        },
    ]
    sp = core.build_system_prompt(role, retrieved_episodes=eps)
    assert "RECENT_RELEVANT_EPISODES" in sp
    assert "[12:34:56]" in sp
    assert "Fibonacci function" in sp
    assert "mergesort" in sp


def test_build_system_prompt_truncates_long_excerpts():
    """PR-I — excerpts over 160 chars are truncated with an ellipsis
    so the system prompt stays bounded."""
    core = _make_core()
    role = _make_role()
    long_excerpt = "x" * 500
    sp = core.build_system_prompt(role, retrieved_episodes=[{
        "ts_str": "00:00:00",
        "signal_type": "TASK_ASSIGN",
        "excerpt": long_excerpt,
    }])
    assert "x" * 200 not in sp  # not the full 500
    assert "…" in sp
