# How to Infuse an Agent Role

Role infusion is the process of giving an ACC agent its behavioural identity: purpose, reasoning style, task scope, allowed actions, and governance overrides. Unlike a static system prompt, an ACC role definition is versioned, cryptographically signed, persistently stored, and hot-reloadable at runtime without a pod restart.

---

## What is a Role?

In ACC's biological model, a **role** is analogous to cell differentiation. All agents run the same `CognitiveCore` code, but each is differentiated by its role definition — just as cardiac cells and neurons share the same DNA but express different proteins based on regulatory signals.

A role definition shapes every LLM call an agent makes:
- The system prompt is constructed from `purpose`, `persona`, and `seed_context`.
- The `task_types` list filters which tasks the agent accepts.
- The `allowed_actions` list limits what signals the agent may publish.
- The `category_b_overrides` map customises the agent's OPA setpoints (token budget, rate limits).

The `observer` role is the only exception — it does not instantiate `CognitiveCore` and has no LLM calls. It publishes telemetry only.

---

## Role Definition Schema

```yaml
role_definition:
  purpose: "Ingest and normalise incoming data signals, extract structured records,
             and forward them to the collective bus."
  persona: concise          # concise | formal | exploratory | analytical
  task_types:               # signals this agent accepts; empty = accept all
    - TASK_ASSIGN
    - SYNC_MEMORY
  seed_context: ""          # domain-specific context injected into every LLM call
  allowed_actions:          # signals this agent may publish; enforced by Cat-A WASM
    - publish_signal
    - write_episode
  category_b_overrides:     # override OPA bundle setpoints for this role
    token_budget: 2048      # max LLM tokens per call
    rate_limit_rpm: 60      # max calls per minute
  version: "0.1.0"          # semantic version; tracked in heartbeat
```

### Field Reference

| Field | Type | Default | Description |
|---|---|---|---|
| `purpose` | string | `""` | One or two sentences describing the agent's function. Injected verbatim into the LLM system prompt. |
| `persona` | enum | `concise` | Reasoning style. `concise` = short direct answers; `analytical` = detailed reasoning chains; `formal` = structured; `exploratory` = broad hypotheses. |
| `task_types` | list[str] | `[]` | Signal types this agent accepts from the NATS bus. Empty list accepts all tasks. |
| `seed_context` | string | `""` | Domain-specific priming text. Injected after `purpose` in the system prompt. Use to provide background knowledge without changing the role. |
| `allowed_actions` | list[str] | `[]` | Signals this agent is permitted to publish. Enforced by the Cat-A WASM in-process. Empty = no restriction beyond Cat-A constitutional rules. |
| `category_b_overrides` | dict[str, float] | `{}` | Per-role OPA setpoint overrides. Keys must match setpoint names in the OPA bundle. |
| `version` | string | `"0.1.0"` | Semantic version string. Reported in heartbeats; used for audit. |

### Persona Styles

| Persona | Prompt effect |
|---|---|
| `concise` | "Respond concisely. Prioritise factual accuracy over elaboration." |
| `formal` | "Respond in formal, structured language. Use section headings where appropriate." |
| `exploratory` | "Explore multiple hypotheses before reaching a conclusion. Surface uncertainty explicitly." |
| `analytical` | "Apply systematic reasoning. Break down the problem into components before synthesising." |

---

## The Four-Tier Load Order

Role definitions are loaded at agent startup using a priority-ordered search:

```
Tier 1 (highest priority): File
  /etc/acc/roles/acc-role-{collective_id}.yaml
  Path controlled by ACC_ROLE_CONFIG_PATH env var.
  Mounted as a ConfigMap in Kubernetes deployments.
  Operator renders this from AgentCollective.spec.roleDefinition.

Tier 2: Redis
  Key: acc:role:{collective_id}
  Written by ROLE_UPDATE hot-reload (see below).
  Survives pod restarts when Redis is available.

Tier 3: LanceDB
  Collection: acc_roles
  Filter: collective_id = {collective_id}
  Fallback when Redis is unavailable.

Tier 4 (lowest priority): Config default
  role_definition block in acc-config.yaml.
  Always present; provides safe defaults.
```

