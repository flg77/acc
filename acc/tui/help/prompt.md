# Prompt — Direct operator → agent channel

Screen 7.  Chat-style pane: the operator types a prompt at the
**bottom** of the screen, the agent's thinking + actions + reply
land in the **transcript** at the centre.  One concrete implementation
of the open `acc.channels.PromptChannel` Protocol — Slack / Telegram /
WhatsApp adapters in future PRs construct the same Protocol from a
bot daemon and reuse the same wire shape (TASK_ASSIGN with optional
`target_agent_id`, TASK_COMPLETE correlated by `task_id`).

## Layout

```
┌─────────────────────────────────────────────────┐
│ NavigationBar (1–7)                              │
├─────────────────────────────────────────────────┤
│ Target: <select role>  Agent id: <input>         │  compact
├─────────────────────────────────────────────────┤
│                                                  │
│   TRANSCRIPT (operator + agent + traces)         │  centre, flex
│                                                  │
├─────────────────────────────────────────────────┤
│ [Type your prompt …                  ]  [Send]   │  fixed
│ Status: idle                                     │
└─────────────────────────────────────────────────┘
```

## Transcript entry types

| Type | Colour | Shows |
|------|--------|-------|
| **operator** | cyan header | Your prompt as submitted |
| **progress** | dim blue arrow `→` | Live `step N/M — <step_label>` lines as the agent emits TASK_PROGRESS.  Tail confidence trend marker: `↑` rising / `→` stable / `↓` falling.  See "live thinking" below. |
| **trace** | one line per dispatched skill/MCP tool — `✓ skill:echo` (green) or `✗ mcp:fs.read  A-018 blocked` (red) | What the agent *did* on its way to the reply |
| **agent** | green header (or red if blocked) | The agent's final response, latency in the header |
| **system** | yellow header | Send/receive errors and timeouts |

## Live "thinking" (TASK_PROGRESS streaming)

The `TUIPromptChannel` honours an optional `on_progress` callback at
`send()` time, and the prompt pane wires it to render `progress` lines
in the transcript as TASK_PROGRESS events arrive.  Operators see
forward motion *while* the agent works, not just the final reply.

Example transcript with streaming:

```
14:32:01  operator → coding_agent          task=ab12cd34
  Generate a unit test for FizzBuzz

  → step 1/3 — Reading specs                ↑ 45%
  → step 2/3 — Drafting tests               ↑ 67%
  ✓ skill:echo
  → step 3/3 — Refining                     → 82%

14:32:08  coding_agent-x   task=ab12cd34 latency=147ms
  def test_fizzbuzz_basic():
      assert fizzbuzz(15) == "FizzBuzz"
```

Pipeline:

1. The agent publishes `TASK_PROGRESS` on `acc.{cid}.task.progress`
   carrying the `task_id` from the originating TASK_ASSIGN plus a
   nested `progress` struct (see `acc/progress.py:ProgressContext`).
2. `NATSObserver._route_task_progress` updates the per-agent snapshot
   AND fans the event out to every per-`task_id` listener registered
   via `register_task_progress_listener`.
3. `TUIPromptChannel.send(..., on_progress=cb)` registers the callback
   BEFORE the publish call (so a fast first event isn't missed).
4. The screen's callback appends a `progress` history entry; the
   reactive watcher re-renders + auto-scrolls to the bottom.
5. When TASK_COMPLETE eventually arrives, the observer auto-cleans
   the progress listener — channels don't have to remember.

`supports_streaming()` reports `True` for `TUIPromptChannel`.  Channels
that do not honour streaming (e.g. `SlackPromptChannel` today) ignore
the `on_progress` kwarg silently and return `False`.  Callers gate
their UX on the capability, never on the concrete class.

> **Agent-side note**: as of this PR, only the **receive** side is
> wired.  `CognitiveCore` does not yet emit `TASK_PROGRESS` during
> `process_task` — that's a separate follow-up.  Until it lands, the
> only way to exercise the surface end-to-end on a live stack is to
> inject synthetic events via:
>
>     acc-cli nats pub acc.sol-01.task.progress '{
>       "signal_type": "TASK_PROGRESS",
>       "task_id": "<id from your prompt>",
>       "agent_id": "coding-1",
>       "progress": {"current_step": 1, "total_steps_estimated": 3,
>                    "step_label": "Drafting", "confidence": 0.6,
>                    "confidence_trend": "RISING"}
>     }'
>
> The prompt pane will render the line correctly.

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

* Agent-side `TASK_PROGRESS` emission during `CognitiveCore.process_task`
  — the receive pipe ships in this PR (see "Live thinking" above);
  the matching emitter follows in a separate PR so the agent surfaces
  step boundaries without operators having to inject synthetic events.
* Slack / Telegram / WhatsApp — those are separate channels
  (each a small follow-up PR) constructing the same `PromptChannel`
  Protocol from a bot daemon.

## See also

* [`acc/channels/base.py`](../../channels/base.py) — Protocol surface.
* [`acc/channels/tui.py`](../../channels/tui.py) — TUI implementation.
* [`acc/agent.py:_handle_task`](../../agent.py) — `target_agent_id`
  filter + `task_id` echo on TASK_COMPLETE.
