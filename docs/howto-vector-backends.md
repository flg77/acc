# Vector backends — choosing TurboVec, LanceDB, or Milvus

ACC stores all embeddings (episodic memory, memory notes, ICL results, role
purpose centroids, and the RAG document store) behind one swappable seam:
`vector_db.backend` in `acc-config.yaml`, or `spec.infrastructure.vectorBackend`
on an `AgentCorpus`. Three backends are supported. This guide explains the
trade-offs so you can pick per deployment.

## TL;DR

| | **TurboVec** | **LanceDB** | **Milvus** |
|---|---|---|---|
| Shape | embedded (in-pod) | embedded (in-pod) | shared service |
| Footprint | tiny — 4-bit quantized (~16× smaller) | full fp32 records | external cluster |
| Extra infra | **none** | none | a Milvus install |
| Multi-writer | no (single process) | no | **yes** |
| Best for | edge, demos, per-corpus recall out of the box | embedded full-record store | large shared multi-writer corpora |
| rhoai default | **yes** (when no Milvus URI) | — | when a Milvus URI is set |
| standalone/edge default | — | **yes** | — |

## TurboVec (default in rhoai when no Milvus is configured)

TurboVec is an embedded, CPU-only quantized vector index (Google Research's
TurboQuant). It needs **zero extra infrastructure**: the index plus a small
SQLite record store live in the agent pod, persisted to a per-pod PVC. A fresh
corpus therefore comes up with **working vector recall out of the box** — no
StatefulSet to provision, no endpoint to wire.

**Pros**
- No infrastructure. Critical on ephemeral RHOAI test clusters (which rotate
  every 2–3 days) and at the edge.
- ~16× smaller than fp32 (4-bit quantization): ~1M 384-dim episodes fit in
  well under 200 MB of RAM.
- Data-oblivious — no training/calibration pass; vectors are searchable the
  moment they are added (good for corpora that churn at runtime).
- Governed RAG: retrieval scope (collective / sub-collective / Cat-B
  boundary) is enforced **inside the search kernel** via an id allowlist —
  no over-fetch, and selective scopes are *faster*, not slower.
- Lighter image (no pyarrow / pymilvus).

**Cons / limits**
- **Single-writer, single-process.** One index per pod. ACC runs each agent as
  a StatefulSet so every replica gets its own PVC — but the per-replica indexes
  are independent (they do not share a corpus). For a *shared* multi-writer
  corpus, use Milvus.
- Lossy quantization (4-bit). Recall is effectively lossless at k≥4 for ACC's
  retrieval patterns (top-5/10), but if you need exact top-1 over adversarial
  distributions, rerank or use Milvus.
- Young dependency (first release 2026-05): pinned exact-version, hidden behind
  the backend seam, with a LanceDB fallback if the wheel is unavailable.

**When to pick it:** edge bundles, demos, single-tenant corpora, and any RHOAI
project that just wants recall without standing up Milvus.

## LanceDB (default standalone / edge)

Embedded, full-record (fp32) store. The historical ACC default for
standalone and edge. Keeps complete records on disk; no quantization loss.

**Pros:** exact vectors; mature; full-record store in one place.
**Cons:** larger footprint (fp32 + pyarrow); single-writer like TurboVec;
no in-kernel governed filtering.

**When to pick it:** standalone deployments where you want exact fp32 recall
and don't mind the heavier footprint.

## Milvus (rhoai, when a URI is configured — and can be used *in addition*)

A shared datacenter vector service. ACC does **not** install Milvus; it
connects to one you run. This is the right choice for **large, shared,
multi-writer corpora** — many agents/collectives reading and writing one
logical vector store at datacenter scale.

> **Milvus is still fully supported and is the recommended choice for shared
> multi-writer corpora.** TurboVec being the zero-infra default does not
> replace it. You can run TurboVec for per-corpus/edge recall *and* point
> heavier collectives at a shared Milvus in the same fleet — set
> `infrastructure.vectorBackend: milvus` + `infrastructure.milvus.uri` on the
> corpora that need it.

**Pros:** multi-writer; horizontal scale; mature ANN indexes.
**Cons:** you must run + operate it; wrong shape for 2–3-day sandboxes and
edge; requires a reachable URI.

**When to pick it:** datacenter, many writers, one shared corpus, scale beyond
a single pod.

## How the default resolves (operator)

On an `AgentCorpus`, `spec.infrastructure.vectorBackend` selects explicitly
(`turbovec` / `lancedb` / `milvus`). When left empty:

- **rhoai** → `turbovec`, *unless* `infrastructure.milvus.uri` is set, in which
  case `milvus`.
- **standalone / edge** → `lancedb`.

An explicit `milvus` backend requires `infrastructure.milvus.uri` (validated at
config load). The console install form surfaces this: TurboVec is preselected
for RHOAI, with a hint that Milvus remains available and can be configured in
addition for shared corpora.

## Persistence

All three persist across restarts in a normal deployment:

- **TurboVec / LanceDB**: the agent StatefulSet mounts a per-replica PVC at
  `/app/data` (`acc-data`, ReadWriteOnce, default 2Gi). The TurboVec index is
  *derived data* — if the `.tvim` file is lost, it is rebuilt from the SQLite
  record store on next start.
- **Milvus**: persistence is Milvus's own concern.

**Scaling caveat:** because embedded backends are single-writer, scaling one
role's StatefulSet to N replicas yields N independent per-pod corpora, not one
shared corpus. If a role needs a shared corpus across replicas, point it at
Milvus.

## Switching backends

Switching is a config change (`vector_db.backend` / `infrastructure.vectorBackend`)
— the seam is three methods (`create_table_if_absent`, `insert`, `search`), so
no calling code changes. Existing data does not migrate automatically; a new
backend starts empty and repopulates as the agent runs (episodic memory and the
RAG corpus rebuild from new activity / re-ingestion).
