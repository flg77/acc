# Role → Model mapping

How to choose which LLM each role's agents run on. As of 2026-06-22 the
**Configuration** screen (pane 8) is the canonical place to do this.

## TL;DR

1. Put your Anthropic key in `.env` (kept local, never committed):
   `ANTHROPIC_API_KEY=sk-...` — the backend now reads `ANTHROPIC_API_KEY` **or**
   `ACC_ANTHROPIC_API_KEY`.
2. TUI → **8 Configuration** → **ROLE → MODEL (active agentset)**.
3. Click **Seed split defaults** → control/review roles get the strongest model
   (`claude-opus`), bulk workers get a cheap one (`maas-qwen3-14b`). Or assign per
   role with the **role** + **model** dropdowns → **Assign**.
4. **Re-apply** the agentset (`./acc-deploy.sh apply <name>`) for it to take effect.

## The three layers (and which wins)

Model selection resolves through three layers — later overrides earlier:

| Layer | Sets | Where |
|---|---|---|
| Global default | one backend+model for the whole stack | `acc-config.yaml` `llm:` ← `.env` `ACC_LLM_*` (Configuration's top form) |
| Registry | the named models you can assign | `models.yaml` (Configuration shows it read-only) |
| **Per-role** | the model a role's agents run on | `collective.yaml` `AgentSpec.model` ← **Configuration ROLE → MODEL** |

> Setting `ANTHROPIC_API_KEY` alone does **not** switch a running agent to Claude —
> it only makes the key available. You still pick Claude either as the global
> default (Backend=`anthropic`, Model=`claude-opus-4-8`, Save & reload) **or**
> per role in ROLE → MODEL. An empty per-role model = the role inherits the global
> default.

## The default split (locked policy)

`Seed split defaults` applies the cost-aware split:

- **Strongest model** (`claude-opus`) → the control + review roles:
  `assistant`, `reviewer`, `orchestrator`, `compliance_officer`, `arbiter`.
- **Cheap worker model** (`maas-qwen3-14b`) → everything else (coding / research /
  business / substrate roles).

This is the high-ROI pattern (PR-MM3): pay for quality where decisions and review
happen, run bulk work cheap. Override any role individually with **Assign**.

## models.yaml

Each entry is a `model_id` you can assign. The strongest Anthropic tier:

```yaml
- model_id: claude-opus
  backend: anthropic
  model: claude-opus-4-8
  api_key_env: ANTHROPIC_API_KEY
  label: "Claude Opus 4.8 (STRONGEST — control + reviewer)"
```

Add your own entries; the key value never lives in `models.yaml` (only the env
var **name**). At synthesis `acc.models.model_env` turns the chosen entry into the
agent's LLM env vars.

## What persists where

ROLE → MODEL writes `AgentSpec.model` into the **active** `collective.yaml`
(resolved as `ACC_COLLECTIVE_PATH` > `/app/collective.yaml` > `./collective.yaml`).
The Ecosystem → **Agentset** tab still offers the same assignment per individual
agent (an agentset can run two agents of one role on different models);
Configuration is the per-**role** default.

## Verify it took effect

After re-apply, Configuration → **LIVE BACKENDS (per agent)** shows what each
running agent actually resolved to. If a role still shows the old model, you
likely edited the value but didn't re-apply the agentset.

## Programmatic / agent path

The same engine (`acc/role_model_map.py`) backs the Assistant: it can read the
current mapping and propose changes. CLI-equivalent: edit `AgentSpec.model` in
`collective.yaml` and `./acc-deploy.sh apply`.
