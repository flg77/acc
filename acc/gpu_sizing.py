"""GPU memory sizing for a vLLM-served model — weights, KV cache, concurrency.

The arithmetic behind the ``gpu_capacity_estimate`` skill (backlog 019, the
GPU capacity advisor, proposal 057): given a Hugging Face ``config.json`` and a
GPU, how much of each GPU the weights take, how many KV-cache tokens fit in
what is left, how many sequences that serves at once, and the smallest tensor
parallel size that fits at all.

Pure functions, standard library only, no network: the agent fetches
``config.json`` itself (``http_get``) and passes it in.

Provenance
----------
Ported from the naive KV-cache estimator and the vLLM memory model of
``aisimulate`` (redhat-performance/aisimulate, the SDK behind ConfigIQ,
https://configiq.xyz), itself derived from NVIDIA AIConfigurator.
Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES, Apache-2.0:

* geometry parsing, per-token KV bytes (GQA/MHA and MLA), the sliding-window
  hybrid inverse and the weight estimate — ``sdk/memory.py``
  (``NaiveKVCacheEstimator``);
* the vLLM "fraction of total" budget, activation coefficients, the 70 MiB
  activation floor, the NCCL table and the 3.5 GiB runtime reserve —
  ``sdk/memory.py`` (``kv_cache_budget_bytes``),
  ``sdk/backends/base_backend.py``, ``sdk/backends/trtllm_backend.py``,
  ``sdk/backends/vllm_backend.py``;
* the GPU table — ``systems/*.yaml``.

Two deliberate departures, both toward the real number: attention weights count
K/V at the KV-head width (GQA) instead of ``4 * hidden**2``, and MoE experts
are sharded by the tensor-parallel size (vLLM's default without expert
parallelism) instead of replicated.

This is the *naive* model. It does not predict latency (TTFT/TPOT) — that needs
ConfigIQ's measured kernel databases (its ``/recommend`` API); treat the result
as a capacity floor to check against live ``vllm:kv_cache_usage_perc``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Optional

GIB = 1 << 30
MIB = 1 << 20

# vLLM >= 0.22 default gpu_memory_utilization, and aisimulate's 2 % KV margin.
DEFAULT_GPU_MEMORY_UTILIZATION = 0.92
KV_CACHE_MEMORY_TOLERANCE = 0.02
# CUDA context, cuBLAS workspaces, CUDA graphs — the per-GPU reserve
# aisimulate carries as ``misc.other_mem`` (3.5 GiB "for safer deployment").
RUNTIME_RESERVE_BYTES = int(3.5 * GIB)
MIN_ACTIVATION_BYTES = 70 * MIB
# vLLM's default chunked-prefill token budget — the activation peak scales
# with the tokens in one forward pass, not with the context length.
DEFAULT_MAX_NUM_BATCHED_TOKENS = 8192
# NCCL buffers per GPU by tensor-parallel size (342 MiB / 392 MiB measured).
_NCCL_BYTES = {1: 0, 2: 358_612_992, 4: 411_041_792, 8: 411_041_792}

# Activation coefficient by model family and min(tp, 8).
_ACTIVATION_COEFFICIENTS = {
    "llama": {1: 11, 2: 6.5, 4: 5, 8: 5},
    "moe": {1: 22, 2: 13, 4: 10, 8: 10},
    "default": {1: 10, 2: 6, 4: 5, 8: 5},
}

TP_CANDIDATES = (1, 2, 4, 8)


@dataclass(frozen=True)
class GPUSpec:
    gpu_id: str
    label: str
    memory_bytes: int
    bandwidth_bytes_s: int
    fp8: bool
    gpus_per_node: int = 8


def _gpu(gpu_id: str, label: str, mem: int, bw: int, fp8: bool, per_node: int = 8) -> GPUSpec:
    return GPUSpec(gpu_id, label, mem, bw, fp8, per_node)


# From aisimulate ``systems/<id>.yaml`` (gpu.mem_capacity, gpu.mem_bw,
# fp8 = sm_version >= 89, node.num_gpus_per_node).
GPUS: dict[str, GPUSpec] = {
    g.gpu_id: g
    for g in (
        _gpu("a30", "NVIDIA A30 24GB", 25_769_803_776, 933_000_000_000, False),
        _gpu("a100_pcie", "NVIDIA A100 80GB PCIe", 85_899_345_920, 1_935_000_000_000, False),
        _gpu("a100_sxm", "NVIDIA A100 80GB SXM", 85_899_345_920, 2_039_000_000_000, False),
        _gpu("l4", "NVIDIA L4 24GB", 25_769_803_776, 300_000_000_000, True),
        _gpu("l40s", "NVIDIA L40S 48GB", 48_305_799_168, 864_000_000_000, True),
        _gpu("h100_pcie", "NVIDIA H100 80GB PCIe", 85_899_345_920, 2_000_000_000_000, True),
        _gpu("h100_sxm", "NVIDIA H100 80GB SXM", 85_899_345_920, 3_350_000_000_000, True),
        _gpu("h200_sxm", "NVIDIA H200 141GB SXM", 151_397_597_184, 4_800_000_000_000, True),
        _gpu("b200_sxm", "NVIDIA B200 180GB SXM", 193_273_528_320, 7_700_000_000_000, True),
        _gpu("b300_sxm", "NVIDIA B300 SXM", 288_400_343_040, 7_750_000_000_000, True),
        _gpu("gb200", "NVIDIA GB200", 198_674_743_296, 8_000_000_000_000, True, 4),
        _gpu("gb300", "NVIDIA GB300", 298_013_687_808, 8_000_000_000_000, True, 4),
        _gpu("rtx_pro_6000_server", "NVIDIA RTX PRO 6000 Server 96GB",
             102_641_958_912, 1_792_000_000_000, True),
    )
}


# ---------------------------------------------------------------------------
# config.json -> geometry
# ---------------------------------------------------------------------------


def _dimension(cfg: dict, *keys: str) -> Optional[int]:
    """First positive integer among equivalent HF keys (``null`` and bools skipped)."""
    for key in keys:
        value = cfg.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, float) and not value.is_integer():
            continue
        try:
            parsed = int(value)
        except (OverflowError, TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _flatten(hf_config: dict) -> dict:
    """Multimodal configs nest the language model under ``text_config``."""
    text = hf_config.get("text_config") or hf_config.get("llm_config")
    if isinstance(text, dict):
        merged = dict(hf_config)
        merged.update(text)
        return merged
    return hf_config


def dtype_bytes(name: Any, default: float = 2) -> float:
    """Byte width of a dtype name (``bfloat16``, ``fp8``, ``int4`` ...)."""
    s = str(name or "").lower()
    if not s or s == "auto":
        return default
    if s in ("float32", "fp32"):
        return 4
    if s in ("bfloat16", "float16", "bf16", "fp16", "half"):
        return 2
    if "fp8" in s or "float8" in s or "int8" in s:
        return 1
    if "int4" in s or "fp4" in s or "nvfp4" in s or "mxfp4" in s:
        return 0.5
    return default


def weight_dtype_bytes(hf_config: dict) -> float:
    """Bytes per weight parameter: the quantization config wins over torch_dtype."""
    cfg = _flatten(hf_config)
    base = dtype_bytes(cfg.get("torch_dtype") or cfg.get("dtype"))
    q = cfg.get("quantization_config")
    if not isinstance(q, dict):
        return base
    bits = _dimension(q, "bits", "w_bit", "weight_bits")
    if bits:
        return bits / 8
    method = str(q.get("quant_method") or "").lower()
    if method in ("fp8", "fbgemm_fp8"):
        return 1
    if method in ("mxfp4", "nvfp4", "modelopt_fp4"):
        return 0.5
    groups = q.get("config_groups")
    if isinstance(groups, dict):  # compressed-tensors
        for group in groups.values():
            w = (group or {}).get("weights") or {}
            nb = _dimension(w, "num_bits")
            if nb:
                return nb / 8
    return base


def geometry(hf_config: dict) -> dict:
    """The scalars the byte formulas use, from an HF ``config.json``.

    Missing values stay ``None``/``0`` so the formulas fail closed.
    """
    cfg = _flatten(hf_config)
    hidden = _dimension(cfg, "hidden_size", "n_embd", "n_embed", "d_model")
    layers = _dimension(cfg, "num_hidden_layers", "n_layer", "num_layers")
    heads = _dimension(cfg, "num_attention_heads", "n_head", "num_heads")
    head_dim = _dimension(cfg, "head_dim", "attention_head_dim")
    if head_dim is None and hidden and heads:
        head_dim = hidden // heads
    num_experts = _dimension(cfg, "n_routed_experts", "num_local_experts", "num_experts") or 0
    inter = _dimension(cfg, "intermediate_size", "ffn_dim", "n_inner", "ffn_hidden_size")
    if inter is None and hidden and not num_experts:
        inter = 4 * hidden
    kv_heads = _dimension(cfg, "num_key_value_heads", "num_kv_heads", "n_head_kv", "n_kv_heads")
    if cfg.get("multi_query") and not cfg.get("new_decoder_architecture"):
        kv_heads = 1  # legacy Falcon

    window = _dimension(cfg, "sliding_window", "sliding_window_size")
    num_swa = num_global = None
    layer_types = cfg.get("layer_types")
    if isinstance(layer_types, list) and layer_types:
        num_swa = sum(1 for t in layer_types if "sliding" in str(t).lower())
        num_global = len(layer_types) - num_swa
    else:
        pattern = _dimension(cfg, "sliding_window_pattern")
        if window and layers and pattern:
            num_global = layers // pattern
            num_swa = layers - num_global
    if not window:
        num_swa = num_global = None

    model_type = str(cfg.get("model_type") or "").lower()
    family = "moe" if num_experts else ("llama" if "llama" in model_type else "default")
    return {
        "model_type": model_type,
        "family": family,
        "hidden": hidden,
        "layers": layers,
        "heads": heads,
        "kv_heads": kv_heads or heads,
        "head_dim": head_dim,
        "inter": inter,
        "vocab": _dimension(cfg, "vocab_size"),
        "num_experts": num_experts,
        "moe_inter": _dimension(cfg, "moe_intermediate_size") or inter or 0,
        "shared_experts": _dimension(cfg, "n_shared_experts", "num_shared_experts") or 0,
        "kv_lora_rank": _dimension(cfg, "kv_lora_rank"),
        "qk_rope_head_dim": _dimension(cfg, "qk_rope_head_dim"),
        "sliding_window": window,
        "num_swa_layers": num_swa,
        "num_global_layers": num_global,
        "tie_word_embeddings": bool(cfg.get("tie_word_embeddings", False)),
        "max_position_embeddings": _dimension(cfg, "max_position_embeddings", "max_seq_len"),
    }


# ---------------------------------------------------------------------------
# geometry -> bytes
# ---------------------------------------------------------------------------


def _ceil_div(a: int, b: int) -> int:
    return -(-a // max(b, 1))


def kv_bytes_per_token(geom: dict, *, tp: int, kv_bytes: float) -> Optional[float]:
    """Per-GPU KV bytes for one token (the full-context rate), or ``None``.

    MLA (``kv_lora_rank``): ``layers * (kv_lora_rank + qk_rope_head_dim) * bytes``,
    replicated on every rank. GQA/MHA: ``2 * ceil(kv_heads / tp) * head_dim *
    layers * bytes``.
    """
    layers = geom.get("layers")
    if not layers:
        return None
    if geom.get("kv_lora_rank"):
        rope = geom.get("qk_rope_head_dim")
        if not rope:
            return None
        return layers * (geom["kv_lora_rank"] + rope) * kv_bytes
    kv_heads, head_dim = geom.get("kv_heads"), geom.get("head_dim")
    if not kv_heads or not head_dim:
        return None
    return 2 * _ceil_div(kv_heads, tp) * head_dim * layers * kv_bytes


def tokens_from_kv_bytes(geom: dict, budget: float, *, tp: int, kv_bytes: float) -> int:
    """KV byte budget -> token capacity.

    Sliding-window hybrids (standard attention only): every layer grows up to
    the window, past it only the global layers do — so capacity is not
    under-counted from the full-context rate. Everything else is linear.
    """
    if budget <= 0:
        return 0
    window, n_swa, n_global = (
        geom.get("sliding_window"), geom.get("num_swa_layers"), geom.get("num_global_layers"),
    )
    if (window and n_swa and n_global is not None and not geom.get("kv_lora_rank")
            and geom.get("kv_heads") and geom.get("head_dim")):
        per_layer = 2 * _ceil_div(geom["kv_heads"], tp) * geom["head_dim"] * kv_bytes
        swa_rate, global_rate = n_swa * per_layer, n_global * per_layer
        full_rate = swa_rate + global_rate
        at_window = full_rate * window
        if budget <= at_window:
            return int(budget // full_rate)
        if global_rate <= 0:
            return int(window)
        return int(window + (budget - at_window) // global_rate)
    per_token = kv_bytes_per_token(geom, tp=tp, kv_bytes=kv_bytes) or 0
    return int(budget // per_token) if per_token > 0 else 0


def parameter_count(geom: dict) -> Optional[int]:
    """Rough total parameter count from the geometry, or ``None``."""
    hidden, layers, vocab, inter = (geom.get(k) for k in ("hidden", "layers", "vocab", "inter"))
    if not (hidden and layers and vocab and (inter or geom.get("num_experts"))):
        return None
    embed = (1 if geom.get("tie_word_embeddings") else 2) * vocab * hidden
    heads, kv_heads, head_dim = geom.get("heads"), geom.get("kv_heads"), geom.get("head_dim")
    if heads and kv_heads and head_dim:
        attn = 2 * hidden * heads * head_dim + 2 * hidden * kv_heads * head_dim  # q,o + k,v
    else:
        attn = 4 * hidden * hidden
    if geom.get("num_experts"):
        moe_inter = geom.get("moe_inter") or inter
        ffn = (geom["num_experts"] + geom.get("shared_experts", 0)) * 3 * hidden * moe_inter
    else:
        ffn = 3 * hidden * inter
    return embed + layers * (attn + ffn)


def weight_bytes_per_gpu(params: int, *, tp: int, pp: int = 1, bytes_per_param: float) -> float:
    """Weights resident on one GPU: everything sharded TP x PP (experts included)."""
    return params * bytes_per_param / (max(tp, 1) * max(pp, 1))


def activation_bytes(geom: dict, *, tp: int, max_num_batched_tokens: int) -> float:
    """Peak activation memory of one forward pass, floored at 70 MiB."""
    width = (geom.get("heads") or 0) * (geom.get("head_dim") or 0) or (geom.get("hidden") or 0)
    coeffs = _ACTIVATION_COEFFICIENTS.get(geom.get("family", "default"), _ACTIVATION_COEFFICIENTS["default"])
    coeff = coeffs[min((t for t in TP_CANDIDATES if t >= min(tp, 8)), default=8)]
    return max(2 * max_num_batched_tokens * width * coeff, MIN_ACTIVATION_BYTES)


def nccl_bytes(tp: int) -> int:
    if tp <= 1:
        return 0
    return _NCCL_BYTES[min((t for t in TP_CANDIDATES if t >= tp), default=8)]


# ---------------------------------------------------------------------------
# The estimate
# ---------------------------------------------------------------------------


def _per_gpu(
    geom: dict,
    gpu: GPUSpec,
    *,
    tp: int,
    params: int,
    w_bytes: float,
    kv_bytes: float,
    utilization: float,
    max_num_batched_tokens: int,
) -> dict:
    weights = weight_bytes_per_gpu(params, tp=tp, bytes_per_param=w_bytes)
    activations = activation_bytes(geom, tp=tp, max_num_batched_tokens=max_num_batched_tokens)
    nccl = nccl_bytes(tp)
    non_kv = weights + activations + nccl + RUNTIME_RESERVE_BYTES
    # vLLM applies gpu_memory_utilization to TOTAL memory, then subtracts
    # everything that is not KV cache (aisimulate kv_cache_budget_bytes, of_total).
    budget = gpu.memory_bytes * utilization * (1 - KV_CACHE_MEMORY_TOLERANCE) - non_kv
    return {
        "tensor_parallel_size": tp,
        "weights_bytes": int(weights),
        "activations_bytes": int(activations),
        "nccl_bytes": nccl,
        "runtime_reserve_bytes": RUNTIME_RESERVE_BYTES,
        "kv_cache_bytes": int(max(budget, 0)),
        "kv_cache_tokens": tokens_from_kv_bytes(geom, budget, tp=tp, kv_bytes=kv_bytes),
    }


def estimate(
    hf_config: dict,
    gpu_id: str,
    *,
    max_model_len: Optional[int] = None,
    tensor_parallel_size: Optional[int] = None,
    gpu_memory_utilization: float = DEFAULT_GPU_MEMORY_UTILIZATION,
    kv_cache_dtype: str = "auto",
    weight_bytes_per_param: Optional[float] = None,
    num_parameters: Optional[int] = None,
    avg_tokens_per_request: Optional[int] = None,
    target_concurrency: Optional[int] = None,
    max_num_batched_tokens: int = DEFAULT_MAX_NUM_BATCHED_TOKENS,
) -> dict:
    """Size one vLLM replica of the model on ``gpu_id``.

    With ``tensor_parallel_size`` unset, picks the smallest of 1/2/4/8 (up to
    the GPUs in one node) whose KV cache holds at least one ``max_model_len``
    sequence. Raises ``ValueError`` when the config lacks what the formulas need
    or the GPU is unknown — never a guessed number.
    """
    gpu = GPUS.get(str(gpu_id).lower())
    if gpu is None:
        raise ValueError(f"unknown gpu {gpu_id!r}; known: {', '.join(sorted(GPUS))}")
    if not 0 < gpu_memory_utilization <= 1:
        raise ValueError("gpu_memory_utilization must be in (0, 1]")
    geom = geometry(hf_config)
    params = int(num_parameters) if num_parameters else parameter_count(geom)
    if not params:
        raise ValueError(
            "insufficient model metadata: need hidden_size, num_hidden_layers, vocab_size "
            "and intermediate_size (or num_parameters)"
        )
    w_bytes = float(weight_bytes_per_param) if weight_bytes_per_param else weight_dtype_bytes(hf_config)
    model_dtype = dtype_bytes(_flatten(hf_config).get("torch_dtype") or _flatten(hf_config).get("dtype"))
    kv_bytes = dtype_bytes(kv_cache_dtype, default=model_dtype)
    if kv_bytes_per_token(geom, tp=1, kv_bytes=kv_bytes) is None:
        raise ValueError(
            "insufficient model metadata: need num_hidden_layers and the KV head geometry "
            "(num_key_value_heads/head_dim, or kv_lora_rank + qk_rope_head_dim for MLA)"
        )
    seq_len = int(max_model_len or geom.get("max_position_embeddings") or 0)
    if seq_len <= 0:
        raise ValueError("max_model_len is required (config.json has no max_position_embeddings)")

    def size(tp: int) -> dict:
        return _per_gpu(geom, gpu, tp=tp, params=params, w_bytes=w_bytes, kv_bytes=kv_bytes,
                        utilization=gpu_memory_utilization, max_num_batched_tokens=max_num_batched_tokens)

    if tensor_parallel_size:
        chosen = size(int(tensor_parallel_size))
    else:
        options = [t for t in TP_CANDIDATES if t <= gpu.gpus_per_node]
        chosen = next((s for s in map(size, options) if s["kv_cache_tokens"] >= seq_len), size(options[-1]))

    tokens = chosen["kv_cache_tokens"]
    tp = chosen["tensor_parallel_size"]
    fits = tokens >= seq_len
    per_request = int(avg_tokens_per_request) if avg_tokens_per_request else seq_len
    result: dict[str, Any] = {
        "gpu": asdict(gpu),
        "model": {
            "model_type": geom["model_type"],
            "family": geom["family"],
            "num_parameters": params,
            "weight_bytes_per_param": w_bytes,
            "kv_cache_bytes_per_element": kv_bytes,
            "kv_bytes_per_token": kv_bytes_per_token(geom, tp=tp, kv_bytes=kv_bytes),
            "sliding_window": geom["sliding_window"],
            "mla": bool(geom["kv_lora_rank"]),
        },
        "max_model_len": seq_len,
        "gpu_memory_utilization": gpu_memory_utilization,
        "per_gpu": chosen,
        "fits": fits,
        "gpus_per_replica": tp,
        "max_concurrent_sequences_at_max_len": tokens // seq_len,
        "avg_tokens_per_request": per_request,
        "max_concurrent_sequences_at_avg": tokens // per_request if per_request else 0,
        "serving_config": {
            "tensor_parallel_size": tp,
            "max_model_len": seq_len,
            "gpu_memory_utilization": gpu_memory_utilization,
            "kv_cache_dtype": kv_cache_dtype,
            "max_num_seqs": max(1, min(tokens // per_request if per_request else 0, 256)),
        },
    }
    if target_concurrency:
        per_replica = result["max_concurrent_sequences_at_avg"]
        replicas = math.ceil(int(target_concurrency) / per_replica) if per_replica else None
        result["target_concurrency"] = int(target_concurrency)
        result["replicas_needed"] = replicas
        result["gpus_needed"] = replicas * tp if replicas else None
    if not fits:
        result["note"] = (
            f"does not fit: even at tensor_parallel_size={tp} the KV cache holds "
            f"{tokens} tokens < max_model_len {seq_len}; lower max_model_len, "
            "quantize the weights or KV cache, or use a larger GPU"
        )
    return result


def list_gpus() -> list[dict]:
    return [asdict(g) for g in GPUS.values()]
