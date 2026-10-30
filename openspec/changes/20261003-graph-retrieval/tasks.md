# 20261003-graph-retrieval — tasks

RDF-06, Phase 1.

## 1. Retrieval
- [x] `acc/kg_retrieval.py`: whole-word label seeds (longest first, ≤ 5, IRIs only), one-hop expansion in and out, ≤ 20 facts ≤ 300 chars, through `KnowledgeStore.query` (ceiling + ownership), never raises
- [x] `facts_for_task()` opens the store read-only; no store = no facts

## 2. The block
- [x] `context_budget.KIND_GRAPH`, `standard_blocks(graph_heading, graph_items)`: after notes, evicted first; display ranks renumbered, relative order kept
- [x] both composers render it identically; empty = byte-identical

## 3. The gate and the join
- [x] `graph_retrieval` role flag (off by default); `ACC_GRAPH_RETRIEVAL=0` kill switch
- [x] `CognitiveCore._graph_facts_for()` — ceiling and requester from the task
- [x] `CognitiveResult.graph_seeds`; `TASK_COMPLETE.graph_seeds` when non-empty; `graph_facts_count` in the retrieve stage

## 4. Tests and close-out
- [x] `tests/rdf/test_kg_retrieval.py` (16)
- [x] prompt-path suites (context budget, cognitive core, lessons, steering, thread, memory, config, byte-identity): 1,300 passed
- [ ] PR + edge-host evidence (incl. per-task store-open cost on the edge)
- [ ] PA-08 memory A/B before the flag is turned on for any shipped role
- [ ] vault RDF-06 note
