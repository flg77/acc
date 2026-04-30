# Prompt — Direct operator → agent channel

Screen 7.  The pane lets the operator type a prompt and dispatch it
straight at an ACC agent over NATS, with the reply streamed back into
a chat-history view inside the same screen.

The pane is one concrete implementation of the open
:class:`acc.channels.PromptChannel` Protocol — Slack / Telegram /
WhatsApp adapters in future PRs construct the same Protocol from a
bot daemon and reuse the same wire shape (TASK_ASSIGN with optional
`target_agent_id`, TASK_COMPLETE correlated by `task_id`).

## How a prompt round-trips

1. Operator picks a **target role** (default `coding_agent`).
2. (Optional) types a **target_agent_id** to pin execution to one
   specific agent within that role.  Empty means "any agent of the
   role picks it up" — legacy broadcast behaviour, preserved for
   back-compat.
3. Types the **prompt** in the textarea.
4. Presses **Send** (or `Ctrl+S`).  The pane:
   * generates a fresh UUID `task_id`,
   * registers a per-`task_id` listener on the App's `NATSObserver`,
   * publishes a `TASK_ASSIGN` payload on `acc.{cid}.task` with
     `signal_type=TASK_ASSIGN`, `task_id`, `target_role`,
     optionally `target_agent_id`, and the prompt text in both
     `content` and `task_description`.
5. The agent's task loop filters on `target_agent_id` (drops the
   message if it's not addressed at us), then runs `process_task` →
   `dispatch_invocations` → publishes `TASK_COMPLETE`.
6. The pane's listener resolves on the matching `task_id`, the
   reply lands in the history pane.

If no reply arrives within 60 s, the pane appends a "(timeout)" line
and stops listening.

## Form fields

| Field | Required | Notes |
|-------|----------|-------|
| Target role | yes | One of the discovered roles in the local cluster.  Free-form input also accepted. |
| Target agent id | no | UUID-suffixed agent id, e.g. `coding_agent-deadbeef`.  Leave empty to broadcast. |
| Prompt | yes | TextArea — multiline OK.  Cleared automatically on Send so you can type the next one. |

## Buttons + keys

| Action | Mouse | Keyboard |
|--------|-------|----------|
| Send | "Send" button | `Ctrl+S` |
| Clear history | "Clear history" button | `Ctrl+L` |
| Navigate to other screens | NavBar buttons | `1`–`7` |

## History pane

Each round-trip produces three timestamped blocks:

* **operator → coding_agent / agent-id** — your prompt.
* **agent-id  task=… latency=…ms** — the agent's reply (green when ok,
  red when blocked).  Latency is the LLM call wall-clock from the
  agent's `CognitiveResult`.
* **system  task=…** — only on error / timeout.

Capped at the most recent 100 entries (FIFO).

## Status line

Below the form, one of:

* `Idle.`
* `Sent task_id=… — awaiting reply…` (yellow)
* `Reply received [ok|blocked] — agent=… latency=…ms` (dim)
* `Timed out waiting for reply.` (red)

## When to use this vs. PLAN / Nucleus

* **Prompt pane** — quick directed work; one operator → one agent.
* **Nucleus** — change a role's `RoleDefinitionConfig` (persona,
  task_types, allowed_actions, …).
* **PLAN** (CLI / future TUI) — multi-step DAG across several agents.

## Out of scope

* TASK_PROGRESS streaming — `TUIPromptChannel.supports_streaming()`
  returns False today; the pane shows the final TASK_COMPLETE only.
  A future PR can flip the flag once the agent's progress emissions
  stabilise.
* Slack / Telegram / WhatsApp — those are separate channels
  (each a small follow-up PR) constructing the same `PromptChannel`
  Protocol from a bot daemon.

## See also

* [`acc/channels/base.py`](../../channels/base.py) — Protocol surface.
* [`acc/channels/tui.py`](../../channels/tui.py) — TUI implementation.
* [`acc/agent.py:_handle_task`](../../agent.py) — `target_agent_id`
  filter + `task_id` echo on TASK_COMPLETE.
