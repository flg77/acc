# 20261030-external-work-board — proposal

## Why

The operator works through coding agents ACC does not run — Claude Code via
Vertex, Codex, Cursor — and wants ACC's assistant to act as their chief of staff
(vault proposal 062, backlog CS-00). The work board (HG-39 / DS-09) shows only
what ACC's own agents do, and its items do not say who did the work. Before any
agent can report in (CS-02, ACC as an MCP server), the board needs a place for
that work and a field that names the executor. This change is CS-03 and CS-10.

## What changes

* **`acc/external_work.py`** — `ExternalWorkItem` (title, status QUEUED /
  RUNNING / DONE / FAILED, executor, session, repo, branch, requester, brief,
  handoff_from, progress lines (last 20), outcome, PR, diffstat, reported cost and
  tokens) and `ExternalWorkStore` beside the oversight queue, like missions:
  Redis `acc:{cid}:ext_work:{item_id}` + set `acc:{cid}:ext_work`, in process
  without Redis. No TTL; a finished item is the record of the work.
* **Signal** `EXTERNAL_WORK` (endocrine) on `acc.{cid}.external.work`,
  payload `{"signal_type", "item": <summary row>}`. Nothing publishes it yet;
  CS-02 will.
* **Arbiter HEARTBEAT** carries `external_work` (newest 50 summary rows), so a
  surface that joins late still shows the items.
* **Observer** — `CollectiveSnapshot.external_work` (item_id → row): the arbiter
  heartbeat replaces it, an `EXTERNAL_WORK` signal updates one row.
* **Board projection** — `project_board(..., external_work=, now=)`: kind
  `external`, `ext:<item_id>`; a RUNNING item without progress for 24 h gets
  `status_detail="stale"` (flagged, never moved); a handed-over QUEUED item says
  by whom. `WorkItem` gains `executor`, `source` (`acc` / `external`), `repo`,
  `cost_usd`, `tokens`; the collective's own items get `executor="acc:<role>"`.
  An external item has no cancel / retry / reassign — it is the agent's to move.
  `filter_snapshot` keeps a viewer to the external items they asked for.
* **Surfaces** — TUI Board shows the executor for external rows and a detail
  block (executor, repo, last progress, reported tokens and cost). WebGUI Board
  shows an executor chip, the repo and the last progress line, and a filter by
  executor when more than one is on the board.

## What does not change

* No new way to write an item from outside yet — that is CS-02.
* ACC's own items: same columns, same controls; `executor` is added only.
* Cost and tokens of ACC's own tasks are not filled yet (the signal log does not
  carry them); external items carry what their agent reports.