The agent logs which tier was used at startup:
```
INFO  acc.role_store - role loaded | tier=file version=1.2.0 collective=sol-01
INFO  acc.role_store - role loaded | tier=redis version=0.8.0 collective=sol-02
INFO  acc.role_store - role loaded | tier=config version=0.1.0 collective=sol-03
```

---

## Method 1 — Configuration File (`acc-config.yaml`)

The simplest way to set a role definition. Effective for standalone mode and development.

```yaml
# acc-config.yaml
deploy_mode: standalone

role_definition:
  purpose: "Analyse incoming text signals for semantic patterns. Extract entities,
             relationships, and anomalies. Flag high-confidence findings for synthesis."
  persona: analytical
  task_types:
    - ANALYZE_SIGNAL
    - PATTERN_MATCH
  seed_context: |
    Domain: financial news monitoring.
    Key entities: company names, tickers, regulatory bodies.
    Flag any mention of earnings surprises, merger activity, or regulatory actions.
  allowed_actions:
    - publish_signal
    - write_episode
    - read_vector_db
  category_b_overrides:
    token_budget: 3000
    rate_limit_rpm: 30
  version: "1.0.0"
```

Override individual fields at runtime via environment variables:
```bash
export ACC_ROLE_PURPOSE="Analyse cybersecurity signals for threat indicators."
export ACC_ROLE_PERSONA=analytical
export ACC_ROLE_VERSION="1.1.0"
```

---

## Method 2 — Operator ConfigMap (Kubernetes / OpenShift)

In Kubernetes mode, the operator renders the `roleDefinition` block from the `AgentCollective` CR into a ConfigMap named `acc-role-{collective_id}` and mounts it into every agent pod at `/etc/acc/roles/acc-role-{collective_id}.yaml`.

### AgentCollective spec

```yaml
apiVersion: acc.redhat-ai-dev.io/v1alpha1
kind: AgentCollective
metadata:
  name: my-collective
  namespace: acc-system
spec:
  collectiveId: "sol-01"
  corpusRef:
    name: my-corpus

  roleDefinition:
    purpose: "Ingest regulatory filing documents and extract structured compliance events."
    persona: formal
    taskTypes:
      - TASK_ASSIGN
      - SYNC_MEMORY
    seedContext: |
      Source: SEC EDGAR filings (10-K, 10-Q, 8-K).
      Extract: filing date, company CIK, form type, key financial metrics.
      Flag: material changes from prior period, going-concern disclosures.
    allowedActions:
      - publish_signal
      - write_episode
    categoryBOverrides:
      token_budget: 2048
      rate_limit_rpm: 60
    version: "2.1.0"

  llm:
    backend: anthropic
    anthropic:
      model: claude-sonnet-4-6
    embeddingModel: all-MiniLM-L6-v2
```

When the operator reconciles this CR, it:
1. Renders the `roleDefinition` fields into `acc-role-sol-01.yaml`.
2. Creates/updates the `acc-role-sol-01` ConfigMap in the namespace.
3. Mounts the ConfigMap into each agent pod at `/etc/acc/roles/acc-role-sol-01.yaml`.
4. Rolls affected pods (via Deployment annotation update) to pick up the new role.

---

## Method 3 — NATS ROLE_UPDATE Hot-Reload

Hot-reload lets the arbiter push a new role definition to all agents in a collective **without restarting any pods**. The update is signed with the arbiter's Ed25519 private key and rejected if the signature is invalid.

### Payload schema

```json
{
  "collective_id": "sol-01",
  "role_definition": {
    "purpose": "Updated purpose text.",
    "persona": "analytical",
    "task_types": ["TASK_ASSIGN"],
    "seed_context": "New domain context.",
    "allowed_actions": ["publish_signal", "write_episode"],
    "category_b_overrides": {"token_budget": 4096},
    "version": "1.3.0"
  },
  "signature": "<hex-encoded Ed25519 signature of JSON payload bytes>",
  "timestamp": "2026-04-21T12:00:00Z"
}
```

