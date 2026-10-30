# 20261030-anthropic-vertex — tasks

Backlog CS-07 (vault `20-backlog/chief-of-staff`), proposal 062 phase C.

- [x] `AnthropicBackend` provider `vertex` → `AsyncAnthropicVertex` (ADC, no API key); SDK env defaults; extra named on ImportError
- [x] `llm.anthropic_provider` / `vertex_project_id` / `vertex_region` + env overrides; both construction sites
- [x] `ModelEntry.provider` / `vertex_project_id` / `vertex_region`; `_entry_to_dict`; `model_env`
- [x] `configstore.check`: no API-key error for vertex
- [x] extra `vertex`, `features/vertex.yaml`; docs, `models.yaml.example`
- [x] `tests/test_llm_anthropic_vertex.py` (11); config, models, backend and schema suites green
- [ ] PR
- [ ] lighthouse: ADC mounted read-only, `acc-cli llm test` against the employer's project (062 Q2)
