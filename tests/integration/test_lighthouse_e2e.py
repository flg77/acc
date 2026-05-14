"""End-to-end test against a live vLLM backend (lighthouse-style host).

Phase 1 of the lighthouse verification:

* Skips the full ACC arbiter/NATS/redis stack — talks to the
  OpenAI-compatible LLM endpoint directly.
* Loads coding_agent's actual system_prompt.md template via the
  same RoleLoader the runtime uses, so the prompt the LLM sees
  is the prompt the production agent would assemble.
* Issues the operator's review prompt:
  "Write ASCII ssh banner — Headline: ACC, Subtitle:
   Agentic Cell Corpus" (written to /tmp).
* Asserts the response contains the required substrings AND
  writes the extracted banner to ``/tmp/acc_banner.txt`` so the
  operator can ``ssh lighthouse cat /tmp/acc_banner.txt`` to
  see what the model produced.

Skipped by default — sets ``ACC_E2E_LLM_URL`` to opt in.  Marked
slow + integration so CI doesn't accidentally run it.

Usage on lighthouse::

    cd /git/ml/agentic/acc-fresh/acc
    ACC_E2E_LLM_URL=http://127.0.0.1:8000/v1 \
    ACC_E2E_LLM_MODEL=RedHatAI/Llama-3.2-1B-Instruct-FP8 \
        python -m pytest tests/integration/test_lighthouse_e2e.py -v --no-cov

## Baseline calibration (2026-05-14, lighthouse)

Ran against ``RedHatAI/Llama-3.2-1B-Instruct-FP8`` × 3 with the
default prompt: 3 / 3 produced a cat-ASCII (``/_/\``, ``( o.o )``,
``> ^ <``) — the model has insufficient instruction-following
capacity for this task.  The pipeline is healthy (vLLM
reachable, system prompt loaded, response parsed, file
written to ``/tmp/acc_banner.txt`` on each run); the FAILURE
is the model's quality, not the plumbing.

The test's strict bar (subtitle present, headline ``ACC``
present, ≥ 50 chars) is calibrated to the operator's stated
UX expectation.  Larger models (Llama 3.1 8B+ / Qwen 7B+ /
DeepSeek-Coder-V2) pass this trivially in informal testing.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Opt-in
# ---------------------------------------------------------------------------


def _llm_url() -> str:
    return os.environ.get("ACC_E2E_LLM_URL", "").rstrip("/")


def _llm_model() -> str:
    return os.environ.get(
        "ACC_E2E_LLM_MODEL", "RedHatAI/Llama-3.2-1B-Instruct-FP8",
    )


pytestmark = pytest.mark.skipif(
    not _llm_url(),
    reason=(
        "Set ACC_E2E_LLM_URL=http://host:port/v1 (and optional "
        "ACC_E2E_LLM_MODEL) to run.  Lives in tests/integration/ so "
        "CI excludes it by default."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post_chat(messages: list[dict], *, timeout_s: float = 60.0) -> dict:
    """POST a chat-completions request via stdlib urllib.

    Returns the parsed JSON dict.  Raises on non-200.
    """
    body = json.dumps({
        "model": _llm_model(),
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1024,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{_llm_url()}/chat/completions",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _load_coding_agent_system_prompt() -> str:
    """Read coding_agent's system_prompt.md template AND interpolate
    the obvious operator-facing placeholders.  Mirrors what the
    runtime's CognitiveCore does at infusion."""
    from acc.tui.path_resolution import resolve_manifest_root  # noqa: PLC0415

    roles_root = resolve_manifest_root("ACC_ROLES_ROOT", "roles")
    path = roles_root / "coding_agent" / "system_prompt.md"
    template = path.read_text(encoding="utf-8")

    # Naive {{name}} interpolation — sufficient for the
    # operator-facing fields the prompt template carries.
    placeholders = {
        "collective_id": "sol-test",
        "version": "0.1.0",
        "purpose": "Generate, review, and test code artefacts.",
        "task_types": "- CODE_GENERATE\n- CODE_REVIEW\n- TEST_WRITE",
    }
    for key, value in placeholders.items():
        template = template.replace("{{" + key + "}}", value)
        template = template.replace(
            "{{ " + key + " }}", value,
        )  # tolerate whitespace
    return template


