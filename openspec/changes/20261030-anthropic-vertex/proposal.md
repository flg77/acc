# 20261030-anthropic-vertex — proposal

## Why

On the operator's employer laptop, Claude is available through Google Vertex AI
on the employer's project, not through an Anthropic API key (vault proposal 062,
backlog CS-07). ACC's `anthropic` backend only took an API key.
So the corporate Claude route could not serve any ACC role. Vertex is a
commercial API on a project the operator is entitled to, not a subscription, so
it belongs in-tree next to the API route rather than in a detached plugin.

## What changes

* **`acc/backends/llm_anthropic.py`** — `AnthropicBackend(provider=, vertex_project_id=,
  vertex_region=)`. With `provider="vertex"` it builds `anthropic.AsyncAnthropicVertex`
  with application default credentials and sends no API key. An empty project or
  region defers to the SDK's `ANTHROPIC_VERTEX_PROJECT_ID` and `CLOUD_ML_REGION`.
  Without `google-auth` it raises an `ImportError` that names the extra. An unknown
  provider is a `ValueError`. Everything after the client (messages, caching, images,
  usage, errors) is unchanged.
* **Config** — `llm.anthropic_provider` (`anthropic` | `vertex`, default
  `anthropic`), `llm.vertex_project_id` and `llm.vertex_region`, with env overrides
  `ACC_ANTHROPIC_PROVIDER`, `ACC_VERTEX_PROJECT_ID` and `ACC_VERTEX_REGION`. Both
  LLM construction sites (`acc.config` and `acc-cli`) pass them; both stay wrapped by
  the prompt recorder.
* **`models.yaml`** — `provider`, `vertex_project_id` and `vertex_region` on an
  entry, persisted on save. `model_env` always emits `ACC_ANTHROPIC_PROVIDER` for an
  anthropic entry, so a role moved off Vertex does not keep it.
* **`acc-cli config check`** — no `ANTHROPIC_API_KEY` error when the provider is `vertex`.
* **Packaging** — extra `vertex` (`google-auth[requests]`) and feature
  `features/vertex.yaml`. The base image is unchanged.
* **Docs** — `docs/llm-backends.md` gets a Vertex section; `models.yaml.example` gets
  a commented entry.

## What does not change

* The API-key route is the default and behaves as before.
