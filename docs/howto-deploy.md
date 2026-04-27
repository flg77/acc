# ACC Deployment Guide — Step-by-Step

This guide covers everything needed to bring up ACC using Podman Compose, from first clone to a running multi-agent collective. It includes the beta/production stack switch, TUI activation, compliance configuration, and common troubleshooting.

---

## Quick Reference

```bash
# First time setup (production stack)
./acc-deploy.sh build          # Build all UBI images
./acc-deploy.sh                # Start stack (detached)

# Beta stack
STACK=beta ./acc-deploy.sh build
STACK=beta ./acc-deploy.sh

# With TUI operator console
TUI=true ./acc-deploy.sh

# Stop
./acc-deploy.sh down
./acc-deploy.sh down -v        # Also remove volumes (full reset)
```

---

## 1. Pre-flight Checklist

Before running for the first time, verify:

| Check | Command | Expected |
|-------|---------|----------|
| Podman installed | `podman --version` | `4.0+` |
| podman-compose installed | `podman-compose --version` | `1.0.6+` |
| Python 3.12 installed | `python3 --version` | `3.12+` |
| Ollama running (if local LLM) | `ollama list` | model list |
| `acc-config.yaml` present | `ls acc-config.yaml` | found |
| `deploy/.env` present | `ls deploy/.env` | found (optional) |

Install missing tools:
```bash
pip install podman-compose
```

---

## 2. Beta vs Production Stack

ACC ships two compose stacks. Choose based on your needs:

| | Beta | Production |
|---|---|---|
| **Path** | `container/beta/podman-compose.yml` | `container/production/podman-compose.yml` |
| **NATS image** | `nats:2.10-alpine` (DockerHub) | Custom UBI9 build (Red Hat registry) |
| **Agent base** | `ubi9/python-312-minimal` | `ubi10/python-312-minimal` |
| **Image tag** | `0.1.0` | `0.2.0` |
| **TUI service** | Not included | Included (disabled by default) |
| **OpenShift compatibility** | No (alpine NATS) | Yes (all UBI) |
| **When to use** | Quick local dev, CI | Pre-production, demo, OpenShift validation |

**Switch between stacks** using the `STACK` env var:
```bash
STACK=beta ./acc-deploy.sh        # beta
STACK=production ./acc-deploy.sh  # production (default)
```

Or call compose directly:
```bash
# Beta
podman-compose -f container/beta/podman-compose.yml up -d

# Production
podman-compose -f container/production/podman-compose.yml up -d
```

---

## 3. First Build

Images must be built before the first `up`. The build downloads UBI layers, installs Python packages, and bakes the `all-MiniLM-L6-v2` embedding model (requires internet access on first build).

```bash
# Build production images (~5–10 min on first run, cached on subsequent runs)
./acc-deploy.sh build

# Or build a specific service only:
podman-compose -f container/production/podman-compose.yml build acc-agent-ingester
```

**Expected output per service:**
```
Building acc-agent-ingester
[1/5] FROM registry.access.redhat.com/ubi10/python-312-minimal:latest
[2/5] RUN microdnf install -y ...
[3/5] COPY pyproject.toml ...
[4/5] RUN python3 -c "import tomllib ..."
[5/5] RUN for attempt in 1 2 3; do python3 -c "from sentence_transformers ..."
```

> **Offline build:** Set `TRANSFORMERS_OFFLINE=1` and pre-mount a model cache volume to skip the HuggingFace download. See `container/production/Containerfile.agent-core` for details.

---

## 4. Environment Configuration

### `.env` file

Create `deploy/.env` (copied from `deploy/.env.example`):
```bash
cp deploy/.env.example deploy/.env
```

Key variables to set:

```bash
# ── Required for production security ──────────────────────────────────────────
REDIS_PASSWORD=<openssl rand -hex 32>

# ── LLM backend ───────────────────────────────────────────────────────────────
# Option A: Ollama (local, no key)
# (no extra vars needed; set ollama_base_url in acc-config.yaml)

# Option B: OpenAI-compatible (any provider)
# ACC_LLM_BACKEND=openai_compat
# ACC_LLM_BASE_URL=https://api.groq.com/openai/v1
# ACC_LLM_MODEL=llama-3.3-70b-versatile
# ACC_LLM_API_KEY_ENV=GROQ_API_KEY
# GROQ_API_KEY=<your-key>

# Option C: Anthropic
# ACC_LLM_BACKEND=anthropic
# ACC_ANTHROPIC_MODEL=claude-sonnet-4-6
# ACC_ANTHROPIC_API_KEY=<your-key>

# ── TUI (production only) ─────────────────────────────────────────────────────
# ACC_COLLECTIVE_IDS=sol-01           # default; comma-separated for multi-collective
# ACC_TUI_WEB_PORT=0                  # set to 8765 to enable HTTP WebBridge
# ACC_ROLES_ROOT=/app/roles           # path to enterprise role definitions

# ── Compliance (optional) ─────────────────────────────────────────────────────
# ACC_COMPLIANCE_ENABLED=true
# ACC_OWASP_ENFORCE=false             # observe mode by default
# ACC_CAT_A_ENFORCE=false             # observe mode by default
# ACC_AUDIT_BACKEND=file              # file | kafka | multi
# ACC_HIPAA_MODE=false
```