def _extract_banner(content: str) -> str:
    """Extract the ASCII banner block from the LLM's response.

    Looks for the first ```...``` fenced block; falls back to the
    whole content stripped if the LLM didn't fence.
    """
    fence = re.search(r"```[^\n]*\n(.*?)```", content, re.DOTALL)
    if fence is not None:
        return fence.group(1).rstrip() + "\n"
    return content.strip() + "\n"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_vllm_is_reachable():
    """Sanity — the configured endpoint serves /v1/models."""
    url = f"{_llm_url()}/models"
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
        assert resp.status == 200
        body = json.loads(resp.read().decode("utf-8"))
    assert "data" in body
    assert any(_llm_model() in m.get("id", "") for m in body["data"]), (
        f"model {_llm_model()!r} not in served list"
    )


def test_coding_agent_writes_ascii_banner_for_acc_to_tmp(tmp_path):
    """Operator's prompt:

        Write ASCII ssh banner — Headline: ACC,
        Subtitle: Agentic Cell Corpus.  Write it to /tmp.

    Asserts:

    * The LLM responds with a non-trivial banner.
    * The banner contains the literal "ACC" and "Agentic Cell
      Corpus" tokens (a low-bar check that survives ASCII-art
      variations).
    * The extracted banner is written to ``/tmp/acc_banner.txt``
      so the operator can inspect the actual output post-run.
    """
    system_prompt = _load_coding_agent_system_prompt()
    operator_prompt = (
        "Write an ASCII ssh banner suitable for /etc/ssh/banner.\n"
        "Headline (large ASCII art):  ACC\n"
        "Subtitle (smaller text under the headline):  "
        "Agentic Cell Corpus\n"
        "\n"
        "Output the banner inside a single fenced code block "
        "(```...```).  No commentary outside the fence.  Aim for "
        "a width of 60 characters or less so it renders cleanly "
        "in a terminal.  Indicate the file should be saved to "
        "/tmp/acc_banner.txt."
    )

    response = _post_chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": operator_prompt},
    ])

    assert "choices" in response and response["choices"], response
    content = response["choices"][0]["message"]["content"]
    assert content, "empty LLM response"

    banner = _extract_banner(content)

    # Persist for operator inspection.  Lives at /tmp on the
    # host AND at tmp_path inside the test sandbox.
    on_disk = Path("/tmp") / "acc_banner.txt"
    try:
        on_disk.write_text(banner, encoding="utf-8")
        wrote_persistent = True
    except OSError:
        wrote_persistent = False  # CI / non-POSIX may forbid /tmp writes

    (tmp_path / "acc_banner.txt").write_text(banner, encoding="utf-8")

    # Substring assertions — survive ASCII-art transformations.
    # The LLM may stylise "ACC" as block letters spanning multiple
    # lines, so we look for the subtitle (which usually renders
    # verbatim) AND the literal "ACC" anywhere in the response (the
    # block headers / commentary should mention it even if the art
    # is purely visual).
    assert "Agentic Cell Corpus" in content, (
        f"subtitle missing from response.  Full text:\n{content[:800]}"
    )
    assert "ACC" in content, (
        f"headline reference missing.  Full text:\n{content[:800]}"
    )

    # Banner length sanity — block-art "ACC" + a subtitle line is
    # typically ≥ 5 lines and ≥ 80 chars.  Calibrated low to
    # survive a model that produces a single-line ASCII version.
    assert len(banner) >= 50, (
        f"banner suspiciously short ({len(banner)} chars):\n{banner}"
    )

    # Report path so the pytest log makes the artefact easy to find.
    print(
        f"\n[lighthouse-e2e] banner written to "
        f"{'/tmp/acc_banner.txt (persistent)' if wrote_persistent else tmp_path / 'acc_banner.txt'}"
    )
    print(f"[lighthouse-e2e] banner ({len(banner)} chars):\n{banner}")