### Publishing a ROLE_UPDATE (arbiter or authorised operator)

```python
import asyncio
import json
import nats
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
import base64

async def publish_role_update(nats_url: str, private_key_b64: str):
    private_key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))

    payload = {
        "collective_id": "sol-01",
        "role_definition": {
            "purpose": "Analyse financial news signals for market-moving events.",
            "persona": "analytical",
            "task_types": ["ANALYZE_SIGNAL"],
            "seed_context": "Focus on earnings surprises and M&A activity.",
            "allowed_actions": ["publish_signal", "write_episode"],
            "category_b_overrides": {"token_budget": 3500},
            "version": "1.4.0",
        },
        "timestamp": "2026-04-21T14:00:00Z",
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    signature = private_key.sign(payload_bytes)
    payload["signature"] = signature.hex()

    nc = await nats.connect(nats_url)
    await nc.publish("acc.sol-01.role_update", json.dumps(payload).encode())
    await nc.drain()

asyncio.run(publish_role_update("nats://localhost:4222", "<base64-private-key>"))
```

### What agents do when they receive ROLE_UPDATE

1. Deserialise the JSON payload.
2. Verify the Ed25519 signature against the arbiter's public key (`ACC_ARBITER_VERIFY_KEY`).
3. If signature is invalid → log `ROLE_UPDATE REJECTED` and discard.
4. If valid → apply the new role definition to `CognitiveCore`.
5. Persist the new role to Redis (`acc:role:{collective_id}`) for restart durability.
6. Log `ROLE_UPDATE APPLIED | version=1.4.0 collective=sol-01`.

---

## Ed25519 Key Management

### Key generation

```bash
python - <<'EOF'
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat, PrivateFormat, NoEncryption
)
import base64

private_key = Ed25519PrivateKey.generate()
public_key = private_key.public_key()

priv_b64 = base64.b64encode(
    private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
).decode()
pub_b64 = base64.b64encode(
    public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
).decode()

print(f"PRIVATE KEY (arbiter only): {priv_b64}")
print(f"PUBLIC KEY (set as ACC_ARBITER_VERIFY_KEY): {pub_b64}")
EOF
```

### Distribution

- **Private key**: store in a Kubernetes Secret accessible only to the arbiter pod.
  ```bash
  kubectl create secret generic acc-arbiter-signing-key \
    --from-literal=ACC_ARBITER_SIGNING_KEY=<base64-private-key> \
    -n acc-system
  ```
- **Public key**: set as `ACC_ARBITER_VERIFY_KEY` in all agent pods (and in `acc-config.yaml`).
  ```yaml
  security:
    arbiter_verify_key: "<base64-public-key>"
  ```

### What happens without a verify key

When `ACC_ARBITER_VERIFY_KEY` is empty:
- Only signature *presence* is checked (`signature` field must be non-empty).
- No cryptographic verification is performed.
- This is backward-compatible with test fixtures but **not safe for production**.

---

## Role Infusion for Each Role

The following role definitions are starting points. Customise `purpose`, `seed_context`, and `category_b_overrides` for your domain.

### ingester

```yaml
role_definition:
  purpose: "Receive external signals from sensors, APIs, or message queues. Normalise
             data into ACC's structured record format. Route to the collective bus."
  persona: concise
  task_types: [TASK_ASSIGN, SYNC_MEMORY]
  allowed_actions: [publish_signal, write_episode]
  category_b_overrides:
    token_budget: 1024
    rate_limit_rpm: 120
  version: "1.0.0"
```

### analyst

```yaml
role_definition:
  purpose: "Pattern recognition and semantic search against episodic memory. Surface
             anomalies, trends, and high-confidence entity matches. Produce structured
             findings for the synthesizer."
  persona: analytical
  task_types: [ANALYZE_SIGNAL, PATTERN_MATCH]
  seed_context: "Query the vector DB for similar past episodes before reasoning."
  allowed_actions: [publish_signal, write_episode, read_vector_db]
  category_b_overrides:
    token_budget: 3000
    rate_limit_rpm: 30
  version: "1.0.0"
```