### `acc-config.yaml`

The root `acc-config.yaml` is mounted read-only into every agent container. Edit it before starting:

```yaml
deploy_mode: standalone

agent:
  collective_id: sol-01

signaling:
  backend: nats
  nats_url: nats://nats:4222     # container name; use localhost:4222 for host access

llm:
  backend: ollama
  ollama_base_url: http://host.containers.internal:11434   # Ollama on host
  ollama_model: llama3.2:3b

observability:
  backend: log
```

> `host.containers.internal` resolves to the host from within a Podman container.

---

## 5. Starting the Stack

### Standard start (production, detached)

```bash
./acc-deploy.sh
```

Services started (in dependency order):
1. `nats` — NATS JetStream (port 4222, monitoring 8222)
2. `acc-redis` — Redis (port 6379)
3. `acc-agent-ingester` — Ingests raw signals
4. `acc-agent-analyst` — Analyzes and synthesizes
5. `acc-agent-arbiter` — Governance and planning

### Verify startup

```bash
# Check all containers are running
./acc-deploy.sh status

# Expected:
# NAME                    STATUS                  PORTS
# acc-nats                Up 30 seconds (healthy) 0.0.0.0:4222->4222/tcp ...
# acc-redis               Up 29 seconds (healthy) 0.0.0.0:6379->6379/tcp
# acc-agent-ingester      Up 25 seconds
# acc-agent-analyst       Up 25 seconds
# acc-agent-arbiter       Up 25 seconds

# Watch agent logs (should reach ACTIVE within 30s)
./acc-deploy.sh logs acc-agent-ingester

# Expected:
# INFO  acc.agent - REGISTERING | role=ingester collective=sol-01
# INFO  acc.agent - role store loaded | tier=config version=0.2.0
# INFO  acc.agent - ACTIVE | role=ingester collective=sol-01

# Monitor NATS subjects (requires nats CLI)
nats sub "acc.>" --server nats://localhost:4222
```

---

## 6. TUI — Operator Console (Production Only)

The TUI is a full-screen terminal dashboard providing real-time collective observability. It is disabled by default and enabled via the `tui` profile.

### Start with TUI

```bash
# Start full stack + TUI
TUI=true ./acc-deploy.sh

# Or directly:
podman-compose -f container/production/podman-compose.yml --profile tui up -d
```

### Connect to TUI

The TUI requires interactive terminal access (`stdin_open: true`, `tty: true`):

```bash
podman attach acc-tui
```

To detach without stopping: `Ctrl-P Ctrl-Q`

### TUI screens

| Screen | Key | Description |
|--------|-----|-------------|
| Soma (Dashboard) | `1` | Live agent health cards, governance events, memory stats |
| Nucleus (Role Infusion) | `2` | Role definition editor, hot-reload ROLE_UPDATE |
| Compliance | `3` | OWASP guardrail status, audit trail, oversight queue |
| Performance | `4` | LLM latency, token budgets, Cat-B setpoints |
| Comms | `5` | Signal flow monitor, all 11 NATS signal types |
| Ecosystem | `6` | Collective topology, domain registry, ICL episodes |

### WebBridge HTTP API (optional)

To expose TUI data over HTTP (for dashboards, CI polling):

1. Edit `deploy/.env`: set `ACC_TUI_WEB_PORT=8765`
2. Uncomment the port mapping in `container/production/podman-compose.yml`:
   ```yaml
   ports:
     - "8765:8765"
   ```
3. Restart the TUI container: `TUI=true ./acc-deploy.sh`
4. Access: `curl http://localhost:8765/api/snapshot`

---

## 7. Multi-Collective Setup

To monitor multiple collectives from a single TUI:

```bash
# Set comma-separated collective IDs
export ACC_COLLECTIVE_IDS=sol-01,sol-02,sol-03
TUI=true ./acc-deploy.sh
```

Each collective gets its own tab in the `CollectiveTabStrip` at the top of the TUI. Switch with `Tab` / `Shift-Tab` or click.

---

## 8. Enterprise Roles

The `roles/` directory contains 30 pre-built enterprise roles across 9 business domains. To enable a role in a container:

```yaml
# container/production/podman-compose.yml or override
environment:
  ACC_AGENT_ROLE: account_executive    # any role in roles/
  ACC_ROLES_ROOT: /app/roles
```

