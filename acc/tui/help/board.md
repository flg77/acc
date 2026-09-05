# Board — work in flight

What the runtime is doing on your behalf, as a list of cards under five
headers: **QUEUED · RUNNING · BLOCKED · DONE · FAILED**. Cards come from the
PLAN DAG executor's steps, cluster fan-out members, single prompt tasks, and
the oversight gates that block them. The board holds no state of its own —
every key publishes a signal the arbiter applies, and the next tick moves
the row.

Nobody drags a card to Done. The runtime moves cards; you intervene.

| Key | On the highlighted row |
|-----|------------------------|
| `c` | Cancel (queued / running / blocked). A plan step → `PLAN_STEP_CONTROL cancel` (dependents are skipped); a single task → `TASK_CANCEL`. |
| `r` | Retry a failed or cancelled plan step (and the dependents that were only skipped). |
| `a` | Reassign a failed / queued plan step to another role from the roster (pick with a key). |
| `g` | Go to the gate a Blocked item waits on — the Prompt pane's permission request. |
| `Enter` | Detail: description, dependencies, task id, reviewer iteration + critique, outcome. |

Reachable from every profile: `Ctrl+A` then its digit, or `Ctrl+P` → "Go to Board".

Related: `?` on Prompt (answering a gate), Compliance (the record and history),
Comms → ACTIVE PLAN (the DAG), `docs/howto-tui.md`.