### synthesizer

```yaml
role_definition:
  purpose: "Aggregate analyst findings into a coherent consolidated assessment.
             Resolve contradictions between analysts. Prepare structured outputs
             for the arbiter's governance review."
  persona: formal
  task_types: [SYNTHESIZE_FINDINGS]
  allowed_actions: [publish_signal, write_episode]
  category_b_overrides:
    token_budget: 4096
    rate_limit_rpm: 20
  version: "1.0.0"
```

### arbiter

```yaml
role_definition:
  purpose: "Governance authority for the collective. Sign Category-C rules derived
             from ICL episode patterns. Coordinate rogue detection. Approve reprogramming
             ladder escalations. Countersign ROLE_UPDATE payloads."
  persona: formal
  task_types: [GOVERNANCE_REVIEW, SIGN_ROLE_UPDATE, ROGUE_DETECTION]
  allowed_actions: [publish_signal, write_episode, sign_role_update, publish_role_approval]
  category_b_overrides:
    token_budget: 2048
    rate_limit_rpm: 10
  version: "1.0.0"
```

### observer (no CognitiveCore)

The observer role does not use a role definition — it has no LLM calls. It subscribes to all subjects in read-only mode and emits OTel spans and Prometheus metrics. No `role_definition` block is needed for observer pods.

---

## Edge Considerations

At the edge, the agent holds the last successfully loaded role if it becomes disconnected from the hub:

- If the agent loaded its role from **Tier 1 (File)**, the ConfigMap survives pod restarts — no hub needed.
- If the agent loaded its role from **Tier 2 (Redis)**, Redis is local at edge — the role is also available offline.
- **ROLE_UPDATE hot-reload is only applied when the NATS leaf node is connected to the hub.** Pending ROLE_UPDATE messages queue in the hub's JetStream and are delivered on reconnect.
- Role version drift is tracked in the heartbeat's `role_version` field — the hub can detect edge agents running an old role version and alert operators.

---

## Testing Role Infusion

```bash
# Run role store tests (Ed25519 valid/invalid, tier load order)
pytest tests/test_role_store.py -v

# Test output:
# tests/test_role_store.py::TestRoleStoreEdSig::test_valid_signature - PASSED
# tests/test_role_store.py::TestRoleStoreEdSig::test_tampered_payload - PASSED
# tests/test_role_store.py::TestRoleStoreEdSig::test_empty_signature_rejected - PASSED
# tests/test_role_store.py::TestRoleStoreEdSig::test_wrong_key_rejected - PASSED
```

To manually test hot-reload in a running standalone stack:

```bash
# Port-forward NATS
kubectl port-forward -n acc-system svc/my-corpus-nats 4222:4222 &
# or use local NATS from podman-compose

# Publish a ROLE_UPDATE (use the Python snippet from above)
python scripts/publish_role_update.py \
  --nats-url nats://localhost:4222 \
  --collective sol-01 \
  --purpose "Updated purpose" \
  --version "1.5.0" \
  --private-key "$ACC_ARBITER_SIGNING_KEY"

# Watch agent logs for confirmation
podman logs -f acc-agent-analyst | grep "ROLE_UPDATE"
# INFO  acc.role_store - ROLE_UPDATE APPLIED | version=1.5.0 collective=sol-01
```

---

## See Also

- [`docs/howto-standalone.md`](howto-standalone.md) — Full environment variable reference
- [`docs/howto-edge.md`](howto-edge.md) — Edge role considerations and ROLE_UPDATE timing
- [`acc/cognitive_core.py`](../acc/cognitive_core.py) — `build_system_prompt()` and `process_task()` source
- [`acc/role_store.py`](../acc/role_store.py) — `RoleStore` 4-tier load order and `apply_update()` source
