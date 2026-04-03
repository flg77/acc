# Design: Phase 1a — Backend Abstraction Layer

**Change ID:** 20260403-phase1a-backend-abstraction

---

## High-Level Approach

Use Python PEP 544 `Protocol` classes as structural interfaces for each
infrastructure concern. Concrete implementations are selected at startup by the
config loader (`acc/config.py`) reading `acc-config.yaml`. No `if/else` branching
exists in business logic — only in the factory function that wires backends.

The package is structured as a proper Python package with `pyproject.toml`
(PEP 517/518), targeting Python 3.12+.

---

## Files to Create

```
agentic-cell-corpus/
├── pyproject.toml                         # Project metadata + dependencies
├── acc-config.yaml                        # Default config (standalone mode)
├── .env.example                           # Developer environment template
├── acc/
│   ├── __init__.py                        # Package marker
│   ├── config.py                          # Config loader + backend factory
│   ├── backends/
│   │   ├── __init__.py                    # Protocol interfaces
│   │   ├── signaling_nats.py              # NATS JetStream backend
│   │   ├── vector_lancedb.py              # LanceDB embedded backend
│   │   ├── vector_milvus.py               # Milvus backend (RHOAI)
│   │   ├── llm_ollama.py                  # Ollama REST backend
│   │   ├── llm_anthropic.py               # Anthropic Claude backend
│   │   ├── llm_vllm.py                    # vLLM/KServe backend (RHOAI)
│   │   ├── llm_llama_stack.py             # Llama Stack backend (RHOAI)
│   │   ├── metrics_log.py                 # stdout JSON metrics (standalone)
│   │   └── metrics_otel.py                # OpenTelemetry backend (RHOAI)
├── tests/
│   ├── __init__.py
│   ├── conftest.py                        # Shared fixtures
│   ├── test_config.py                     # Config loader tests
│   ├── test_backends_signaling.py         # NATS backend unit tests (mocked)
│   ├── test_backends_vector.py            # LanceDB backend unit tests
│   ├── test_backends_llm.py               # LLM backend unit tests (mocked)
│   └── test_backends_metrics.py           # Metrics backend unit tests
├── deploy/
│   ├── Containerfile.agent-core           # UBI10-minimal agent image
│   ├── Containerfile.nats                 # NATS with UBI10 wrapper (minimal)
│   └── podman-compose.yml                 # solarSys standalone deployment
```

---

## Interface Design

### `acc/backends/__init__.py` — Protocol Definitions

```python
from typing import Protocol, runtime_checkable, Callable, Any, AsyncIterator

@runtime_checkable
class SignalingBackend(Protocol):
    async def publish(self, subject: str, payload: bytes) -> None: ...
    async def subscribe(self, subject: str, handler: Callable[[bytes], Any]) -> None: ...
    async def connect(self) -> None: ...
    async def close(self) -> None: ...

@runtime_checkable
class VectorBackend(Protocol):
    def search(self, table: str, embedding: list[float], top_k: int) -> list[dict]: ...
    def insert(self, table: str, records: list[dict]) -> int: ...
    def create_table_if_absent(self, table: str, schema: Any) -> None: ...

@runtime_checkable
class LLMBackend(Protocol):
    async def complete(self, system: str, user: str,
                       response_schema: dict | None = None) -> dict: ...
    async def embed(self, text: str) -> list[float]: ...

@runtime_checkable
class MetricsBackend(Protocol):
    def emit_span(self, name: str, attributes: dict[str, str | float | int]) -> None: ...
    def emit_metric(self, name: str, value: float,
                    labels: dict[str, str] | None = None) -> None: ...
```

### `acc/config.py` — Config Loader + Factory

```python
def load_config(path: str = "acc-config.yaml") -> "ACCConfig": ...

def build_backends(config: "ACCConfig") -> "BackendBundle":
    """
    Factory: selects and instantiates concrete backends from config.
    Returns a BackendBundle dataclass with signaling, vector, llm, metrics fields.
    """
```

---

## Dependency Selections

| Dependency | Version | Purpose |
|---|---|---|
| `nats-py` | ^2.7 | NATS JetStream async client |
| `lancedb` | ^0.10 | Embedded vector DB |
| `pymilvus` | ^2.4 | Milvus client (RHOAI) |
| `anthropic` | ^0.40 | Claude API client |
| `httpx` | ^0.27 | Async HTTP (Ollama, vLLM, LlamaStack) |
| `opentelemetry-sdk` | ^1.25 | OTel metrics + traces |
| `opentelemetry-exporter-otlp-proto-grpc` | ^1.25 | OTel OTLP exporter |
| `pydantic` | ^2.8 | Config validation |
| `pyyaml` | ^6.0 | YAML config parsing |
| `msgpack` | ^1.1 | Wire serialization |
| `sentence-transformers` | ^3.2 | Local embeddings (all-MiniLM-L6-v2) |
| `pytest` | ^8.3 | Test runner |
| `pytest-asyncio` | ^0.24 | Async test support |

---

## Container Design

### `deploy/Containerfile.agent-core`

Base: `registry.access.redhat.com/ubi10/python-312:latest`
(Python 3.12 on UBI10 — no additional RHEL subscription required for UBI images)

Layers:
1. Copy `pyproject.toml` + install dependencies (no dev deps)
2. Download `all-MiniLM-L6-v2` model to `/app/models/` at build time
3. Copy `acc/` package
4. Copy `acc-config.yaml` as default config (overridable by volume/env)
5. Run as non-root UID 1001 (OCP compatible)
6. `CMD ["python", "-m", "acc.agent"]`

### `deploy/podman-compose.yml` (solarSys target)

Services:
- `nats` — `nats:2.10-alpine` (lightweight, not UBI — NATS Inc. doesn't publish UBI)
- `acc-agent-ingester` — `localhost/acc-agent-core:0.1.0`, role=ingester
- `acc-agent-analyst` — same image, role=analyst
- `acc-agent-arbiter` — same image, role=arbiter
- `acc-redis` — `registry.access.redhat.com/ubi8/redis-6:latest`

All agent containers mount the same `acc-config.yaml` volume.

---

## Error Handling

- Backend connection failures raise `acc.backends.BackendConnectionError` (custom)
- LLM call failures raise `acc.backends.LLMCallError` with retryable flag
- Config validation errors raise `pydantic.ValidationError` on startup (fail-fast)
- Missing required env vars: fail at startup with clear message listing missing vars

---

## Testing Strategy

**Unit tests (no live infrastructure):**
- NATS backend: mock `nats.connect()` — test subject routing, payload encoding
- LanceDB backend: use temp directory (`tmp_path` fixture) — real LanceDB, no mock
- Milvus backend: mock `pymilvus.MilvusClient` — test schema mapping
- Ollama backend: mock `httpx.AsyncClient` — test request format
- Anthropic backend: mock `anthropic.AsyncAnthropic` — test prompt construction
- Metrics backends: assert stdout output / OTel SDK span emission

**Integration smoke test (runs on solarSys):**
- `podman-compose up` → agent reaches REGISTERING state → exits cleanly
- Verified manually after SSH access is available

---

## Alternatives Considered

- **ABC (abstract base classes) instead of Protocol:** Rejected — forces inheritance,
  prevents using third-party objects that happen to satisfy the interface.
- **Single backend file:** Rejected — would make it impossible to install a subset
  of dependencies (e.g., edge deployment without Milvus).
- **Docker instead of Podman:** Rejected — solarSys uses Podman; docker-compose
  syntax is compatible via `podman-compose`.
