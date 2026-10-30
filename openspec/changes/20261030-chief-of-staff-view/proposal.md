# 20261030-chief-of-staff-view — proposal

## Why

The coding agents now report their work items, sessions and notes to ACC
(`20261030-acc-mcp-server`). The assistant, which the operator wants as chief of
staff for those agents (vault proposal 062, backlog CS-11), cannot see any of it:
the record sits in Redis and no prompt block reads it. Asked "who is on what", it
would answer from nothing, or invent.

## What changes

* **`acc/coding_agents_view.py`** — `gather(redis, cid)` reads the external work
  items, the sessions of the last week and the latest notes. `render(view)` builds
  one bounded block, `## Coding agents (outside the collective)`:
  * open items (RUNNING with the last progress and how long ago, flagged STALE after
    a day without progress; QUEUED with who it is for, who handed it over and the
    brief), at most 15;
  * items finished in the last day with outcome and PR, at most 5;
  * sessions of the last day with their purpose, at most 6;
  * the latest 5 notes, first line only;
  * an instruction to cite items by id, because they are the only ones that exist;
  * at most 3500 characters, cut at a line with "… (more on the work board)".

  Nothing reported means an empty string.
* **`CognitiveCore`** — in the Observe step, for the `control` perception profile
  (the assistant) with a Redis client, the block is gathered per task. It renders
  right after the perception block in the system prompt, the live-state section it
  belongs with. Failures are logged at debug and never block the task.
  `ACC_CODING_AGENTS_VIEW=0` switches it off. An empty block leaves the prompt
  byte-identical.
* **Golden prompts** — `cos_who_is_on_what`, `cos_what_is_blocked` and
  `cos_next_for_codex`. Each expects an `ext-…` id (or, for blocked, an explicit
  "nothing is stuck"). They need at least one reported item on the stack they run
  against.

## What does not change

* No role file changes. The block is tied to the `control` profile the assistant
  already has, not to a new role flag.
* The notes are not embedded into the document store. Semantic recall over them
  is a later step if the long-running test (CS-14) shows the word match is not
  enough.
