"""``acc-cli llm test`` — round-trip LLM smoke test.

Loads ``acc-config.yaml`` (path overridable via ``ACC_CONFIG_PATH``) and
constructs the configured LLM backend through :func:`acc.config.build_backends`.
The full backend bundle is built so the LLM is wired exactly as the
agent would see it; we just don't connect signaling/vector/metrics.

Useful for verifying:
  * Network reachability of vLLM / OpenAI-compat endpoints.
  * API key resolution from env vars (``ACC_LLM_API_KEY_ENV``).
  * Model name correctness — surfaces errors that would otherwise
    only appear deep inside the agent's first task.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import Any


def register(sub: argparse._SubParsersAction) -> None:
    llm = sub.add_parser("llm", help="Smoke-test the configured LLM backend.")
    llm_sub = llm.add_subparsers(dest="llm_command", required=True, metavar="ACTION")

    test_p = llm_sub.add_parser("test", help="Send a tiny prompt and print the result.")
    test_p.add_argument(
        "--prompt",
        default="Reply with the JSON object {\"ok\": true} and nothing else.",
        help="Prompt text (default: tiny JSON probe).",
    )
    test_p.add_argument(
        "--system",
        default="You are a deterministic JSON responder.",
        help="System prompt (default: deterministic JSON responder).",
    )
    test_p.add_argument(
        "--config",
        default=None,
        help="Path to acc-config.yaml (default: $ACC_CONFIG_PATH or ./acc-config.yaml).",
    )
    test_p.set_defaults(func=_cmd_test)


async def _cmd_test(args: argparse.Namespace) -> int:
    from acc.config import build_backends, load_config  # noqa: PLC0415

    cfg_path = args.config or os.environ.get("ACC_CONFIG_PATH", "acc-config.yaml")
    if not os.path.isfile(cfg_path):
        print(f"config not found at {cfg_path!r}", file=sys.stderr)
        print(
            "Set ACC_CONFIG_PATH or pass --config; the file is also generated\n"
            "  by `./acc-deploy.sh up` and lives at the repo root.",
            file=sys.stderr,
        )
        return 1

    config = load_config(cfg_path)
    backends = build_backends(config)
    llm = backends.llm

    print(f"backend:    {config.llm.backend}")
    print(f"model:      {getattr(config.llm, 'ollama_model', '') or getattr(config.llm, 'model', '')}")
    print(f"base_url:   {getattr(config.llm, 'base_url', '')}")
    print(f"system:     {args.system}")
    print(f"prompt:     {args.prompt}")
    print("---")

    t0 = time.perf_counter()
    try:
        result = await llm.complete(args.system, args.prompt)
    except Exception as exc:
        print(f"FAILED: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 2
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    print(f"elapsed_ms: {elapsed_ms:.1f}")
    if isinstance(result, dict):
        import json  # noqa: PLC0415
        print("response:")
        for line in json.dumps(result, indent=2, default=str).splitlines():
            print(f"  {line}")
    else:
        print(f"response:   {result!r}")

    return 0
