"""PR-CA1's prefix invariant, held against the roles that actually ship.

``tests/test_prompt_cache_ordering.py`` already proves the invariant and proves
it well — retrieved memory stays out of the system prompt, the prompt is
deterministic, a role edit is allowed to change it. What it proves it *about*
is a minimal synthetic ``RoleDefinitionConfig``: purpose, persona, version, and
none of the optional blocks.

Every optional block is where variability could actually leak, and every one of
them is live in ``roles/`` right now: six roles set ``reasoning_trace: true``
(``acc/cognitive_core.py:1927``) and two carry ``default_skills``
(``:1946``), whose advertised list is filtered against a **set**
(``advertised_skill_ceiling``, ``:1835``). The filter iterates the list and uses
the set only for membership, which is correct — and correct in a way nothing
currently asserts, so the next edit to that expression is free to reverse it.

Two gaps, then, and they are different in kind:

1. **Reach.** The invariant is asserted for a role shape no deployment runs.
   These tests parametrise over the real ``roles/`` tree instead.

2. **Method.** ``test_the_stable_prompt_is_deterministic`` compares two calls in
   one process. A set- or dict-ordering leak is *stable within a process* and
   invisible to that comparison — it changes between runs, not within one. Only
   a differing ``PYTHONHASHSEED`` in a fresh interpreter can see it, which is
   what ``test_prefix_survives_a_different_hash_seed`` does.

The second is the one worth having. It is also the failure mode that would
present exactly as PR-CA1 was designed to prevent: nothing fails, the prompt is
still correct, and the cache silently stops hitting after a restart.

Change: ``openspec/changes/20260826-endpoint-capability-profile`` Phase 1.2.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.cognitive_core import CognitiveCore
from acc.role_loader import RoleLoader, list_roles

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROLES_ROOT = _REPO_ROOT / "roles"

#: The shipped roles, by name. Collected at import so a parametrised test id
#: names the role that failed.
REAL_ROLES = list_roles(_ROLES_ROOT)


def _core(role_label: str = "analyst") -> CognitiveCore:
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"content": "ok", "usage": {"total_tokens": 0}})
    llm.embed = AsyncMock(return_value=[0.0] * 384)
    return CognitiveCore(
        agent_id="agent-prefix",
        collective_id="sol-test",
        llm=llm,
        vector=MagicMock(),
        redis_client=None,
        role_label=role_label,
    )


def _load(name: str):
    role = RoleLoader(roles_root=_ROLES_ROOT, role_name=name).load()
    assert role is not None, f"roles/{name}/role.yaml did not load"
    return role


def _episodes(n: int) -> list[dict]:
    return [
        {
            "ts_str": f"1{i}:30:00",
            "signal_type": "TASK_ASSIGN",
            "excerpt": f"episode {i} with distinctive text",
        }
        for i in range(n)
    ]


def test_the_role_tree_is_not_empty():
    """A guard on the parametrisation itself.

    If ``list_roles`` ever returns nothing — a moved directory, a renamed
    ``role.yaml`` — every test below would pass by never running, which is the
    quietest way for this file to stop protecting anything.
    """
    assert REAL_ROLES, f"no roles found under {_ROLES_ROOT}"


@pytest.mark.parametrize("role_name", REAL_ROLES)
def test_real_role_prefix_ignores_retrieved_episodes(role_name: str):
    """The PR-CA1 guarantee, for the roles that ship rather than a stub."""
    core = _core()
    role = _load(role_name)
    baseline = core.build_system_prompt(role)

    assert core.build_system_prompt(role, _episodes(1)) == baseline
    assert core.build_system_prompt(role, _episodes(25)) == baseline
    assert "RECENT_RELEVANT_EPISODES" not in baseline


@pytest.mark.parametrize("role_name", REAL_ROLES)
def test_real_role_prefix_is_stable_across_calls(role_name: str):
    """No timestamp, counter or per-call id in the prefix of a shipped role."""
    core = _core()
    role = _load(role_name)
    assert core.build_system_prompt(role) == core.build_system_prompt(role)


def test_the_optional_prompt_branches_are_actually_exercised():
    """Coverage guard, because the tests above can hold vacuously.

    ``reasoning_trace`` and ``default_skills`` are the two branches that append
    to the prefix after the seed. If no shipped role sets them, every assertion
    above is testing the same plain path repeatedly and the branches that carry
    the real ordering risk go untested without anything saying so.
    """
    roles = {name: _load(name) for name in REAL_ROLES}

    with_trace = [n for n, r in roles.items() if getattr(r, "reasoning_trace", False)]
    with_skills = [n for n, r in roles.items() if getattr(r, "default_skills", None)]

    assert with_trace, (
        "no shipped role sets reasoning_trace -- the _REASONING_SYSTEM_BLOCK "
        "branch (cognitive_core.py:1927) is no longer covered by this file"
    )
    assert with_skills, (
        "no shipped role sets default_skills -- the advertised-skills branch "
        "(cognitive_core.py:1946) is no longer covered by this file"
    )


# ---------------------------------------------------------------------------
# Cross-process determinism
# ---------------------------------------------------------------------------

#: Rendered in a fresh interpreter so ``PYTHONHASHSEED`` is in force. Prints one
#: JSON object mapping role name to the SHA-256 of its system prompt.
_DIGEST_SCRIPT = """
import hashlib, json, sys
from unittest.mock import AsyncMock, MagicMock
from acc.cognitive_core import CognitiveCore
from acc.role_loader import RoleLoader, list_roles

