# Endpoint fixtures — provenance

Captured **2026-08-26** from the live lighthouse edge deployment, not authored.
Re-capture rather than hand-edit; a field that drifts is the thing these exist
to catch.

| Fixture | Source |
|---|---|
| `vllm_v1_models.json` | `GET /v1/models` |
| `vllm_tokenize.json` | `POST /tokenize` |
| `vllm_embeddings_unsupported.json` | `POST /v1/embeddings` on a chat-only model |
| `vllm_metrics.txt` | `GET /metrics`, filtered to the cache/KV/request families |
| `vllm_version.json` | `GET /version` |

* **Host:** lighthouse (10.199.12.9), container `vllm-llama-32-3b-fp8`,
  published `0.0.0.0:8022 -> 8000`.
* **Server:** vLLM `0.11.2+rhai5` (RHAIIS build).
* **Model:** `RedHatAI/Llama-3.2-3B-Instruct-FP8`, served `max_model_len=8192`.

## Three things the real responses changed in the design

1. **`POST /v1/embeddings` returns HTTP 200 with an error envelope** on a
   chat-only model (`{"error":{...,"code":400}}`). A status-code-only probe
   reports embeddings as *available*. Detection must parse the body. An
   unrouted path (`/v1/nonexistent`) does return a real 404.
2. **`vllm:cache_config_info` makes prefix caching declarative** —
   `enable_prefix_caching="True"` is a label, so "is it on" needs no
   behavioural A/B. It also carries `cache_dtype`, `gpu_memory_utilization`,
   `block_size` and `num_gpu_blocks`, from which the KV pool follows:
   6032 x 16 = 96,512 tokens, or ~11.8 concurrent full-window sequences at
   8192. That is the shared-KV figure RP-04 Phase 6 wanted and could not name.
3. **`POST /tokenize` also returns `max_model_len`**, so the window has a
   second independent source on this backend.

## The measurement HG-12 asked for

`vllm:prefix_cache_hits_total / vllm:prefix_cache_queries_total`
= 29040 / 31185 = **93.1%**. PR-CA1 works, on the deployment that matters, and
this is the first time it has been measured rather than assumed.

Note `vllm:external_prefix_cache_*` is a *different* family — cross-instance KV
connector sharing, 0.0 here. Reading it instead of the plain family would report
a 0% hit rate on a cache that is in fact hitting 93%.

## Not captured

No Ollama `api_show.json`: no ACC host currently runs Ollama. The Ollama parser
therefore ships against an authored fixture and is marked lower-confidence in
`acc/endpoint_profile.py` until a real capture exists.