Or set `ACC_ROLES_ROOT` to a host-mounted directory for custom roles:
```bash
# Mount custom roles
ACC_ROLES_ROOT=/path/to/my/roles TUI=true ./acc-deploy.sh
```

See [`docs/howto-role-infusion.md`](howto-role-infusion.md) for the full role authoring guide and the `roles/TEMPLATE/` directory for a copy-and-customize starting point.

---

## 9. LLM Backend Selection

Switch LLM providers without changing the compose file — configure via env vars:

```bash
# Groq (fast, free tier available)
export ACC_LLM_BACKEND=openai_compat
export ACC_LLM_BASE_URL=https://api.groq.com/openai/v1
export ACC_LLM_MODEL=llama-3.3-70b-versatile
export ACC_LLM_API_KEY_ENV=GROQ_API_KEY
export GROQ_API_KEY=gsk_...
./acc-deploy.sh

# OpenAI
export ACC_LLM_BACKEND=openai_compat
export ACC_LLM_BASE_URL=https://api.openai.com/v1
export ACC_LLM_MODEL=gpt-4o
export ACC_LLM_API_KEY_ENV=OPENAI_API_KEY
export OPENAI_API_KEY=sk-...
./acc-deploy.sh

# Gemini
export ACC_LLM_BACKEND=openai_compat
export ACC_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
export ACC_LLM_MODEL=gemini-2.0-flash
export ACC_LLM_API_KEY_ENV=GEMINI_API_KEY
export GEMINI_API_KEY=...
./acc-deploy.sh

# OpenRouter (300+ models)
export ACC_LLM_BACKEND=openai_compat
export ACC_LLM_BASE_URL=https://openrouter.ai/api/v1
export ACC_LLM_MODEL=mistralai/mixtral-8x7b-instruct
export ACC_LLM_API_KEY_ENV=OPENROUTER_API_KEY
export OPENROUTER_API_KEY=...
./acc-deploy.sh
```

---

## 10. Compliance Configuration

ACC ships OWASP LLM Top 10 guardrails and an audit trail. All enforcement is **observe-only by default** — violations are logged but do not block processing.

To enable enforcement:

```bash
# Enable OWASP guardrails (LLM01-LLM08)
export ACC_COMPLIANCE_ENABLED=true
export ACC_OWASP_ENFORCE=true

# Enable real Cat-A governance (requires constitutional WASM)
export ACC_CAT_A_ENFORCE=true

# HIPAA mode: PHI redaction before LanceDB storage
export ACC_HIPAA_MODE=true

# Audit trail (file-based, rotating JSONL)
export ACC_AUDIT_BACKEND=file
```

Audit files appear at `/app/data/audit/audit-YYYY-MM-DD.jsonl` inside the agent containers. To access from host:

```bash
podman exec acc-agent-analyst cat /app/data/audit/audit-$(date +%Y-%m-%d).jsonl | python -m json.tool | head -50
```

---

## 11. Stopping the Stack

```bash
# Stop containers (preserve volumes)
./acc-deploy.sh down

# Stop and remove ALL data (full reset)
./acc-deploy.sh down -v
```

> **Warning:** `-v` removes the `lancedb-data`, `redis-data`, and `nats-data` volumes. All episodic memory, role state, and NATS stream data is permanently deleted.

---

## 12. Upgrading

When you pull new code:

```bash
git pull

# Rebuild images (only changed layers rebuild)
./acc-deploy.sh down
./acc-deploy.sh build
./acc-deploy.sh
```

To rebuild a single service without stopping the stack:
```bash
podman-compose -f container/production/podman-compose.yml build acc-agent-analyst
podman-compose -f container/production/podman-compose.yml up -d --no-deps acc-agent-analyst
```

---

## 13. Troubleshooting

### Stale aardvark-dns error

```
WARN: aardvark-dns runs in a different netns than expected
```

```bash
podman stop -a
killall aardvark-dns 2>/dev/null || true
rm -rf /run/user/$UID/containers/networks/aardvark-dns
./acc-deploy.sh
```

### Agent stuck in REGISTERING

Agent publishes heartbeat on `acc.{collective_id}.heartbeat`. If stuck:

1. Verify NATS is healthy: `podman inspect acc-nats --format "{{.State.Health.Status}}"`
2. Check NATS JetStream is enabled: `curl http://localhost:8222/healthz`
3. Verify agent can reach NATS: `podman exec acc-agent-ingester ncat -z nats 4222 && echo OK`
4. Check env: `podman inspect acc-agent-ingester | grep ACC_NATS_URL`

### Redis authentication failure

```
redis.exceptions.AuthenticationError: WRONGPASS
```

1. Verify `REDIS_PASSWORD` in `deploy/.env` is non-empty
2. Ensure both the Redis container and agent containers see the same value:
   ```bash
   podman exec acc-redis redis-cli -a "$REDIS_PASSWORD" ping
   ```