roles_root = sys.argv[1]
llm = MagicMock()
llm.complete = AsyncMock(return_value={"content": "ok", "usage": {"total_tokens": 0}})
llm.embed = AsyncMock(return_value=[0.0] * 384)
core = CognitiveCore(
    agent_id="agent-prefix",
    collective_id="sol-test",
    llm=llm,
    vector=MagicMock(),
    redis_client=None,
    role_label="analyst",
)

out = {}
for name in list_roles(roles_root):
    role = RoleLoader(roles_root=roles_root, role_name=name).load()
    if role is None:
        continue
    prompt = core.build_system_prompt(role)
    out[name] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
print(json.dumps(out))
"""


def _digests_under_hash_seed(seed: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", _DIGEST_SCRIPT, str(_ROLES_ROOT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"prompt rendering failed under PYTHONHASHSEED={seed}:\n{proc.stderr[-4000:]}"
    )
    # Anything a warning wrote to stdout would corrupt the payload; take the
    # last line, which is the print above.
    payload = proc.stdout.strip().splitlines()[-1]
    return json.loads(payload)


def test_prefix_survives_a_different_hash_seed():
    """The check an in-process comparison structurally cannot make.

    A prefix built by iterating a ``set`` — of allowed skills, of MCP ids, of
    overlay grants — is *stable within one interpreter* and differs between
    interpreters. Two calls in one process therefore agree, the existing
    determinism test passes, and the cache stops hitting after every restart
    with nothing to show for it.

    ``advertised_skill_ceiling`` (``acc/cognitive_core.py:1835``) is a real set
    on this path. Today it is only ever used for membership while the ordered
    list drives iteration (``:1947``), which is correct — this test is what
    keeps it that way.
    """
    a = _digests_under_hash_seed("0")
    b = _digests_under_hash_seed("1")

    assert a, "the subprocess rendered no roles at all"
    assert a.keys() == b.keys()

    differing = sorted(name for name in a if a[name] != b[name])
    assert not differing, (
        "system prompt changed with PYTHONHASHSEED for: "
        + ", ".join(differing)
        + " -- something in the prefix iterates a set or dict, so the per-role "
        "prefix cache is invalidated on every process restart"
    )


def test_the_hash_seed_probe_would_notice_a_leak():
    """Guards the guard.

    ``test_prefix_survives_a_different_hash_seed`` is only worth anything if a
    seed-dependent string really does differ across seeds. If that assumption
    ever stopped holding — a Python change, a seed the runner pins — the test
    above would pass unconditionally and prove nothing.
    """
    script = (
        "import json;"
        "print(json.dumps({'x': [str(hash('acc-prefix-canary'))]}))"
    )

    def _run(seed: str) -> str:
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, env=env, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    assert _run("0") != _run("1"), (
        "PYTHONHASHSEED is not varying string hashing in this environment; "
        "test_prefix_survives_a_different_hash_seed cannot detect anything"
    )
