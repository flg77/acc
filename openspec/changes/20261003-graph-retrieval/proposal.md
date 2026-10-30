# 20261003-graph-retrieval — proposal

## Why

Vector recall finds *similar text*; it does not follow relations — a policy → the
products it caps → who owns them — and a small edge model cannot reconstruct them from
prose chunks. RDF-04/05 put relations in a governed store; RDF-06 (vault
`20-backlog/RDF support`) lets a task's prompt use them.

## What changes

* **`acc/kg_retrieval.py`** — facts for one task:
  * **Seeds:** entities whose label (`skos:prefLabel`, `skos:altLabel`, `dcterms:title`,
    `rdfs:label`) occurs in the task text as a whole word, longest first (`credit risk`
    before `risk`), at most 5; blank nodes and labels under 3 characters are skipped.
  * **One hop:** each seed's outgoing and incoming triples, ends shown by label when
    they have one.
  * **Bounded:** at most 20 facts of at most 300 characters.
  * Every read goes through `KnowledgeStore.query()` — the reader's ceiling and
    private-graph ownership hold exactly as for `kg_query`. Never raises: no store, a
    refused query or anything else yields no facts.
* **`KNOWLEDGE_GRAPH` block** (`acc/context_budget.py`) — renders right after
  `MEMORY_NOTES` and is **evicted first** (priority 6, after peer lessons): its value is
  unmeasured. Display ranks renumbered to make room; every existing block keeps its
  relative order.
* **`CognitiveCore._graph_facts_for()`** — only for a role with `graph_retrieval: true`
  and unless `ACC_GRAPH_RETRIEVAL=0`; ceiling and requester from the admitted task
  (unattributed = CRITICAL, the operator's own). Both prompt paths (budgeted and not)
  render the block identically.
* **Outcome join** — `CognitiveResult.graph_seeds`; `TASK_COMPLETE.graph_seeds` when
  non-empty (absent otherwise, like `lessons_used`), so PA-08 can compare outcomes with
  and without the block.
* `graph_retrieval` role flag (default off); `acc.pipeline.memory_retrieve` reports
  `graph_facts_count`.

## What does not change

* **Which notes and episodes are retrieved** — add-only. Graph-first retrieval (the
  graph choosing notes) was the RDF-06 note's open question; add-only is the lower-risk
  first step it recommended, taken here.
* Every prompt of a role without the flag, and of a task whose text matches no label:
  byte-identical (tested on both prompt paths).

## Decisions

* **D1 — lexical seeds.** Labels matched as whole words: cheap, explainable, no model in
  the loop. Entity linking with an embedding or the model is later, and only if PA-08
  shows the block earns its tokens.
* **D2 — evicted first.** Below peer lessons: hearsay from a peer is at least about this
  collective's recent work; a lexical match may be noise.
* **D3 — keep it only if it helps.** The block stays behind a flag until PA-08's memory
  A/B shows outcome-per-episode improves with it; `graph_seeds` on `TASK_COMPLETE` is
  the join that measurement needs.

## Cost (measured on the workstation, 100k-triple store)

The labels query takes 5–8 ms and each expansion query 1–3 ms once warm. The first
query in a process pays rdflib's SPARQL-parser start-up (~0.6 s) once — an agent is a
long-running process, so it pays it at its first graph-backed task, not per task.
Opening the store read-only per task is measured on the edge host (evidence).
