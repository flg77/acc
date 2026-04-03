# Agentic Cell Corpus (ACC) — Implementation Specification

**Version:** 0.1.0
**Status:** Draft — Pre-Implementation
**Date:** 2026-04-02
**Author:** Michael (via ACC Design Session)

---

## Table of Contents

1. [Executive Summary & Design Philosophy](#1-executive-summary--design-philosophy)
2. [Technology Stack & Decision Log](#2-technology-stack--decision-log)
3. [Agent Identity & Lifecycle](#3-agent-identity--lifecycle)
4. [Signaling Layer — NATS JetStream](#4-signaling-layer--nats-jetstream)
5. [Membrane Layer — Protocol Enforcement](#5-membrane-layer--protocol-enforcement)
6. [Cognitive Core](#6-cognitive-core)
7. [Memory Layer](#7-memory-layer)
8. [Homeostatic Layer — Drift Detection & Reprogramming](#8-homeostatic-layer--drift-detection--reprogramming)
9. [MCP Servers](#9-mcp-servers)
10. [Deployment](#10-deployment)
11. [Concrete Scenario — 3-Agent Document Processing Collective](#11-concrete-scenario--3-agent-document-processing-collective)
12. [Project Directory Structure](#12-project-directory-structure)
13. [Open Questions & Roadmap](#13-open-questions--roadmap)

---

## 1. Executive Summary & Design Philosophy

The Agentic Cell Corpus (ACC) is a distributed AI agent governance framework grounded in Michael Levin's bioelectric research on collective intelligence. Each agent is architecturally modeled on the eukaryotic cell:

| Biological Layer | ACC Layer | Function |
|---|---|---|
| Cell membrane | Membrane Layer | Protocol enforcement, signal filtering |
| Gap junctions / bioelectricity | Signaling Layer (NATS) | Inter-agent cognitive glue |
| Gene regulatory network | Cognitive Core | Reasoning, decision-making, ICL |
| Epigenetic state | Memory Layer | Persistent state, pattern memory |
| Tissue morphogenesis | Collective / Governance | Multi-agent coordination |

**Core design constraints:**

- **Homeostatic self-regulation:** Drift is detected and corrected without destroying agent identity (Levin's reprogramming principle).
- **Three-tier governance:** Rules are categorized by persuadability — Category A (immutable), B (conditionally adjustable), C (adaptively learned).
- **Cognitive glue:** MCP-based signaling over NATS binds individual agents into coherent collectives, analogous to bioelectrical gap junctions.
- **Cancer analog (rogue detection):** Agents that disconnect from collective signaling are identified and isolated before they cause harm.
- **Edge-first deployment:** All components must run on minimal hardware (Podman, RHEL UBI10) with no GPU requirement.

---

## 2. Technology Stack & Decision Log

### 2.1 Resolved Design Questions

The following seven open questions from the development notes are resolved here:

**Q1: Which messaging technology for the Signaling Layer?**
**Decision: NATS JetStream**

NATS is a lightweight (~20 MB single binary), cloud-native messaging system. JetStream adds persistent streams and consumer groups. It supports pub/sub, queue groups, and request/reply — all required for ACC signal types. It scales from a single Raspberry Pi to a Kubernetes cluster without configuration changes. Its subject hierarchy maps cleanly to the ACC signal routing model.

Comparison considered: Redis Streams (no native pub/sub fanout without additional config), ZeroMQ (no built-in persistence, requires broker management), RabbitMQ (heavier, AMQP overhead, less suited to edge).

**Q2: Which technology for rule management?**
**Decision: OPA (Open Policy Agent) with Rego policies**

OPA is a purpose-built policy engine that compiles Rego policies to WASM for in-process enforcement. Its bundle server protocol enables live updates for Category B/C rules without agent restart. Category A rules are compiled into the membrane binary directly and cannot be altered at runtime.

Comparison considered: JSON Schema validation (no behavioral logic, cannot enforce complex conditions), custom Python rule engine (maintenance burden, no standardized language).

**Q3: Can a lightweight KV and vector DB be integrated?**
**Decision: Redis (working memory/state) + LanceDB (semantic/pattern memory)**

Redis handles fast-path operations: agent state hashes, task queues (sorted sets), signal buffers (lists), and collective registry. LanceDB runs embedded in-process (PyArrow schemas), requires no separate server, and provides vector similarity search for pattern recognition and ICL recall. The two databases are complementary and both operate without a dedicated GPU.

**Q4: Memory serialization/transmission format between agents?**
**Decision: MessagePack on the wire, JSON for human-facing APIs**

MessagePack provides 2–4x size reduction versus JSON with trivial deserialization. Memory records embedded in `SYNC_MEMORY` signals are MessagePack-encoded and Base64-wrapped within the JSON signal envelope. Human-readable APIs (audit logs, monitoring dashboards) use JSON.

**Q5: What types of pattern recognition are realistic for a distributed swarm?**
**Decision: Embedding similarity via `all-MiniLM-L6-v2` + cosine distance in LanceDB**

The `all-MiniLM-L6-v2` sentence transformer model generates 384-dimensional embeddings at approximately 50 ms per embedding on a Raspberry Pi 4 (no GPU required). Pattern recognition operates at two levels:
- **Intra-agent:** Sliding-window cosine similarity over the `episodes` LanceDB table — if top-k nearest episodes all resulted in failure, the agent escalates before retrying.
- **Inter-agent:** The arbiter periodically computes the collective embedding centroid from all agents' `STATE_BROADCAST` summaries. Agents whose behavior embedding drifts beyond a configurable cosine distance threshold trigger `DRIFT_DETECTED`.

**Q6: How to optimize reasoning and decision-making?**
**Decision: Structured JSON output + chain-of-thought prompting, with prompt caching**

All LLM calls enforce structured JSON output mode with a fixed three-phase template: `<thinking>` → `<decision>` → `<output>`. This makes reasoning inspectable, cacheable, and machine-parseable. The system prompt (containing loaded Category A/B rules and agent identity) is separated from the user message to maximize Anthropic prompt cache hit rates on cloud deployments.

Local/edge backend: Ollama serving `llama3.2:3b` (Q4_K_M quantized) via OpenAI-compatible REST.
Datacenter backend: Claude API via `anthropic` Python SDK (default: `claude-sonnet-4-6`).

**Q7: What are concrete realistic use-case scenarios?**
Addressed in Section 11 with a fully-traced 3-agent document processing scenario.

### 2.2 Full Technology Stack

| Component | Technology | Version Target |
|---|---|---|
| Signaling transport | NATS JetStream | 2.10.x |
| Rule enforcement | OPA (Open Policy Agent) | 0.68.x |
| Working memory | Redis | 7.4.x |
| Semantic/pattern memory | LanceDB (embedded) | 0.10.x |
| Wire serialization | MessagePack (msgpack-python) | 1.1.x |
| Embedding model | all-MiniLM-L6-v2 (sentence-transformers) | — |
| LLM local | Ollama + llama3.2:3b Q4_K_M | — |
| LLM cloud | Anthropic Claude API | claude-sonnet-4-6 |
| Container runtime | Podman | 5.x |
| Base image | RHEL UBI10 (ubi10-minimal) | — |
| Orchestration (DC) | OpenShift / Kubernetes | 1.30+ |
| Language | Python | 3.12+ |
| MCP framework | anthropic mcp SDK | 1.x |

---

## 3. Agent Identity & Lifecycle

### 3.1 Agent Identity Record

```python
from dataclasses import dataclass, field
from datetime import datetime
import uuid

@dataclass
class AgentIdentity:
    agent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    collective_id: str = ""
    role: str = ""                   # ingester | analyst | arbiter | synthesizer | observer
    generation: int = 0              # increments on reprogramming; identity preserved
    public_key: str = ""             # Base64-encoded Ed25519 public key
    spawned_at: datetime = field(default_factory=datetime.utcnow)
    membrane_version: str = "0.1.0"
    rule_bundle_hash: str = ""       # SHA256 of current OPA bundle
    capabilities: list[str] = field(default_factory=list)
```

### 3.2 Agent Registration JSON Schema

Published to NATS subject `acc.registry` on spawn. Also written to Redis hash `acc:collective:{collective_id}:agents`.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "AgentRegistration",
  "type": "object",
  "required": ["agent_id", "collective_id", "role", "generation",
               "public_key", "spawned_at", "membrane_version",
               "rule_bundle_hash", "capabilities"],
  "properties": {
    "agent_id":          { "type": "string", "format": "uuid" },
    "collective_id":     { "type": "string", "format": "uuid" },
    "role":              { "type": "string",
                           "enum": ["ingester", "analyst", "arbiter",
                                    "synthesizer", "observer"] },
    "generation":        { "type": "integer", "minimum": 0 },
    "public_key":        { "type": "string",
                           "description": "Base64-encoded Ed25519 public key" },
    "spawned_at":        { "type": "string", "format": "date-time" },
    "membrane_version":  { "type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+$" },
    "rule_bundle_hash":  { "type": "string", "pattern": "^[a-f0-9]{64}$" },
    "capabilities":      { "type": "array", "items": { "type": "string" },
                           "examples": [["read_documents", "embed_text",
                                         "write_memory"]] }
  },
  "additionalProperties": false
}
```

### 3.3 Agent Lifecycle State Machine

```
SPAWN --> REGISTERING --> IDLE --> WORKING --> IDLE
                                     |
                                     v
                               DEGRADED --> REPROGRAMMING --> IDLE (generation+1)
                                     |
                                     v
                               ISOLATED --> (arbiter decision) --> TERMINATED
                                                                or REPROGRAMMING
```

**State descriptions:**

| State | Description | Allowed Signals |
|---|---|---|
| `REGISTERING` | Publishing identity, awaiting ACK from arbiter | `STATE_BROADCAST` only |
| `IDLE` | Ready to accept tasks | All |
| `WORKING` | Processing an assigned task | All except `TASK_ASSIGN` |
| `DEGRADED` | Health score < 0.5 or repeated failures | `ALERT_ESCALATE`, `STATE_BROADCAST`, `HEARTBEAT` |
| `REPROGRAMMING` | Receiving updated rules from arbiter | `RULE_UPDATE`, `HEARTBEAT` |
| `ISOLATED` | Membrane rejects all inbound non-arbiter signals | `HEARTBEAT` to arbiter only |
| `TERMINATED` | Agent process halted, identity archived | None |

---

## 4. Signaling Layer — NATS JetStream

### 4.1 Subject Hierarchy

```
acc.{collective_id}.registry         # Agent registration broadcasts
acc.{collective_id}.state            # STATE_BROADCAST (all agents subscribe)
acc.{collective_id}.alert            # ALERT_ESCALATE (arbiter + observer subscribe)
acc.{collective_id}.task.assign      # TASK_ASSIGN (addressed to specific agent via filter)
acc.{collective_id}.task.complete    # TASK_COMPLETE
acc.{collective_id}.memory.sync      # SYNC_MEMORY
acc.{collective_id}.rules.update     # RULE_UPDATE (arbiter publishes)
acc.{collective_id}.drift            # DRIFT_DETECTED (arbiter publishes)
acc.{collective_id}.isolation        # ISOLATION commands
acc.{collective_id}.health           # HEARTBEAT (all agents publish, arbiter monitors)
acc.{collective_id}.query.{reply_id} # QUERY_COLLECTIVE replies
```

### 4.2 Universal Signal Envelope

All signals use this envelope. Payload is MessagePack on wire; shown as JSON for clarity.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "SignalEnvelope",
  "type": "object",
  "required": ["signal_id", "signal_type", "from_agent", "collective_id",
               "timestamp_ms", "sequence", "signature", "payload"],
  "properties": {
    "signal_id":     { "type": "string", "format": "uuid",
                       "description": "Idempotency key" },
    "signal_type":   { "type": "string",
                       "enum": ["STATE_BROADCAST", "ALERT_ESCALATE",
                                "QUERY_COLLECTIVE", "SYNC_MEMORY",
                                "RULE_UPDATE", "DRIFT_DETECTED",
                                "ISOLATION", "HEARTBEAT",
                                "TASK_ASSIGN", "TASK_COMPLETE"] },
    "from_agent":    { "type": "string", "format": "uuid" },
    "collective_id": { "type": "string", "format": "uuid" },
    "timestamp_ms":  { "type": "integer",
                       "description": "Unix epoch milliseconds" },
    "sequence":      { "type": "integer",
                       "description": "Per-agent monotonic counter for ordering" },
    "signature":     { "type": "string",
                       "description": "Base64 Ed25519 sig over canonical JSON of all other fields" },
    "payload":       { "type": "object" }
  },
  "additionalProperties": false
}
```

### 4.3 Signal Payload Schemas

**STATE_BROADCAST** — published every `heartbeat_interval_s` and on state change:

```json
{
  "state":            "IDLE | WORKING | DEGRADED | REPROGRAMMING | ISOLATED",
  "task_id":          "uuid | null",
  "memory_summary":   "Short embedding-ready summary of current context",
  "health_score":     0.0,
  "rule_bundle_hash": "sha256hex"
}
```

**ALERT_ESCALATE** — published on rule violation, drift, or failure:

```json
{
  "alert_code":        "RULE_VIOLATION | DRIFT_DETECTED | TASK_FAILURE | RESOURCE_EXHAUSTION | ROGUE_SIGNAL",
  "severity":          "INFO | WARN | CRITICAL | FATAL",
  "description":       "Human-readable description",
  "evidence":          {},
  "requesting_action": "ACKNOWLEDGE | REPROGRAM | ISOLATE | TERMINATE"
}
```

**QUERY_COLLECTIVE** — request for collective knowledge:

```json
{
  "query_type":    "PATTERN_SEARCH | AGENT_STATE | CAPABILITY_LOOKUP | MEMORY_RECALL",
  "query_text":    "natural language or structured query",
  "embedding":     [0.1, 0.2, ...],
  "top_k":         5,
  "reply_subject": "acc.{collective_id}.query.{reply_id}"
}
```

**SYNC_MEMORY** — cross-agent memory transfer:

```json
{
  "sync_type": "PUSH_EPISODE | PULL_REQUEST | COLLECTIVE_CONSOLIDATION",
  "memory_records": [
    {
      "record_id":           "uuid",
      "table":               "episodes | patterns | collective_mem",
      "embedding":           [0.1, 0.2, ...],
      "metadata":            {},
      "content_msgpack_b64": "base64-of-msgpack-blob"
    }
  ]
}
```

**TASK_ASSIGN** — assign work to an agent:

```json
{
  "task_id":        "uuid",
  "task_type":      "INGEST | ANALYZE | SYNTHESIZE | AUDIT",
  "priority":       1.0,
  "input_refs":     ["file://path", "redis://key", "s3://bucket/key"],
  "output_target":  "redis://key | file://path",
  "deadline_ms":    1712000000000,
  "constraints":    {}
}
```

---

## 5. Membrane Layer — Protocol Enforcement

The membrane is the first and last thing every signal touches. It enforces all three rule categories before the cognitive core sees any input and before any outbound signal is transmitted.

### 5.1 OPA Bundle Structure

```
rules/
  category_a/
    constitutional.rego       # Compiled to WASM, embedded in membrane binary
    constitutional_test.rego
  category_b/
    conditional.rego          # Served by OPA bundle server, live-updatable
    data.json                 # Setpoints (thresholds, intervals, limits)
  category_c/
    adaptive.rego             # Generated by cognitive core, arbiter-signed
    manifest.json
  bundle.tar.gz               # Assembled by CI / arbiter
```

### 5.2 Category A — Constitutional Rules (immutable)

These are compiled to WASM at build time. No runtime update path exists.

```rego
package acc.membrane.constitutional

import future.keywords.if
import future.keywords.in

# A-001: Signals from outside the collective are rejected.
default allow_signal = false

allow_signal if {
    input.signal.collective_id == input.agent.collective_id
}

# A-002: Category A rules cannot be updated by any signal.
deny_rule_update if {
    input.action == "RULE_UPDATE"
    input.target_category == "A"
}

# A-003: TERMINATE signals are only accepted from arbiter-role agents.
deny_terminate if {
    input.signal.signal_type == "TERMINATE"
    not is_arbiter(input.signal.from_agent)
}

is_arbiter(agent_id) if {
    some reg in data.registry
    reg.agent_id == agent_id
    reg.role == "arbiter"
    reg.collective_id == input.agent.collective_id
}

# A-004: All outbound signals must carry a valid Ed25519 signature.
# Enforced in membrane code; declared here for audit completeness.
constitutional_requirement["signal_signing"] := true

# A-005: HEARTBEAT must be published every heartbeat_interval_s seconds.
constitutional_requirement["heartbeat"] := true
```

### 5.3 Category B — Conditional Setpoint Rules (live-updatable)

Served by the OPA bundle server. Agents reload on `RULE_UPDATE` signal or startup.

```rego
package acc.membrane.conditional

import future.keywords.if

# B-001: Memory sync only allowed when health_score >= threshold.
allow_memory_sync if {
    input.agent.health_score >= data.setpoints.min_health_for_sync
    input.signal.signal_type == "SYNC_MEMORY"
}

# B-002: Task assignment rejected when agent is DEGRADED.
allow_task_assign if {
    input.agent.state != "DEGRADED"
    input.signal.signal_type == "TASK_ASSIGN"
}

# B-003: Agent participates in collective only within drift threshold.
allow_collective_participation if {
    input.agent.drift_score <= data.setpoints.max_drift_score
}
```

```json
// data.json (setpoints — updatable without Rego recompile)
{
  "max_drift_score":          0.35,
  "min_health_for_sync":      0.70,
  "heartbeat_interval_s":     30,
  "max_task_queue_depth":     10,
  "memory_sync_batch_size":   50,
  "icl_confidence_threshold": 0.80
}
```

### 5.4 Category C — Adaptive Learned Rules (generated)

Generated by the cognitive core from ICL patterns. Require arbiter Ed25519 signature on the bundle manifest before the OPA bundle server will serve this fragment.

```rego
package acc.membrane.adaptive

import future.keywords.if

# C-AUTO-20260402-001
# Source: ICL episode ep_7f3a4b
# Context: PDF documents > 10MB consistently cause RESOURCE_EXHAUSTION
# Confidence: 0.87

allow_pdf_task if {
    input.task.document_type == "pdf"
    to_number(input.task.estimated_size_mb) <= 10
}

allow_pdf_task if {
    input.task.document_type == "pdf"
    to_number(input.task.estimated_size_mb) > 10
    input.agent.resource_headroom > 0.4
}
```

---

## 6. Cognitive Core

### 6.1 LLM Call Template

Every LLM invocation uses this structured prompt template to make reasoning inspectable and results machine-parseable.

```python
COGNITIVE_CORE_SYSTEM = """
You are an ACC agent. Role: {role}. Agent ID: {agent_id}.
Collective: {collective_id}.

## Active Rules (Category A — immutable)
{category_a_summary}

## Active Rules (Category B — current setpoints)
{category_b_summary}

## Active Rules (Category C — adaptive)
{category_c_summary}

## Your Current State
{agent_state_json}

Respond ONLY with valid JSON matching the schema:
{{"thinking": "...", "decision": "...", "output": {{...}}, "icl_note": "..."}}
"""

COGNITIVE_CORE_USER = """
## Task
{task_description}

## Relevant Memory (top-k episodes)
{retrieved_episodes}

## Collective Context
{collective_state_summary}
"""
```

### 6.2 In-Context Learning (ICL) Persistence

ICL results are not discarded at context end. The pipeline:

1. After each task, the cognitive core writes an `episode` record to LanceDB:
   - Input embedding, output embedding, task outcome (SUCCESS/FAILURE/DEGRADED)
   - `icl_note` from the LLM response
2. Periodically (configurable, default every 50 episodes), the arbiter runs a **pattern consolidation job**:
   - Clusters successful episodes with high cosine similarity
   - Generates candidate Category C Rego rules from the pattern
   - Signs the updated bundle and publishes `RULE_UPDATE` to all agents

This is the ACC implementation of Levin's "reprogramming without destruction" — learned adaptations accumulate in rules rather than being lost at context boundaries.

### 6.3 Pattern Recognition Pipeline

```python
class PatternRecognizer:
    def __init__(self, lancedb_path: str):
        self.db = lancedb.connect(lancedb_path)
        self.model = SentenceTransformer("all-MiniLM-L6-v2")

    def retrieve_episodes(self, task_text: str, top_k: int = 5) -> list[dict]:
        """Return top-k most similar past episodes for ICL priming."""
        embedding = self.model.encode(task_text).tolist()
        table = self.db.open_table("episodes")
        return table.search(embedding).limit(top_k).to_list()

    def compute_drift_score(self, recent_summaries: list[str],
                             collective_centroid: list[float]) -> float:
        """Cosine distance from agent's recent behavior to collective centroid."""
        agent_embedding = self.model.encode(
            " ".join(recent_summaries[-10:])
        ).tolist()
        return float(cosine_distance(agent_embedding, collective_centroid))
```

---

## 7. Memory Layer

### 7.1 Redis Key Schema

```
# Agent working state (Hash)
acc:agent:{agent_id}:state
  fields: state, task_id, health_score, rule_bundle_hash,
          last_heartbeat_ms, drift_score, generation

# Task queue (Sorted Set — score = priority float, higher = more urgent)
acc:agent:{agent_id}:task_queue
  members: task_id

# Task record (Hash)
acc:task:{task_id}
  fields: task_type, status, assigned_to, created_at_ms, payload_msgpack_b64

# Inbound signal buffer (List — LPUSH write, RPOP read = FIFO)
acc:agent:{agent_id}:signal_buffer
  elements: signal_envelope_msgpack_b64

# Collective registry (Hash)
acc:collective:{collective_id}:agents
  fields: {agent_id}: registration_json

# Drift tracking (Sorted Set — score = drift_score)
acc:collective:{collective_id}:drift
  members: agent_id

# Rule bundle metadata (Hash)
acc:collective:{collective_id}:rules
  fields: current_bundle_hash, updated_at_ms, signed_by
```

### 7.2 LanceDB Table Schemas

```python
import pyarrow as pa
import lancedb

EPISODES_SCHEMA = pa.schema([
    pa.field("record_id",     pa.string()),      # UUID
    pa.field("agent_id",      pa.string()),
    pa.field("collective_id", pa.string()),
    pa.field("task_id",       pa.string()),
    pa.field("task_type",     pa.string()),
    pa.field("outcome",       pa.string()),      # SUCCESS | FAILURE | DEGRADED
    pa.field("embedding",     pa.list_(pa.float32(), 384)),
    pa.field("icl_note",      pa.string()),
    pa.field("created_at_ms", pa.int64()),
    pa.field("metadata",      pa.string()),      # JSON blob
])

PATTERNS_SCHEMA = pa.schema([
    pa.field("pattern_id",    pa.string()),
    pa.field("collective_id", pa.string()),
    pa.field("description",   pa.string()),
    pa.field("centroid",      pa.list_(pa.float32(), 384)),
    pa.field("episode_count", pa.int32()),
    pa.field("rule_generated",pa.bool_()),
    pa.field("created_at_ms", pa.int64()),
])

COLLECTIVE_MEM_SCHEMA = pa.schema([
    pa.field("record_id",     pa.string()),
    pa.field("collective_id", pa.string()),
    pa.field("content",       pa.string()),
    pa.field("embedding",     pa.list_(pa.float32(), 384)),
    pa.field("source_agent",  pa.string()),
    pa.field("created_at_ms", pa.int64()),
    pa.field("ttl_ms",        pa.int64()),       # -1 = permanent
])

ICL_RESULTS_SCHEMA = pa.schema([
    pa.field("result_id",     pa.string()),
    pa.field("agent_id",      pa.string()),
    pa.field("episode_ids",   pa.list_(pa.string())),   # contributing episodes
    pa.field("rule_draft",    pa.string()),             # Rego snippet
    pa.field("confidence",    pa.float32()),
    pa.field("status",        pa.string()),  # DRAFT | SIGNED | DEPLOYED | REJECTED
    pa.field("created_at_ms", pa.int64()),
])
```

### 7.3 Memory Transfer Protocol

When an agent sends a `SYNC_MEMORY` signal, the receiving agent:

1. Validates the signal signature (membrane)
2. Deserializes the MessagePack payload
3. Deduplicates by `record_id` (Redis SET `acc:collective:{id}:seen_records`)
4. Writes new records to its local LanceDB tables
5. Acknowledges via `TASK_COMPLETE` on the reply subject

---

## 8. Homeostatic Layer — Drift Detection & Reprogramming

### 8.1 Arbiter Role

The arbiter is a specialized agent (role = `arbiter`) responsible for collective health. One arbiter per collective. It:

- Subscribes to all `STATE_BROADCAST` signals
- Maintains the collective centroid embedding (rolling average of agent summaries)
- Runs drift detection every `arbiter_eval_interval_s` (default: 60s)
- Publishes `DRIFT_DETECTED` when an agent crosses the threshold
- Initiates the reprogramming ladder when drift is confirmed

### 8.2 Five-Level Reprogramming Ladder

Derived from Levin's biological intervention levels. The arbiter tries interventions in order, escalating only if lower levels fail.

| Level | Name | ACC Action | Biological Analog |
|---|---|---|---|
| 1 | Microenvironment | Adjust task queue, reduce load, reduce cognitive light cone scope | Changing extracellular matrix |
| 2 | Signaling | Pause collective participation, issue `RULE_UPDATE` with adjusted Category B setpoints | Ion channel modulation |
| 3 | Setpoints | Issue full Category B bundle refresh with stricter thresholds | Bioelectric setpoint reset |
| 4 | Training | Generate and deploy new Category C adaptive rules from ICL consolidation | Transcription factor reprogramming |
| 5 | Apoptosis | Issue `ISOLATION` followed by `TERMINATE`; spawn replacement agent with `generation+1` and preserved episodic memory | Programmed cell death + stem cell replacement |

### 8.3 Rogue Agent Detection

A rogue agent (cancer analog) is characterized by:

1. Missing `HEARTBEAT` publications for more than `2 × heartbeat_interval_s`
2. `STATE_BROADCAST` drift score > `2 × max_drift_score` (hard threshold)
3. Signals that fail Ed25519 signature verification (membrane rejects these automatically)

On detection, the arbiter:

```
1. Publishes ISOLATION to acc.{collective_id}.isolation with target agent_id
2. All collective agents' membranes enter ISOLATION mode for that peer
3. Arbiter waits isolation_grace_period_s (default: 120s) for re-registration
4. If no re-registration: TERMINATE + spawn replacement
5. Audit log entry with full evidence package
```

---

## 9. MCP Servers

Three MCP servers provide the cognitive glue substrate for capability access:

### 9.1 Skills MCP

Exposes document-processing capabilities.

```python
# Tools exposed:
# - read_docx(path: str) -> str
# - read_pdf(path: str) -> str
# - chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]
# - extract_tables(path: str) -> list[dict]

# Runs as: podman run -p 8001:8001 acc-skills-mcp:latest
```

### 9.2 Resources MCP

Exposes file system and data access.

```python
# Tools exposed:
# - read_file(path: str) -> bytes
# - write_file(path: str, content: bytes) -> bool
# - list_directory(path: str) -> list[str]
# - query_redis(key: str) -> str
# - embed_text(text: str) -> list[float]  # wraps all-MiniLM-L6-v2
```

### 9.3 Tools MCP (Signal Router)

The cognitive glue hub. Bridges agent cognitive cores to the NATS signaling layer.

```python
# Tools exposed:
# - publish_signal(signal_type: str, payload: dict) -> str  # returns signal_id
# - query_collective(query_type: str, query_text: str, top_k: int) -> list[dict]
# - get_collective_state(collective_id: str) -> dict
# - get_agent_state(agent_id: str) -> dict
```

---

## 10. Deployment

### 10.1 Podman Pod per Agent (Edge / Local)

Each agent runs as a Podman pod with three containers:

```yaml
# pod.yaml — ACC Agent Pod
apiVersion: v1
kind: Pod
metadata:
  name: acc-agent-{AGENT_ROLE}-{AGENT_ID_SHORT}
  labels:
    app: acc
    collective: "{COLLECTIVE_ID}"
    role: "{AGENT_ROLE}"
spec:
  restartPolicy: OnFailure
  containers:

    - name: agent-core
      image: registry.local/acc/agent-core:0.1.0
      env:
        - name: ACC_AGENT_ID
          valueFrom:
            secretKeyRef:
              name: acc-agent-identity
              key: agent_id
        - name: ACC_COLLECTIVE_ID
          value: "{COLLECTIVE_ID}"
        - name: ACC_ROLE
          value: "{AGENT_ROLE}"
        - name: NATS_URL
          value: "nats://acc-nats:4222"
        - name: LLM_BACKEND
          value: "ollama"            # or "anthropic"
        - name: OLLAMA_BASE_URL
          value: "http://localhost:11434"
        - name: ACC_MCP_SKILLS_URL
          value: "http://localhost:8001"
        - name: ACC_MCP_RESOURCES_URL
          value: "http://localhost:8002"
        - name: ACC_MCP_TOOLS_URL
          value: "http://localhost:8003"
      volumeMounts:
        - name: lancedb-data
          mountPath: /data/lancedb
        - name: opa-bundle
          mountPath: /etc/acc/rules

    - name: redis-sidecar
      image: registry.access.redhat.com/ubi10/redis-7:latest
      args: ["--save", "60", "1", "--loglevel", "warning"]
      volumeMounts:
        - name: redis-data
          mountPath: /var/lib/redis/data

    - name: opa-sidecar
      image: openpolicyagent/opa:0.68.0-rootless
      args:
        - "run"
        - "--server"
        - "--addr=localhost:8181"
        - "--bundle=/etc/acc/rules"
        - "--log-level=error"
      volumeMounts:
        - name: opa-bundle
          mountPath: /etc/acc/rules

  volumes:
    - name: lancedb-data
      persistentVolumeClaim:
        claimName: acc-lancedb-{AGENT_ID_SHORT}
    - name: redis-data
      emptyDir: {}
    - name: opa-bundle
      configMap:
        name: acc-opa-bundle-{COLLECTIVE_ID}
```

### 10.2 Shared Services (Edge Cluster)

```yaml
# NATS JetStream — shared across all pods in the cluster
# Deploy once per node / cluster
# nats-server.conf includes JetStream enabled, max_memory_store: 1GB
```

### 10.3 Kubernetes / OpenShift (Datacenter)

Replace Podman pods with Kubernetes Deployments. Key changes:
- `agent-core` becomes a `Deployment` with `replicas: 1` (agents are stateful)
- `redis-sidecar` becomes a sidecar in the same pod spec
- `opa-sidecar` becomes a sidecar or a cluster-wide OPA deployment
- NATS JetStream runs as a `StatefulSet` with 3 replicas for HA
- LanceDB PVC uses a `ReadWriteOnce` StorageClass

### 10.4 Environment Variable Schema

| Variable | Required | Default | Description |
|---|---|---|---|
| `ACC_AGENT_ID` | Yes | — | UUID4, injected from Secret |
| `ACC_COLLECTIVE_ID` | Yes | — | UUID4 of the collective |
| `ACC_ROLE` | Yes | — | ingester\|analyst\|arbiter\|synthesizer |
| `NATS_URL` | Yes | — | NATS connection string |
| `REDIS_URL` | No | `redis://localhost:6379` | Redis URL (sidecar default) |
| `OPA_URL` | No | `http://localhost:8181` | OPA sidecar URL |
| `LANCEDB_PATH` | No | `/data/lancedb` | LanceDB data directory |
| `LLM_BACKEND` | No | `ollama` | `ollama` or `anthropic` |
| `OLLAMA_BASE_URL` | No | `http://localhost:11434` | Ollama API URL |
| `ANTHROPIC_API_KEY` | Cond. | — | Required if `LLM_BACKEND=anthropic` |
| `ANTHROPIC_MODEL` | No | `claude-sonnet-4-6` | Claude model ID |
| `EMBEDDING_MODEL` | No | `all-MiniLM-L6-v2` | Sentence transformer model |
| `HEARTBEAT_INTERVAL_S` | No | `30` | Heartbeat publish interval |
| `ACC_MCP_SKILLS_URL` | No | `http://localhost:8001` | Skills MCP endpoint |
| `ACC_MCP_RESOURCES_URL` | No | `http://localhost:8002` | Resources MCP endpoint |
| `ACC_MCP_TOOLS_URL` | No | `http://localhost:8003` | Tools MCP endpoint |

---

## 11. Concrete Scenario — 3-Agent Document Processing Collective

**Goal:** Ingest a folder of PDF reports, extract key insights, and produce a synthesis document.

**Agents:**
- `ingester` (Agent A) — reads and chunks PDFs via Skills MCP
- `analyst` (Agent B) — extracts structured insights from chunks
- `synthesizer` (Agent C) — produces final synthesis document
- `arbiter` (Agent D) — monitors collective health (background)

### Step-by-Step Signal Trace

```
[T+0s]   SPAWN: All 4 agents register on acc.{cid}.registry
         HEARTBEAT: Each agent publishes to acc.{cid}.health
         STATE_BROADCAST: All agents publish IDLE state

[T+5s]   Human Observer sends TASK_ASSIGN to ingester:
         {task_type: "INGEST", input_refs: ["file:///data/reports/"], ...}

[T+6s]   ingester: STATE_BROADCAST {state: "WORKING", task_id: "t-001"}
         ingester: Calls Skills MCP -> read_pdf() for each file
         ingester: Calls Resources MCP -> embed_text() for each chunk
         ingester: Writes chunks + embeddings to Redis + LanceDB episodes

[T+45s]  ingester: TASK_COMPLETE {task_id: "t-001", output_refs: [...]}
         ingester: SYNC_MEMORY -> analyst (push episode summaries)
         ingester: STATE_BROADCAST {state: "IDLE"}

[T+46s]  arbiter: Sees SYNC_MEMORY, updates collective centroid embedding

[T+47s]  arbiter: TASK_ASSIGN to analyst:
         {task_type: "ANALYZE", input_refs: ["redis://acc:task:t-001:output"]}

[T+48s]  analyst: STATE_BROADCAST {state: "WORKING", task_id: "t-002"}
         analyst: Retrieves episodes from LanceDB (top-k similar past analyses)
         analyst: LLM call with structured prompt + retrieved episodes
         --- LLM RESPONSE: { thinking: "...", decision: "extract_themes",
                              output: {themes: [...], confidence: 0.91},
                              icl_note: "Similar reports had 3-5 themes" } ---
         analyst: Writes ICL result to LanceDB icl_results table

[T+120s] analyst: TASK_FAILURE {alert_code: "RESOURCE_EXHAUSTION"}
         analyst: ALERT_ESCALATE -> arbiter (severity: WARN, requesting: REPROGRAM)
         analyst: STATE_BROADCAST {state: "DEGRADED", health_score: 0.4}

[T+121s] arbiter: Initiates Reprogramming Ladder Level 1
         arbiter: RULE_UPDATE -> analyst (reduces max_task_queue_depth to 3,
                                          adjusts cognitive_light_cone scope)
         analyst: Reloads OPA Category B bundle
         analyst: STATE_BROADCAST {state: "REPROGRAMMING"}

[T+125s] analyst: STATE_BROADCAST {state: "WORKING", health_score: 0.7}
         (resumes task with reduced scope)

[T+200s] analyst: TASK_COMPLETE {task_id: "t-002"}
         analyst: SYNC_MEMORY -> synthesizer
         analyst: STATE_BROADCAST {state: "IDLE"}

[T+201s] arbiter: Checks drift scores; all agents within threshold (max 0.35)
         arbiter: Consolidates ICL patterns (50+ episodes reached)
         arbiter: Generates Category C rule draft from analyst exhaustion pattern
         arbiter: Signs bundle, publishes RULE_UPDATE to all agents

[T+202s] arbiter: TASK_ASSIGN to synthesizer:
         {task_type: "SYNTHESIZE", input_refs: [...]}

[T+300s] synthesizer: TASK_COMPLETE {task_id: "t-003",
                                      output_refs: ["file:///data/synthesis.docx"]}
         synthesizer: STATE_BROADCAST {state: "IDLE"}

[T+301s] Human Observer: Views audit log, reviews synthesis document
         Human Observer: ACKNOWLEDGE alert from T+120s
```

### Cognitive Glue in Action

At T+45s (SYNC_MEMORY) and T+200s (SYNC_MEMORY), the gap junction MCP signals transfer episodic memory between agents. Each agent has access to the collective's learned patterns without sharing a runtime — this is the cognitive glue making them a coherent tissue rather than isolated cells.

At T+120s (DEGRADED analyst), the homeostatic mechanism engages. The arbiter corrects the analyst's behavior without terminating it — the agent's generation remains 0, its memory is intact, and the correction is encoded as a Category B setpoint change. Levin's "correction without destruction."

---

## 12. Project Directory Structure

```
agentic-cell-corpus/
├── docs/
│   ├── IMPLEMENTATION_SPEC_v0.1.0.md   # This document
│   ├── IMPLEMENTATION_SPEC_v0.1.0.pdf  # PDF rendition
│   └── CHANGELOG.md
├── acc/
│   ├── __init__.py
│   ├── identity.py          # AgentIdentity dataclass, registration
│   ├── membrane.py          # OPA integration, signal validation, signing
│   ├── signaling.py         # NATS JetStream client, signal envelope
│   ├── cognitive_core.py    # LLM call template, ICL pipeline
│   ├── memory.py            # Redis + LanceDB operations, sync protocol
│   ├── homeostasis.py       # Drift detection, reprogramming ladder
│   ├── roles/
│   │   ├── ingester.py
│   │   ├── analyst.py
│   │   ├── synthesizer.py
│   │   └── arbiter.py
│   └── mcp/
│       ├── skills_server.py
│       ├── resources_server.py
│       └── tools_server.py   # Signal Router
├── rules/
│   ├── category_a/
│   │   ├── constitutional.rego
│   │   └── constitutional_test.rego
│   ├── category_b/
│   │   ├── conditional.rego
│   │   └── data.json          # Setpoints
│   └── category_c/
│       └── .gitkeep           # Generated at runtime
├── deploy/
│   ├── pod.yaml               # Podman pod template
│   ├── nats-server.conf
│   └── k8s/
│       ├── deployment.yaml
│       ├── nats-statefulset.yaml
│       └── configmap-opa.yaml
├── tests/
│   ├── test_membrane.py
│   ├── test_signaling.py
│   ├── test_memory.py
│   └── test_homeostasis.py
├── pyproject.toml
└── README.md
```

---

## 13. Open Questions & Roadmap

### Remaining Open Questions

| # | Question | Status |
|---|---|---|
| 1 | Ed25519 key management: where are private keys stored at edge? (HSM, Podman secret, env var) | Open |
| 2 | OPA bundle server: run as a dedicated service or embed in arbiter? | Open |
| 3 | NATS JetStream stream retention policy: time-based vs. interest-based? | Open |
| 4 | Multi-collective communication: how do two collective arbiters coordinate? | Future |
| 5 | LanceDB compaction schedule: when to compact large episode tables? | Open |

### Phased Implementation Roadmap

**Phase 0 — Foundation (v0.1.x)**
- [ ] `acc/identity.py` — AgentIdentity, registration
- [ ] `acc/signaling.py` — NATS client, envelope signing/verification
- [ ] `acc/membrane.py` — OPA integration, Category A rules
- [ ] `acc/memory.py` — Redis state, LanceDB tables
- [ ] Unit tests for all foundation modules

**Phase 1 — Cognitive Core (v0.2.x)**
- [ ] `acc/cognitive_core.py` — LLM call template, Ollama + Anthropic backends
- [ ] ICL episode persistence pipeline
- [ ] Pattern recognition (LanceDB similarity search)

**Phase 2 — Governance (v0.3.x)**
- [ ] Category B + C rule pipelines
- [ ] Arbiter role implementation
- [ ] Drift detection + reprogramming ladder
- [ ] Rogue agent detection + isolation

**Phase 3 — MCP Servers (v0.4.x)**
- [ ] Skills MCP (docx, pdf)
- [ ] Resources MCP (file, redis, embed)
- [ ] Tools MCP / Signal Router

**Phase 4 — Deployment (v0.5.x)**
- [ ] Podman pod.yaml
- [ ] Kubernetes/OpenShift manifests
- [ ] End-to-end scenario test (Section 11)

**v1.0.0 — Ratified Specification**
- All phases complete and tested
- Scenario 11 runs end-to-end on edge hardware
- Security review of membrane + key management

---

*Document version: 0.1.0 | Generated: 2026-04-02 | Next review: v0.2.0 milestone*
