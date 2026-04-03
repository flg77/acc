# Spec: ACC Backend Abstraction Layer

**Capability:** backends
**Change ID:** 20260403-phase1a-backend-abstraction
**Version:** 0.1.0

---

## Requirements — ADDED

### Signaling Backend

**REQ-SIG-001** The `SignalingBackend` Protocol SHALL define `publish(subject, payload)`, `subscribe(subject, handler)`, `connect()`, and `close()` as async methods.

**REQ-SIG-002** `NATSBackend` SHALL connect to a NATS JetStream server at the URL specified by `signaling.nats_url` in `acc-config.yaml`.

**REQ-SIG-003** `NATSBackend.publish()` SHALL serialize the payload as MessagePack bytes before publishing.

**REQ-SIG-004** `NATSBackend.subscribe()` SHALL deserialize inbound MessagePack bytes before invoking the handler.

**REQ-SIG-005** A connection failure in `NATSBackend.connect()` SHALL raise `BackendConnectionError` with the original exception chained.

### Vector Backend

**REQ-VEC-001** The `VectorBackend` Protocol SHALL define `search(table, embedding, top_k)`, `insert(table, records)`, and `create_table_if_absent(table, schema)`.

**REQ-VEC-002** `LanceDBBackend` SHALL store data at the path specified by `vector_db.lancedb_path` in `acc-config.yaml`.

**REQ-VEC-003** `LanceDBBackend.search()` SHALL return results as a list of dicts, ordered by cosine similarity descending.

**REQ-VEC-004** `LanceDBBackend` SHALL auto-create the four standard tables (`episodes`, `patterns`, `collective_mem`, `icl_results`) with schemas defined in v0.1.0 Section 7.2 if they do not exist.

**REQ-VEC-005** `MilvusBackend` SHALL connect to the URI specified by `vector_db.milvus_uri` and prefix all collection names with `vector_db.milvus_collection_prefix`.

**REQ-VEC-006** `MilvusBackend.search()` SHALL use cosine distance metric on the `embedding` field.

### LLM Backend

**REQ-LLM-001** The `LLMBackend` Protocol SHALL define `complete(system, user, response_schema)` and `embed(text)` as async methods.

**REQ-LLM-002** `OllamaBackend.complete()` SHALL POST to `{ollama_base_url}/api/chat` with the model specified in config, requesting JSON output when `response_schema` is provided.

**REQ-LLM-003** `AnthropicBackend.complete()` SHALL use the `anthropic` SDK, pass the system prompt separately from the user message, and request structured JSON output via `response_format`.

**REQ-LLM-004** `VLLMBackend.complete()` SHALL use the OpenAI-compatible `/v1/chat/completions` endpoint at `llm.vllm_inference_url`.

**REQ-LLM-005** `LlamaStackBackend.complete()` SHALL POST to `{llama_stack_url}/inference/chat-completion`.

**REQ-LLM-006** All LLM backends SHALL implement `embed(text)` — `OllamaBackend` and `VLLMBackend` use the model's embedding endpoint; `AnthropicBackend` and `LlamaStackBackend` SHALL use a local `sentence-transformers` fallback (`all-MiniLM-L6-v2`).

**REQ-LLM-007** A non-2xx HTTP response from any LLM backend SHALL raise `LLMCallError` with `retryable=True` for 429/503 and `retryable=False` for 400/401/422.

### Metrics Backend

**REQ-MET-001** The `MetricsBackend` Protocol SHALL define `emit_span(name, attributes)` and `emit_metric(name, value, labels)`.

**REQ-MET-002** `LogMetricsBackend` SHALL write each emission as a JSON line to stdout with keys `ts`, `type` (`span`|`metric`), `name`, `value`/`attributes`, `labels`.

**REQ-MET-003** `OTelMetricsBackend` SHALL use the OpenTelemetry Python SDK and export via OTLP gRPC to `OTEL_EXPORTER_OTLP_ENDPOINT`.

**REQ-MET-004** `OTelMetricsBackend` SHALL set the OTel service name from `observability.otel_service_name` in config.

### Config Loader

**REQ-CFG-001** `load_config()` SHALL accept an optional path argument and default to `acc-config.yaml` in the current working directory.

**REQ-CFG-002** `load_config()` SHALL overlay environment variable values over YAML values using the mappings defined in v0.2.0 Section 4.3.

**REQ-CFG-003** `load_config()` SHALL validate the config with Pydantic and raise `ValidationError` on missing required fields for the selected `deploy_mode`.

**REQ-CFG-004** `build_backends()` SHALL instantiate exactly the concrete backend classes indicated by `config.deploy_mode` as per v0.2.0 Section 4.2.

**REQ-CFG-005** `build_backends()` SHALL return a `BackendBundle` dataclass with fields `signaling`, `vector`, `llm`, `metrics`.

### Container Image

**REQ-CTR-001** `deploy/Containerfile.agent-core` SHALL use `registry.access.redhat.com/ubi10/python-312` as the base image.

**REQ-CTR-002** The container SHALL run as non-root user with UID 1001 to be compatible with OpenShift's restricted SCC.

**REQ-CTR-003** The container image SHALL include the `all-MiniLM-L6-v2` model cached at `/app/models/` so no internet access is required at runtime.

**REQ-CTR-004** `deploy/podman-compose.yml` SHALL define services for `nats`, `acc-redis`, `acc-agent-ingester`, `acc-agent-analyst`, and `acc-agent-arbiter`.

**REQ-CTR-005** Redis SHALL use `registry.access.redhat.com/ubi8/redis-6` as its base image.

### Testing

**REQ-TST-001** All backend unit tests SHALL pass without requiring live infrastructure (NATS, Milvus, Ollama, Anthropic API, or OTel collector).

**REQ-TST-002** `LanceDBBackend` tests SHALL use a real LanceDB instance in a `tmp_path` temporary directory.

**REQ-TST-003** Test coverage for `acc/backends/` SHALL be at minimum 80% line coverage.
