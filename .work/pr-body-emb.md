## What
`acc-agent-core` bakes the `all-MiniLM-L6-v2` embedding model as a HuggingFace
**hub-cache tree** (via `cache_folder=`) but the runtime loads it as a **plain
model directory**. The layouts are incompatible, so embedding load fails and
RAG / episodic memory silently break on every agent-core image.

Fixes #183.

## Root cause
The Containerfiles bake with:

```dockerfile
SentenceTransformer('all-MiniLM-L6-v2', cache_folder='/app/models/all-MiniLM-L6-v2')
```

→ the real files land under
`/app/models/all-MiniLM-L6-v2/models--…/snapshots/<hash>/` (config.json,
model.safetensors, modules.json, 1_Pooling, tokenizer) with **no top-level
config.json**. But `LLMConfig.embedding_model_path` defaults to
`/app/models/all-MiniLM-L6-v2` and every backend
(`llm_anthropic` / `llm_openai_compat` / `llm_vllm`) loads it as a directory:
`SentenceTransformer(embedding_model_path)`. That path has no top-level
config.json, so it raises:

```
ValueError: Unrecognized model in /app/models/all-MiniLM-L6-v2.
Should have a `model_type` key in its config.json.
→ rag: embed failed; skip retrieval
→ cognitive_core: episode persist failed
```

LLM inference is unaffected; only retrieval + episode persistence break (empty
`episodes` table, no recall of prior context).

## Fix
Bake a **plain SentenceTransformer directory** (top-level config.json etc.),
matching the directory-path load convention — download to a scratch cache,
`.save()` the loadable dir, drop the scratch tree:

```dockerfile
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('all-MiniLM-L6-v2', cache_folder='/tmp/st-cache').save('/app/models/all-MiniLM-L6-v2')" \
 && rm -rf /tmp/st-cache
```

Applied to all three agent-core Containerfiles: `deploy/`, `container/production/`,
`container/beta/`.

## Verified live (bb3, OCP 4.21)
Rebuilt as `acc-agent-core-0.5.20` (re-materializing the plain dir offline from
the already-baked cache; openshell CLI + iproute2 preserved → OpenShell Model-2
cage intact). Agent pod `bb3-collective-01-assistant-0` 1/1 Running.

- In-pod embed via the real code path (`build_llm_backend(config)._get_st_model().encode()`):
  OK, dim 384 — no `Unrecognized model`.
- LanceDB `episodes` table persists rows (was 0); no `embed failed` / `episode persist failed`.
- Retrieval end-to-end — a task recalled a fact persisted by an earlier task:

  > From the episode at [15:41:39]: the ACC bb3 sandbox mascot is **Azure**, a **blue** heron.

## Notes
- Independent of PR #182 (entrypoint PATH + rhoai-anthropic validator). Separate
  branch/issue by request; no overlap in touched files.
