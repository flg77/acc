# 20261003-kg-skills — proposal

## Why

RDF-04 gave each collective a knowledge store that only an operator could read or fill.
RDF-05 (vault `20-backlog/RDF support`) lets agents use it: read it as the requester,
at the task's ceiling, and propose facts — without the store ever filling from what a
model merely says.

## Decisions (operator, 2026-10-03)

1. **Opt-in.** A role sets `knowledge_graph: true` (like `document_store`) to get
   `kg_query` and `kg_propose`; every other role is unchanged.
2. **Private graph, proposal to share.** Accepted facts land in the requester's private
   graph, readable by them alone; making them readable to others is a `kg_share`
   proposal a person approves.
3. **Shared volume, arbiter writes.** The store sits on a volume the collective's agents
   mount; agents open it read-only, the arbiter is its only writer (Oxigraph admits one
   writing process — verified; read-only opens beside it work — verified).

## What changes

* **`acc/call_context.py`** — `capability_dispatch` sets, per call, the requester, the
  task's ceiling (unattributed = CRITICAL, the operator's own), the collective and the
  task id. Skills that act as the requester read them here — never from their arguments.
* **Owner-aware store** (`acc/kg.py`) — a graph may carry `acc:owner`, a pseudonym (a
  keyed hash under a salt kept with the store, `.owner-salt`, 0600); `graphs()` /
  `query()` take the reader and show a private graph only to its owner, whatever the
  ceiling. One private graph per person per ceiling (`private-<16 hex>-<ceiling>`), so
  every graph keeps one ceiling. `append=True` writes; read-only opens refuse writes and
  never create the salt (no salt = nothing owned yet).
* **SHACL gate** (`acc/kg_shapes.py`) — shapes from the signed pack named by
  `ACC_KG_SHAPES` (`shapes/*.ttl`); **no shapes, no writes**. Proposals are Turtle with
  ACC prefixes predeclared, at most 200 triples, literals ≤ 4,000 chars, validated with
  `pyshacl`; violations are returned so the model can correct and retry.
* **Write path** (`acc/kg_writes.py`, `acc/agent.py`) — `kg_propose` validates, then
  the agent signs (PA-09) and publishes a `KG_WRITE` on **`acc.{cid}.kg.write`**. The
  arbiter verifies the sender, the roster, and that the sender's signed role definition
  carries `knowledge_graph`; **re-validates** against the shapes; appends to the
  requester's private graph at the task's ceiling; and, for `share: true`, queues a
  **`kg_share`** proposal.
* **`kg_share`** (`acc/assistant_proposal.py`) — HIGH, never auto-executed (like
  `publish`); refused without a named approver; dispatched only by the arbiter (enforced
  with or without NKeys — the store lock is not a bus permission); re-validates and
  appends to `shared-<ceiling>`; announces `kg_shared` / `kg_share_refused`.
* **Skills** — `skills/kg_query` (LOW: SELECT / ASK / CONSTRUCT / DESCRIBE at the task's
  ceiling as the requester, results capped at `limit` ≤ 200) and `skills/kg_propose`
  (MEDIUM).
* **NATS matrix** — workers may publish `acc.*.kg.write` (the arbiter already subscribes
  to `acc.>`); the operator's embedded copy is synced.
* `ACC_KG_SHAPES` registered; `pyshacl` in the `rdf` and `dev` extras;
  `acc-cli kg graphs|query` read as the caller (an operator sees their own private
  graphs, nobody else's).

## Trust boundary (stated, not solved)

The arbiter proves the sending **agent** and its role's flag. The requester and ceiling
on a write are what that agent says it was serving — the same trust every episode the
agent attributes already rests on. A compromised agent can write into the private graph
of a requester it serves; it cannot widen a ceiling, write another collective's store,
or make anything shared without a person's approval.

## Not in this change

* **Deployment of the shared volume** — the edge compose mount and an RWX PVC in the
  operator (with `ACC_KG_PATH` on every agent). Until then agents on separate
  filesystems see no store; `kg_query` says so and `kg_propose` writes land only where
  the arbiter's filesystem is.
* **Hub-owned shapes.** Shapes come from the collective's own installed pack; whether
  they follow the taxonomy's hub rule (RDF-02) is open.
* **Per-triple provenance** (RDF 1.2 triple terms). Each write batch records its source
  task and agent in the catalogue; per-triple statements are later.
* **Erasure of a person's private graphs** (`kg forget --person`): the graphs are named
  by pseudonym and can be dropped; the command is later, with the RDF-03 erasure decision.
* The operator rebuild that carries the new matrix entry to clusters.
