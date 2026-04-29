# Comms — Extracellular Signalling

This screen visualises the **chemical messaging** between cells:
plans (endocrine), task progress (synaptic), knowledge sharing
(paracrine), and episode nominations (autocrine).

## Panels

### ACTIVE PLAN (top)
The latest `PLAN` signal rendered as an ASCII DAG. Each step shows:

- `PENDING` (grey) — waiting on dependency.
- `RUNNING` (amber) — assigned, in-flight.
- `COMPLETE` (green) — TASK_COMPLETE received.
- `FAILED` (red) — error / blocked.

Up to 5 plans are kept in memory; older plans are evicted FIFO.

### KNOWLEDGE FEED (last 20)
`KNOWLEDGE_SHARE` signals — patterns, anti-patterns, heuristics, or
domain facts emitted by any agent. Filter by tag with the search box.

### SIGNAL FLOW LOG (last 30)
Reverse-chronological log of every signal seen, with the key payload
field summarised:

| Signal | Key field shown |
|--------|----------------|
| HEARTBEAT | state |
| TASK_COMPLETE | blocked |
| ALERT_ESCALATE | reason |
| TASK_PROGRESS | step_label |
| QUEUE_STATUS | queue_depth |
| BACKPRESSURE | state |
| PLAN | plan_id |
| KNOWLEDGE_SHARE | tag |
| EVAL_OUTCOME | outcome |
| CENTROID_UPDATE | drift_score |
| EPISODE_NOMINATE | episode_id |

### EPISODE NOMINATIONS
Tasks scored ≥ 0.85 in EVAL_OUTCOME that were proposed for promotion to
Cat-C. Status: `PENDING` / `PROMOTED` / `REJECTED`.

## Communication modes (by signal mode)

- **SYNAPTIC** — direct point-to-point (TASK_ASSIGN, TASK_COMPLETE).
- **PARACRINE** — local broadcast filtered by `domain_receptors`
  (HEARTBEAT, KNOWLEDGE_SHARE, TASK_PROGRESS).
- **AUTOCRINE** — the agent talks to itself (EVAL_OUTCOME, EPISODE_NOMINATE).
- **ENDOCRINE** — system-wide broadcast (PLAN, ALERT_ESCALATE,
  CENTROID_UPDATE, BRIDGE_DELEGATE).

## Keybindings
- `f` — focus signal filter input
- `1` … `6` — switch screens
- `?` — this help
