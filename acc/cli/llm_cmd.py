"""``acc-cli llm test`` — round-trip LLM smoke test.

Loads ``acc-config.yaml`` (path overridable via ``ACC_CONFIG_PATH``) and
constructs **only the LLM backend** (not the full :class:`BackendBundle`)
so the CLI image does not need to bundle LanceDB / pymilvus / OTel /
sentence-transformers — heavy deps that ``build_backends`` would pull
in for vector/signaling/metrics.

Mirrors the LLM construction logic in :func:`acc.config.build_backends`
exactly; that function uses lazy imports per-branch so each backend
adds only its own dep (e.g. ``httpx`` for ``openai_compat``).

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


def _build_llm_only(config: Any) -> Any:
    """Construct the LLM backend in isolation (no vector/signaling/metrics).

    Mirrors :func:`acc.config.build_backends` LLM branch-by-branch so the
    CLI image only carries the deps it actually needs — most importantly
    keeping LanceDB and pymilvus out of the CLI container.

    Raises:
        ValueError: When the configured backend name is unknown.
    """
    backend_name = config.llm.backend

    if backend_name == "ollama":
        from acc.backends.llm_ollama import OllamaBackend  # noqa: PLC0415
        return OllamaBackend(
            base_url=config.llm.ollama_base_url,
            model=config.llm.ollama_model,
        )
    if backend_name == "anthropic":
        from acc.backends.llm_anthropic import AnthropicBackend  # noqa: PLC0415
        return AnthropicBackend(
            model=config.llm.anthropic_model,
            embedding_model_path=config.llm.embedding_model_path,
        )
    if backend_name == "vllm":
        from acc.backends.llm_vllm import VLLMBackend  # noqa: PLC0415
        return VLLMBackend(
            inference_url=config.llm.base_url or config.llm.vllm_inference_url,
            model=config.llm.model or config.llm.ollama_model,
        )
    if backend_name == "openai_compat":
        from acc.backends.llm_openai_compat import OpenAICompatBackend  # noqa: PLC0415
        return OpenAICompatBackend(
            base_url=config.llm.base_url or config.llm.vllm_inference_url,
            model=config.llm.model or config.llm.ollama_model,
            api_key_env=config.llm.api_key_env,
            embedding_model_path=config.llm.embedding_model_path,
            timeout_s=config.llm.request_timeout_s,
            max_retries=config.llm.max_retries,
        )
    if backend_name == "llama_stack":
        from acc.backends.llm_llama_stack import LlamaStackBackend  # noqa: PLC0415
        return LlamaStackBackend(
            base_url=config.llm.llama_stack_url,
            embedding_model_path=config.llm.embedding_model_path,
        )
    raise ValueError(f"Unknown LLM backend: {backend_name!r}")


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
    from acc.config import load_config  # noqa: PLC0415

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
    try:
        llm = _build_llm_only(config)
    except ImportError as exc:
        # Container image does not ship this LLM backend's dep.  Tell the
        # user exactly which package to add or which backend to switch to.
        print(
            f"LLM backend {config.llm.backend!r} requires a missing package: {exc.name}",
            file=sys.stderr,
        )
        print(
            "  The minimal acc-cli image only ships httpx (covers openai_compat,\n"
            "  vllm).  For ollama/anthropic/llama_stack rebuild the CLI image with\n"
            "  the extra dep, or switch ACC_LLM_BACKEND to openai_compat.",
            file=sys.stderr,
        )
        return 2

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
