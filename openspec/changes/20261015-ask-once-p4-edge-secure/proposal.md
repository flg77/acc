# 20261015-ask-once-p4-edge-secure — proposal

**058 Phase 4: an `edge-secure` profile. Guardrails that block, MLflow,
promptfoo on the golden prompts, and Garak as a release gate.** The
operator's statement of 2026-10-15: "Guardrails via Garak etc. also early on
on the Edge, also Edge profiles need to be attachable to MLFlow, promptfoo
evals etc." Q6: promptfoo "in a container which can be enabled by the
operator (also from within the webgui)". Q5 (a runtime guard model) is open.

## What changes

- **`evals:` configuration** (`EvalsConfig`): `mlflow_tracking_uri`,
  `promptfoo_enabled`, `promptfoo_interval_s`, `garak_gate` and
  `garak_probes`. Env overrides are `ACC_PROMPTFOO_ENABLED` and
  `ACC_GARAK_GATE`.
- **Profiles** may set `compliance.owasp_enforce` and `evals.*`.
  `compliance.owasp_enforce` and `evals.garak_gate` are **posture**: a
  browser can only propose them through oversight.
- **`profiles/edge_secure.yaml`**: compliance on, OWASP and Cat-A enforcing,
  the garak gate on, promptfoo left to the operator. It requires
  `ACC_SANDBOX_NAME`, `OPENSHELL_GATEWAY` and `ACC_MLFLOW_TRACKING_URI`.
- **MLflow:** `mlflow_runs` falls back to `evals.mlflow_tracking_uri` when
  the env var is unset, so the profile attaches an edge host.
- **promptfoo** (`acc/evals/promptfoo.py`, `acc-cli evals promptfoo-config`)
  builds a config from the golden prompts against the OpenAI-compatible
  endpoint (`model` = role). The asserts reuse `output_contains`,
  `output_matches_regex` and `reply_non_empty`.
  `deploy/evals/podman-compose.evals.yml` adds `acc-promptfoo` (profile
  `evals`), which reads the switch from `acc-config.yaml` every round.
- **WebGUI → Settings → Evals:** the promptfoo switch, written through
  `/api/config/set`. The posture settings are shown read-only.
- **Garak** (`acc/evals/garak.py`, `acc-cli evals garak-gate`) summarises
  the report per probe and compares it with a baseline. A drop beyond the
  tolerance, or a probe that was not run, blocks with exit 1. The first run
  becomes the baseline. `acc-garak` is a one-shot service (profile
  `evals-gate`).

## Not in this change

- A runtime guard model (Llama Guard / Granite Guardian): 058 Q5 is open.
- Wiring `garak-gate` into `acc-promote` / `tools/gate`. The command and its
  exit code are the hook; the release routine has to call it.
- The image tags are pinned but were not pulled when this was written;
  verify them before the first run.
- The AgentBOM does not carry the profile or baselines yet (058 step 21).
