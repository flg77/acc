# Prompt caching

Every LLM call re-sends the large, stable role system prompt. Caching
that prefix cuts cost + latency. ACC's approach is **backend-independent
first** — it works on the edge (local vLLM / Ollama) with no external
dependency — with Anthropic's native cache as an optional DC accelerator.

## The independent core: a stable, cacheable prefix (PR-CA1)

Every model server caches a **repeated prompt prefix**: vLLM
(`--enable-prefix-caching`), Ollama / llama.cpp (automatic KV cache),
proxies, and Anthropic (`cache_control`). The one thing they all need is
that the prefix be **byte-identical** across calls.

So `acc.cognitive_core.build_system_prompt` emits a **stable per-role
system prompt** (purpose + persona + seed + skill/MCP ads + delegation).
The *variable* parts — the `RECENT_RELEVANT_EPISODES` RAG block and
`MEMORY_NOTES` — ride the **LLM user message** instead (they used to sit
in the middle of the system prompt, which defeated every cache). This
alone delivers caching on the edge, with no config and no cloud
dependency, and no behaviour change (same content, different message
slot; guardrails / Cat-A / persistence still see the bare task).

## Optional per-backend hint (PR-CA2)

`complete()` takes an optional `cache_prefix` flag, passed when
`ACC_LLM_ENABLE_PROMPT_CACHE` (or `LLMConfig.enable_prompt_cache`) is set
— **default off, optional in all modes**:

- **Anthropic** → sends the system prompt as a `cache_control: ephemeral`
  block and surfaces `cache_creation_input_tokens` /
  `cache_read_input_tokens` in `usage`.
- **vLLM / Ollama** → ignore the hint; their server prefix cache already
  hits on the stable prefix. Enable vLLM caching at deploy with
  `--enable-prefix-caching`.
- **openai_compat / llama_stack** → no-op (provider/proxy-dependent).

## Metrics (PR-CA3)

When a backend reports cache reads (Anthropic), the cumulative
`cache_read_tokens` + a hit ratio flow through `StressIndicators` → the
heartbeat → the **Performance pane** ("cache: N read (X% of input)").
Edge backends cache invisibly to the client, so the metric is
best-effort — the saving is real regardless.

## Notes

- Keep the role prefix stable: any change to purpose/persona/seed/skill
  ads busts the cache (expected on a role update). RAG + memory notes are
  deliberately kept OUT of the prefix.
- Anthropic only caches prefixes above a minimum token size; small role
  prompts simply won't cache (no error, no benefit).

## Tests

```
pytest tests/test_prompt_prefix_cache.py tests/test_backend_cache_hint.py \
       tests/test_cache_metrics.py -v
```
