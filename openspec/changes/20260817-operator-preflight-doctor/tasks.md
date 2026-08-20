# Tasks: Operator preflight — one command that says whether a deployment is sane

**Change ID:** 20260817-operator-preflight-doctor
**Branch:** `feat/operator-preflight-doctor`

---

## Phase 1 — Check framework

- [ ] `[1]` `acc/preflight.py` — `Check`, `Result`, severity classes, registry
- [ ] `[2]` Context object: resolved config paths, registry, env var **names**, container state (optional)
- [ ] `[3]` Unit tests for the framework with fake checks

## Phase 2 — The documented failures

- [ ] `[4]` Check: every `role_models` value resolves to a known `model_id`
- [ ] `[5]` Check: every referenced `api_key_env` name is present in the environment
- [ ] `[6]` Check: exactly one `role_models` key per file (duplicate-key guard)
- [ ] `[7]` Check: endpoint reachability, probing the **gateway root** as well as the model
- [ ] `[8]` Check: declared-vs-running drift (config mtime vs agent start; image vs checkout)
- [ ] `[9]` Regression test per check against a deliberately broken fixture

## Phase 3 — Surfaces

- [ ] `[10]` `acc-cli doctor` with `--json` and exit codes
- [ ] `[11]` Call the same registry from one other surface (TUI diagnostics or web GUI)
- [ ] `[12]` Live verification on an edge node: healthy run is clean, broken run names the fault

