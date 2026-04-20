# Tasks: Phase 1a — Backend Abstraction Layer

**Change ID:** 20260403-phase1a-backend-abstraction
**Branch:** `feature/ACC-1-phase1a-backend-abstraction-layer`

---

## Phase 1 — Foundation

- [x] `[0]` Initialize git repo, commit existing docs + regulatory_layer
- [ ] `[1]` Create `pyproject.toml` with all dependencies and project metadata
- [ ] `[2]` Create `acc-config.yaml` (standalone mode defaults) + `.env.example`
- [ ] `[3]` Create `acc/__init__.py` (package marker + version constant)
- [ ] `[4]` Create `acc/backends/__init__.py` — 4 Protocol interfaces + `BackendConnectionError` / `LLMCallError`
- [ ] `[5]` Create `acc/config.py` — `ACCConfig` pydantic model + `load_config()` + `build_backends()` factory

## Phase 2 — Core Backend Implementations

- [ ] `[6]`  Create `acc/backends/signaling_nats.py` — NATS JetStream async backend
- [ ] `[7]`  Create `acc/backends/vector_lancedb.py` — LanceDB embedded backend (episodes, patterns, collective_mem, icl_results tables)
- [ ] `[8]`  Create `acc/backends/vector_milvus.py` — Milvus client backend (RHOAI path)
- [ ] `[9]`  Create `acc/backends/llm_ollama.py` — Ollama REST backend (OpenAI-compat)
- [ ] `[10]` Create `acc/backends/llm_anthropic.py` — Anthropic Claude SDK backend
- [ ] `[11]` Create `acc/backends/llm_vllm.py` — vLLM/KServe InferenceService backend
- [ ] `[12]` Create `acc/backends/llm_llama_stack.py` — Llama Stack inference API backend
- [ ] `[13]` Create `acc/backends/metrics_log.py` — stdout JSON metrics (standalone)
- [ ] `[14]` Create `acc/backends/metrics_otel.py` — OpenTelemetry SDK backend (RHOAI)

## Phase 3 — Integration

- [ ] `[15]` Create `acc/agent.py` — minimal agent entry point (REGISTERING state, heartbeat loop, graceful shutdown)
- [ ] `[16]` Create `deploy/Containerfile.agent-core` — UBI10 python-312 image
- [ ] `[17]` Create `deploy/podman-compose.yml` — standalone Podman deployment (NATS + 3 agent roles + Redis)

## Phase 4 — Testing

- [ ] `[18]` Create `tests/conftest.py` — shared fixtures (tmp_path, mock NATS, mock httpx)
- [ ] `[19]` Create `tests/test_config.py` — config loader unit tests
- [ ] `[20]` Create `tests/test_backends_signaling.py` — NATS backend (mocked)
- [ ] `[21]` Create `tests/test_backends_vector.py` — LanceDB (real, tmp_path) + Milvus (mocked)
- [ ] `[22]` Create `tests/test_backends_llm.py` — all 4 LLM backends (mocked httpx/SDK)
- [ ] `[23]` Create `tests/test_backends_metrics.py` — log + OTel backends
- [ ] `[24]` Run `pytest tests/` — all green

## Phase 5 — Polish

- [ ] `[25]` Build container image: `podman build -f deploy/Containerfile.agent-core -t acc-agent-core:0.1.0 .`
- [ ] `[26]` Build image and run smoke test: `podman-compose up` → agent hits REGISTERING
- [ ] `[27]` Commit final state, update `docs/CHANGELOG.md` with v0.1.0 implementation note
- [ ] `[28]` Open PR: `[feat] Phase 1a: backend abstraction layer (ACC-1)`