### LanceDB volume permission error

```
PermissionError: [Errno 13] Permission denied: '/app/data/lancedb/ingester'
```

The production entrypoint (`deploy/entrypoint-agent.sh`) creates and chowns the LanceDB subdirectory. If this fails:

```bash
# Check the entrypoint is present
podman exec acc-agent-ingester ls -la /app/entrypoint.sh

# Check volume ownership
podman exec acc-agent-ingester ls -la /app/data/lancedb/
```

### TUI exits immediately

The TUI needs an interactive terminal:
```bash
# Wrong (no TTY)
podman start acc-tui

# Correct
podman attach acc-tui   # after TUI=true ./acc-deploy.sh started it
```

### Model download fails at build time

The agent-core Containerfile retries 3 times. If all fail:

```bash
# Check HuggingFace connectivity
curl -s https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/config.json | head -5

# Or build with a pre-cached model volume (offline build)
TRANSFORMERS_OFFLINE=1 podman build \
  -v /path/to/model-cache:/app/models:ro \
  -f container/production/Containerfile.agent-core .
```

---

## 14. Running Tests

Validate your deployment with the container test suite:

```bash
# Tier 1: Static checks (no runtime required — fast, always run)
pytest tests/container/unit/ -v

# Tier 2: Build validation (requires podman)
pytest tests/container/build/ -v

# Tier 3: Runtime checks (requires built images)
pytest tests/container/runtime/ -v

# Tier 4: Full stack integration (requires podman-compose + built images)
pytest tests/container/integration/ -v

# Run all tiers
pytest tests/container/ -v
```

See `tests/container/` for each tier's requirements and what is validated.

---

## Full Environment Variable Reference

| Variable | Default | Description |
|---|---|---|
| `ACC_DEPLOY_MODE` | `standalone` | Deployment profile (`standalone` / `edge` / `rhoai`) |
| `ACC_AGENT_ROLE` | `ingester` | Agent role (set per-pod in compose) |
| `ACC_COLLECTIVE_ID` | `sol-01` | Single collective ID (agent pods) |
| `ACC_COLLECTIVE_IDS` | `sol-01` | Comma-separated IDs for TUI multi-collective |
| `ACC_NATS_URL` | `nats://localhost:4222` | NATS server URL |
| `ACC_NATS_HUB_URL` | _(empty)_ | NATS leaf hub URL (edge mode) |
| `ACC_LANCEDB_PATH` | `/app/data/lancedb` | LanceDB data directory |
| `ACC_REDIS_URL` | _(empty)_ | Redis connection URL |
| `ACC_REDIS_PASSWORD` | _(empty)_ | Redis password |
| `ACC_LLM_BACKEND` | `ollama` | LLM backend (`ollama` / `anthropic` / `vllm` / `openai_compat`) |
| `ACC_LLM_MODEL` | _(empty)_ | Universal model identifier (overrides backend-specific) |
| `ACC_LLM_BASE_URL` | _(empty)_ | Universal inference base URL |
| `ACC_LLM_API_KEY_ENV` | _(empty)_ | Env var name containing the API key |
| `ACC_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `ACC_OLLAMA_MODEL` | `llama3.2:3b` | Ollama model name |
| `ACC_ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Anthropic model name |
| `ACC_VLLM_INFERENCE_URL` | _(empty)_ | vLLM inference endpoint |
| `ACC_METRICS_BACKEND` | `log` | Metrics backend (`log` / `otel`) |
| `ACC_ARBITER_VERIFY_KEY` | _(empty)_ | Base64 Ed25519 public key for role update verification |
| `ACC_PEER_COLLECTIVES` | _(empty)_ | Comma-separated delegation targets |
| `ACC_HUB_COLLECTIVE_ID` | _(empty)_ | Hub collective (edge mode) |
| `ACC_BRIDGE_ENABLED` | `false` | Enable cross-collective delegation |
| `ACC_TUI_WEB_PORT` | `0` | TUI WebBridge HTTP port (0 = disabled) |
| `ACC_ROLES_ROOT` | `/app/roles` | Path to enterprise role definitions directory |
| `ACC_COMPLIANCE_ENABLED` | `true` | Enable compliance framework |
| `ACC_OWASP_ENFORCE` | `false` | Block on OWASP guardrail violations (false = observe only) |
| `ACC_CAT_A_ENFORCE` | `false` | Block on Cat-A violations (false = observe only) |
| `ACC_HIPAA_MODE` | `false` | Enable PHI redaction before LanceDB storage |
| `ACC_AUDIT_BACKEND` | `file` | Audit backend (`file` / `kafka` / `multi`) |
| `ACC_AUDIT_KAFKA_BOOTSTRAP` | _(empty)_ | Kafka bootstrap servers (RHOAI only) |
