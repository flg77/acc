# Multimodel roles + reviewer

Run cheap/fast models for worker roles and a more powerful model for a
**reviewer** that critiques work and advises revisions — lifting result
quality at a fraction of the cost of running everything on the big model.

## Central model registry (`models.yaml`)

Models live in a central `models.yaml` at the repo root
(`acc.models.load_models`). Each entry maps a `model_id` to a backend +
model (+ optional `base_url` / `api_key_env`):

```yaml
models:
  - model_id: claude-sonnet
    backend: anthropic
    model: claude-sonnet-4-6
    label: "Claude Sonnet (powerful — reviewer)"
  - model_id: claude-haiku
    backend: anthropic
    model: claude-haiku-4-6
    label: "Claude Haiku (cheap — worker)"
  - model_id: ollama-llama32-3b
    backend: ollama
    model: "llama3.2:3b"
    base_url: "http://localhost:11434"
```

`models.yaml` is mounted read-only into `acc-tui`; override its location
with `ACC_MODELS_PATH`.

## Assigning a model to a sub-agent (1:1)

Each `AgentSpec` in `collective.yaml` can pin a `model` (a `model_id`):

```yaml
agents:
  - role: coding_agent_implementer
    replicas: 2
    model: claude-haiku       # cheap worker
  - role: reviewer
    replicas: 1
    model: claude-sonnet      # powerful reviewer
```

At synthesis time `acc.collective.roles_to_compose` resolves the
`model_id` via `acc.models.model_env` into the per-agent LLM env vars
(`ACC_LLM_BACKEND` + the backend-specific model/url/key), applied before
`extra_env` (so explicit env still wins). Unset/unknown → the collective
default model.

### From the TUI

Ecosystem → **Agentset** tab: each agent row shows its **Model**;
highlight a row, pick a model from the **Model →** dropdown (populated
from the registry), and click **Set model on selected**. That writes the
`model_id` into the agent in the editor YAML; **Save** + **Apply** then
persist and recreate just that agent on the new model (via the existing
apply-watcher).

## The reviewer (per-step critic loop)

`roles/reviewer/role.yaml` is a generic, model-agnostic reviewer. Its
seed_context makes the model emit a single JSON verdict:

```json
{"verdict": "GOOD|PARTIAL|NEEDS_REVISE|BAD",
 "critique": "specific, actionable feedback",
 "prompt_patch": {"append": "optional extra instruction"}}
```

The agent surfaces that verdict as `eval_outcome` on its `TASK_COMPLETE`
(`acc.agent._extract_eval_outcome`). The arbiter's `PlanExecutor`
(`plan._maybe_reissue_for_revise`) then, on **NEEDS_REVISE**, re-issues
the reviewed step with the critique (and optional prompt-patch, gated by
the step's `enable_prompt_patches` + Cat-A A-021), up to the step's
`max_iterations`. Pair the reviewer with a powerful model and the
workers with cheap ones.

## Preset

`collective.reviewer.yaml` wires cheap `coding_agent_implementer` /
`coding_agent_tester` workers + a single powerful `reviewer`:

```bash
./acc-deploy.sh apply collective.reviewer.yaml
podman inspect acc-cell-reviewer-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep MODEL
```

Single-instance reviewer (multiple reviewers fragment the verdict).
Cat-A governance is model-agnostic and applies to every model.

## Tests

```
pytest tests/test_models_registry.py \
       tests/test_agentset_model_dropdown.py \
       tests/test_reviewer_loop.py \
       tests/test_iteration_loop.py -v
```
