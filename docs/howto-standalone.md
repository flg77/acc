# How to Run ACC in Standalone Mode (Podman)

Standalone mode runs the full ACC stack on a single machine using Podman Compose. It is designed for local development, CI, and demos. No Kubernetes, no cloud account, no GPU required.

---

## Prerequisites

| Component | Minimum version | Install |
|-----------|----------------|---------|
| Python | 3.12 | [python.org](https://www.python.org/downloads/) |
| Podman | 4.0 | [podman.io](https://podman.io/getting-started/installation) |
| podman-compose | 1.0.6 | `pip install podman-compose` |
| NATS CLI (optional) | any | `go install github.com/nats-io/natscli/nats@latest` |
| Ollama (if using local LLM) | any | [ollama.com](https://ollama.com/download) |

---

## Step 1 — Clone and Install

```bash
git clone https://github.com/redhat-ai-dev/agentic-cell-corpus.git
cd agentic-cell-corpus

# Install the Python package in editable mode
pip install -e ".[dev]"
```

---

## Step 2 — Configure `acc-config.yaml`

The root `acc-config.yaml` is the configuration file for the standalone stack. It is already set to sensible standalone defaults. Review and adjust the sections below before starting.

### Minimal configuration (Ollama)

```yaml
deploy_mode: standalone

agent:
  role: ingester            # overridden per-pod in podman-compose.yml
  collective_id: sol-01
  heartbeat_interval_s: 30

signaling:
  backend: nats
  nats_url: nats://localhost:4222

vector_db:
  backend: lancedb
  lancedb_path: /app/data/lancedb

llm:
  backend: ollama
  ollama_base_url: http://localhost:11434
  ollama_model: llama3.2:3b
  embedding_model: all-MiniLM-L6-v2

observability:
  backend: log
```

### LLM Backend Options

**Ollama (default — no API key needed):**
```yaml
llm:
  backend: ollama
  ollama_base_url: http://localhost:11434
  ollama_model: llama3.2:3b   # or llama3.1:8b for better quality
```

Make sure Ollama is running and the model is pulled:
```bash
ollama pull llama3.2:3b
```

**Anthropic:**
```yaml
llm:
  backend: anthropic
  anthropic_model: claude-sonnet-4-6
```
Set `ACC_ANTHROPIC_API_KEY` in your environment or `.env` file.

**vLLM (local or remote):**
```yaml
llm:
  backend: vllm
  vllm_inference_url: http://localhost:8000
```

**OpenAI-compatible (any provider — Groq, OpenAI, Gemini, OpenRouter, HuggingFace, Together, etc.):**
```yaml
llm:
  backend: openai_compat
  base_url: https://api.groq.com/openai/v1
  model: llama-3.3-70b-versatile
  api_key_env: GROQ_API_KEY       # name of the env var holding the key
```
Set via environment variables:
```bash
export ACC_LLM_BACKEND=openai_compat
export ACC_LLM_BASE_URL=https://api.groq.com/openai/v1
export ACC_LLM_MODEL=llama-3.3-70b-versatile
export ACC_LLM_API_KEY_ENV=GROQ_API_KEY
export GROQ_API_KEY=gsk_...
```
Supported base URLs: OpenAI (`https://api.openai.com/v1`), Groq, Gemini (`https://generativelanguage.googleapis.com/v1beta/openai`), OpenRouter (`https://openrouter.ai/api/v1`), HuggingFace TGI (`https://api-inference.huggingface.co/v1`), local vLLM (`http://localhost:8000/v1`), LM Studio (`http://localhost:1234/v1`).

---

## Step 3 — Security Configuration (Phase 0a/0b)

### Redis Password (Phase 0b)

Generate a Redis password and configure it before starting. Agents use Redis for working memory — role centroid, stress indicators, ICL episode state.

```bash
# Generate a secure password
export REDIS_PASS=$(openssl rand -hex 32)
echo "ACC_REDIS_PASSWORD=$REDIS_PASS" >> .env
```

Add to `acc-config.yaml`:
```yaml
working_memory:
  url: redis://localhost:6379
  password: ""    # set via ACC_REDIS_PASSWORD env var
```

### Ed25519 Arbiter Signing Key (Phase 0a)

The arbiter signs ROLE_UPDATE messages with an Ed25519 private key. Agents verify these signatures using the corresponding public key. Without a verify key configured, only signature presence is checked (backward compatible, not production-safe).

Generate a key pair:
```bash
# Generate Ed25519 key pair (requires Python cryptography library)
python - <<'EOF'
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
import base64

private_key = Ed25519PrivateKey.generate()
public_key = private_key.public_key()

priv_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
pub_bytes = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)

print(f"Private key (arbiter only): {base64.b64encode(priv_bytes).decode()}")
print(f"Public key (set as ACC_ARBITER_VERIFY_KEY): {base64.b64encode(pub_bytes).decode()}")
EOF
```

Add the public key to your `.env`:
```bash
echo "ACC_ARBITER_VERIFY_KEY=<base64-public-key>" >> .env
```

Store the private key securely — the arbiter pod needs it to sign ROLE_UPDATE messages.

---

## Step 4 — Environment Variables

All config fields can be overridden via environment variables. The full reference:

| Environment Variable | Config field | Default | Description |
|---|---|---|---|
| `ACC_DEPLOY_MODE` | `deploy_mode` | `standalone` | Deployment profile |
| `ACC_AGENT_ROLE` | `agent.role` | `ingester` | Role for this agent pod |
| `ACC_COLLECTIVE_ID` | `agent.collective_id` | `sol-01` | Collective identifier (agent pods) |
| `ACC_COLLECTIVE_IDS` | _(TUI only)_ | `sol-01` | Comma-separated IDs for TUI multi-collective tab strip |
| `ACC_NATS_URL` | `signaling.nats_url` | `nats://localhost:4222` | NATS server URL |
| `ACC_NATS_HUB_URL` | `signaling.hub_url` | _(empty)_ | NATS leaf hub URL (edge only) |
| `ACC_LANCEDB_PATH` | `vector_db.lancedb_path` | `/app/data/lancedb` | LanceDB data directory |
| `ACC_MILVUS_URI` | `vector_db.milvus_uri` | _(empty)_ | Milvus URI (rhoai mode) |
| `ACC_MILVUS_COLLECTION_PREFIX` | `vector_db.milvus_collection_prefix` | `acc_` | Milvus collection prefix |
| `ACC_LLM_BACKEND` | `llm.backend` | `ollama` | LLM backend (`ollama` / `anthropic` / `vllm` / `openai_compat`) |
| `ACC_LLM_MODEL` | `llm.model` | _(empty)_ | Universal model ID — overrides backend-specific model fields |
| `ACC_LLM_BASE_URL` | `llm.base_url` | _(empty)_ | Universal inference base URL (openai_compat) |
| `ACC_LLM_API_KEY_ENV` | `llm.api_key_env` | _(empty)_ | Name of env var holding the API key |
| `ACC_OLLAMA_BASE_URL` | `llm.ollama_base_url` | `http://localhost:11434` | Ollama server URL |
| `ACC_OLLAMA_MODEL` | `llm.ollama_model` | `llama3.2:3b` | Ollama model name |
| `ACC_ANTHROPIC_MODEL` | `llm.anthropic_model` | `claude-sonnet-4-6` | Anthropic model name |
| `ACC_VLLM_INFERENCE_URL` | `llm.vllm_inference_url` | _(empty)_ | vLLM inference endpoint |
| `ACC_LLAMA_STACK_URL` | `llm.llama_stack_url` | _(empty)_ | Llama Stack base URL |
| `ACC_METRICS_BACKEND` | `observability.backend` | `log` | Metrics backend |
| `ACC_OTEL_SERVICE_NAME` | `observability.otel_service_name` | `acc-agent` | OTel service name |
| `ACC_ROLE_PURPOSE` | `role_definition.purpose` | _(empty)_ | Role purpose override |
| `ACC_ROLE_PERSONA` | `role_definition.persona` | `concise` | Persona style override |
| `ACC_ROLE_VERSION` | `role_definition.version` | `0.1.0` | Role version override |
| `ACC_ARBITER_VERIFY_KEY` | `security.arbiter_verify_key` | _(empty)_ | Base64 Ed25519 public key |
| `ACC_REDIS_URL` | `working_memory.url` | _(empty)_ | Redis connection URL |
| `ACC_REDIS_PASSWORD` | `working_memory.password` | _(empty)_ | Redis password |
| `ACC_PEER_COLLECTIVES` | `agent.peer_collectives` | _(empty list)_ | Comma-separated delegation targets |
| `ACC_HUB_COLLECTIVE_ID` | `agent.hub_collective_id` | _(empty)_ | Hub collective (edge mode) |
| `ACC_BRIDGE_ENABLED` | `agent.bridge_enabled` | `false` | Enable cross-collective delegation |
| `ACC_TUI_WEB_PORT` | _(TUI only)_ | `0` | HTTP port for TUI WebBridge (0 = disabled) |
| `ACC_ROLES_ROOT` | _(TUI/agent)_ | `/app/roles` | Path to enterprise role definitions directory |
| `ACC_COMPLIANCE_ENABLED` | `compliance.enabled` | `true` | Enable compliance framework |
| `ACC_OWASP_ENFORCE` | `compliance.owasp_enforce` | `false` | Block on OWASP violations (false = observe only) |
| `ACC_CAT_A_ENFORCE` | `compliance.cat_a_enforce` | `false` | Block on Cat-A violations (false = observe only) |
| `ACC_HIPAA_MODE` | `compliance.hipaa_mode` | `false` | PHI redaction before LanceDB storage |
| `ACC_AUDIT_BACKEND` | `compliance.audit_backend` | `file` | Audit backend (`file` / `kafka` / `multi`) |
| `ACC_AUDIT_KAFKA_BOOTSTRAP` | `compliance.audit_kafka_bootstrap` | _(empty)_ | Kafka bootstrap servers (RHOAI) |

---

## Step 5 — Start the Stack

ACC ships two compose stacks. Use the `acc-deploy.sh` helper to select between them:

```bash
# Build images first (one-time, ~5–10 min)
./acc-deploy.sh build

# Start production stack (UBI10-based, recommended)
./acc-deploy.sh

# Start beta stack (alpine NATS, 0.1.x images)
STACK=beta ./acc-deploy.sh

# Start production stack + TUI operator console
TUI=true ./acc-deploy.sh
```

Or use podman-compose directly:
```bash
# Production (0.2.x, UBI10, recommended)
podman-compose -f container/production/podman-compose.yml up -d

# Beta (0.1.x, nats:alpine)
podman-compose -f container/beta/podman-compose.yml up -d
```

The compose file starts: NATS JetStream, Redis, `acc-agent-ingester`, `acc-agent-analyst`, `acc-agent-arbiter`.

For a comprehensive step-by-step guide including TUI, multi-collective, LLM provider selection, compliance configuration, and troubleshooting, see [`docs/howto-deploy.md`](howto-deploy.md).

### Verify agent startup

```bash
# Watch ingester logs — should reach ACTIVE within 30s
podman logs -f acc-agent-ingester

# Expected output:
# INFO  acc.agent - REGISTERING | role=ingester collective=sol-01
# INFO  acc.agent - role store loaded | tier=config version=0.1.0
# INFO  acc.agent - ACTIVE | role=ingester collective=sol-01 agent_id=ingester-a3f2
```

```bash
# (Optional) Watch NATS subjects using NATS CLI
nats sub "acc.>" --server nats://localhost:4222

# You should see heartbeats every 30s:
# [#1] Received on "acc.sol-01.heartbeat"
#   {"agent_id": "ingester-a3f2", "role": "ingester", "phase": "ACTIVE", ...}
```

---

## Step 6 — Run the Tests

```bash
# All unit tests (127 tests, no external services required — all backends mocked)
pytest tests/ -v

# Coverage report
pytest tests/ --cov=acc --cov-report=term-missing
```

---

## Troubleshooting

**Redis auth failure:**
```
redis.exceptions.AuthenticationError: WRONGPASS invalid username-password pair
```
Check that `ACC_REDIS_PASSWORD` matches the password configured in the Redis container. The password is injected via `ACC_REDIS_PASSWORD` in both the Redis container env and the agent container env.

**NATS connection refused:**
```
nats: no servers available for connection
```
Verify NATS is running: `podman ps | grep nats`. Check port binding: `podman port acc-nats`.

**Ollama model not found:**
```
Error: model 'llama3.2:3b' not found
```
Run `ollama pull llama3.2:3b` before starting the compose stack. Make sure the `ollama_base_url` in `acc-config.yaml` points to your Ollama instance.

**Agent stuck in REGISTERING:**
The agent publishes a heartbeat to `acc.{collective_id}.heartbeat` on NATS. If it stays in `REGISTERING`, NATS is unreachable. Check `ACC_NATS_URL` and that NATS JetStream is enabled (`-js` flag in NATS config).

---

## Cross-Collective Bridge (ACC-9) in Standalone Mode

Standalone mode supports cross-collective delegation when multiple collectives are configured. To enable:

```yaml
# acc-config.yaml
agent:
  collective_id: sol-01
  peer_collectives:
    - sol-02
  bridge_enabled: true     # required to honour [DELEGATE:...] LLM markers
```

Or via environment variables:
```bash
export ACC_PEER_COLLECTIVES="sol-02,sol-03"
export ACC_BRIDGE_ENABLED=true
```

When bridge is enabled, the LLM can emit `[DELEGATE:sol-02:task exceeds local context]` in its response, and the agent will publish the task to `acc.bridge.sol-01.sol-02.delegate` for handling by the `sol-02` collective. Results arrive on `acc.bridge.sol-02.sol-01.result` within 30 seconds or the task is handled locally.

See [`docs/howto-role-infusion.md`](howto-role-infusion.md) for role definition configuration.
