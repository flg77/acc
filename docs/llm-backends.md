# LLM backends for ACC

Reference for the LLM models we drive against from the test
hosts on the operator's LAN.  Models are launched via
[`vllmpunch`](https://github.com/flg77/vllmpunch) — a wrapper
around `vllm` that ships an opinionated `models.json` catalogue
+ port-allocation scheme.  Per-host `acc-config.yaml` overlays
live under [`deploy/host-configs/`](../deploy/host-configs/)
(gitignored — operator-local).

## Why a catalogue

ACC is model-agnostic by design; the operator-facing experience
(coding-agent, autoresearcher, …) is sensitive to model size,
quant, and instruction-following capability.  Recording which
models we've tested against, on which host, and with what
results, lets us learn from one test cycle to the next instead
of re-running the same matrix every time.

The matching skill (`acc-llm-test-history`) appends every test
run to [`test/history/`](../test/history/) as JSONL.  Use that
archive — not memory — to decide what to test next.

## Hosts

| Host | GPU | VRAM | Notes |
|---|---|---|---|
| `lighthouse` | NVIDIA RTX 4000 Ada Laptop | 12 GB | Operator's primary test bench.  Repo at `/git/ml/agentic/acc-fresh/acc`.  vllmpunch at `/git/infrastructure/containers/vllm/vllmpunch`. |

Add additional test hosts to this table as they come online;
write matching `deploy/host-configs/<host>-*.yaml` overlays.

## Model catalogue (lighthouse-tuned)

VRAM column is the **model only**; KV cache adds another
~2–4 GB depending on `max_model_len`.  Headroom column is what's
left on a 12 GB GPU after both.

| vllmpunch alias | HF id | Quant | Model VRAM | Headroom on 12 GB | Use when… |
|---|---|---|---|---|---|
| `llama` (alias of small) | `RedHatAI/Llama-3.2-1B-Instruct-FP8` | FP8 | ~2 GB | ✅ comfortable | smoke-tests, baseline.  Below the bar for ASCII art / structured output. |
| `qwen3` | `Qwen/Qwen3-1.7B` | BF16 | ~3.5 GB | ✅ comfortable | small + Qwen-3 family is sharper than Llama-3.2-1B on instruction-following. |
| `llama-3b-fp8` | `RedHatAI/Llama-3.2-3B-Instruct-FP8` | FP8 | ~3 GB | ✅ comfortable | smallest meaningful upgrade from 1B — big quality jump for coding/instruction tasks. |
| `phi4` | `microsoft/Phi-4-mini-instruct` | BF16 | ~5 GB | ✅ comfortable | Microsoft Phi-4 mini.  Strong on reasoning despite the size. |
| `qwen-7b-gptq` | `Qwen/Qwen2.5-7B-Instruct-GPTQ-Int4` | GPTQ-Int4 | ~4 GB | ✅ very comfortable | general-purpose 7B at 4 GB.  Excellent default for non-coding agents. |
| **`qwen-coder-7b-awq`** | **`Qwen/Qwen2.5-Coder-7B-Instruct-AWQ`** | **AWQ-Int4** | **~5 GB** | **✅ recommended for coding_agent** | tuned for code + JSON-fenced output the coding_agent system prompt expects. |
| `mistral-7b` | `neuralmagic/Mistral-7B-Instruct-v0.3-quantized.w8a8` | W8A8 | ~7 GB | ✅ fits | Mistral 7B at W8A8.  Strong general instruction-following. |
| `r1-8b` | `neuralmagic/DeepSeek-R1-Distill-Llama-8B-FP8` | FP8 | ~8 GB | ⚠️ tight; `max_model_len 8192` | reasoning-tuned; long CoT outputs.  Bump `request_timeout_s` to ≥ 180. |
| `llama-8b-fp8` | `RedHatAI/Llama-3.1-8B-Instruct-FP8` | FP8 | ~8 GB | ⚠️ tight; `max_model_len 16384` | high-quality general 8B.  Operator-preferred for serious tasks if it fits. |
| `dsv3` | `deepseek-ai/DeepSeek-V3-Distill-Llama-8B` | BF16 | ~16 GB | ❌ likely OOM at 12 GB | included for catalogue completeness — won't run on lighthouse without aggressive quant. |
| `nemo12` | `neuralmagic/Mistral-NeMo-Instruct-2407-FP8` | FP8 + FP8 KV | ~12 GB | ⚠️ very tight; consider disabling fp8 KV cache | Mistral NeMo 12B.  Pushes the 12 GB envelope; may OOM under load. |

## Picking a model — operator's decision tree

```
need code-shaped output (JSON fenced, file paths, …)?
└── yes  → qwen-coder-7b-awq
    no   → next

need long-context (16k+)?
└── yes  → llama-8b-fp8 (with --max-model-len 16384)
    no   → next

need reasoning chains?
└── yes  → r1-8b  (bump timeout)
    no   → next

just want a baseline / fast smoke?
└── llama  (1B; or llama-3b-fp8 for a real ceiling)
```

## Launch flow

On the test host (lighthouse):

```bash
# 1. Start the model — vllmpunch pulls + launches on the configured port.
cd /git/infrastructure/containers/vllm/vllmpunch
./vllmpunch run -d qwen-coder-7b-awq

# 2. Wait ~30 s for the container to come up + the model to load.
curl -s http://127.0.0.1:8013/v1/models | head -3

# 3. On your laptop, sync the matching acc-config overlay.
./scripts/sync-host-config.sh lighthouse qwen-coder-7b-awq

# 4. Run the test from your laptop OR on the host.
ACC_E2E_LLM_URL=http://lighthouse:8013/v1 \
ACC_E2E_LLM_MODEL=Qwen/Qwen2.5-Coder-7B-Instruct-AWQ \
    python -m pytest tests/integration/test_lighthouse_e2e.py -v --no-cov
```

The `acc-llm-test-history` skill automates steps 3 + 4 + result
capture.

## Port allocation

Pinned in the operator's `~/.config/vllmpunch/models.json`:

| Port | Model | Notes |
|---|---|---|
| 8000 | (currently `vllm-llama`, ad-hoc launch) | `pasta.avx2` host-side listener |
| 8001 | `r1-8b` | |
| 8002 | `nemo12` | |
| 8003 | `phi4` | |
| 8004 | `dsv3` | |
| 8005 | `llama` | |
| 8006 | `qwen3` | |
| 8007 | `granite4-1b-speech` | |
| 8008 | `qwen3-tts-1.7-custom` | |
| 8011–8015 | reserved by ACC for `llama-3b-fp8`, `llama-8b-fp8`, `qwen-coder-7b-awq`, `qwen-7b-gptq`, `mistral-7b-w8a8` | operator adds to vllmpunch-models.json when needed |

## See also

* [`deploy/host-configs/README.md`](../deploy/host-configs/README.md)
* [`test/history/README.md`](../test/history/README.md)
* `~/.claude/skills/acc-llm-test-history/SKILL.md`
* `vllmpunch` upstream repo
