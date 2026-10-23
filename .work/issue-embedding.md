## Summary
The baked `all-MiniLM-L6-v2` embedding model is written as a **HuggingFace
hub-cache tree** (via `cache_folder=`), but the runtime loads it as a **plain
model directory** (`SentenceTransformer(embedding_model_path)`). The two layouts
are incompatible, so embedding load fails and RAG / episodic memory silently
degrades on every agent-core image.

## Symptom
```
ValueError: Unrecognized model in /app/models/all-MiniLM-L6-v2.
Should have a `model_type` key in its config.json.
```
followed by:
```
ERROR acc.cognitive_core rag: embed failed; skip retrieval
WARNING acc.cognitive_core cognitive_core: episode persist failed: Unrecognized model …
```
LLM inference is unaffected (Anthropic 200 OK, tasks complete) — only retrieval
and episode persistence break, so the `episodes` LanceDB table stays empty and
agents never recall prior context.

## Root cause
The Containerfiles bake the model with `cache_folder=`:

```dockerfile
SentenceTransformer('all-MiniLM-L6-v2', cache_folder='/app/models/all-MiniLM-L6-v2')
```

which produces a hub-cache tree — the real files land under
`/app/models/all-MiniLM-L6-v2/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/<hash>/`
(config.json, model.safetensors, modules.json, 1_Pooling, tokenizer, …), and the
top level has only `CACHEDIR.TAG` / `.locks` / `models--…`.

But `LLMConfig.embedding_model_path` defaults to `/app/models/all-MiniLM-L6-v2`
and every backend loads it as a directory path:

- `acc/backends/llm_anthropic.py:47` — `SentenceTransformer(self._embedding_model_path)`
- `acc/backends/llm_openai_compat.py:274` — same
- `acc/backends/llm_vllm.py` — same

`SentenceTransformer('/app/models/all-MiniLM-L6-v2')` looks for `config.json` at
that top level, finds none, and raises. The bake and the load disagree on the
on-disk layout.

## Fix
Bake a **plain SentenceTransformer directory** so the top level has
`config.json` + `modules.json` + weights + tokenizer — matching the runtime's
directory-path load. Download to a scratch cache, `.save()` the plain dir, drop
the scratch tree:

```dockerfile
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('all-MiniLM-L6-v2', cache_folder='/tmp/st-cache').save('/app/models/all-MiniLM-L6-v2')" \
 && rm -rf /tmp/st-cache
```

Affects all three agent-core Containerfiles: `deploy/Containerfile.agent-core`,
`container/production/Containerfile.agent-core`, `container/beta/Containerfile.agent-core`.

## Verified live (bb3)
Rebuilt as `acc-agent-core-0.5.20` (re-materializing the plain dir offline from
the already-baked cache). Agent pod 1/1 Running, cage intact. Positive evidence:

- In-pod embed via the real code path (`build_llm_backend(config)._get_st_model().encode()`):
  OK, dim 384 — no `Unrecognized model`.
- LanceDB `episodes` table now persists rows (was 0); no `embed failed` /
  `episode persist failed`.
- Retrieval end-to-end: a task recalled a fact persisted by an earlier task —
  agent answered *"From the episode at [15:41:39]: the ACC bb3 sandbox mascot is
  Azure, a blue heron."*

Fix on branch `fix/agent-core-embedding-model-dir`.
