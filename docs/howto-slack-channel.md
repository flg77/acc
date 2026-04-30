# How to run the ACC Slack channel

The Slack channel is the second concrete implementation of the
:class:`acc.channels.PromptChannel` Protocol introduced in PR-B
(after the TUI prompt pane).  It runs as a long-lived daemon
process: when an operator @-mentions the bot in a Slack channel, the
daemon dispatches the message to an ACC agent over NATS and posts
the agent's reply back to the same Slack thread.

```
Slack workspace                   acc-channel-slack daemon                  ACC bus
───────────────                   ────────────────────────                  ───────
@acc summarise PR #42  ────────►  on_app_mention  ──────────►  TASK_ASSIGN
                                   strip mention,                ▼
                                   route to default_target_role  agent processes
                                   call channel.send                ▼
                                   await channel.receive  ◄──────  TASK_COMPLETE
post reply in thread   ◄────────  format Slack message            (echoes task_id)
```

The daemon uses Slack's **Socket Mode** so it works behind firewalls
without exposing a public webhook — exactly the same pattern as the
production `slack_bolt` quickstart, just wired to publish on the
ACC NATS subject instead of a custom backend.

## 1. Prerequisites

* A running ACC stack reachable on `ACC_NATS_URL` (NATS broker,
  arbiter, at least one role-bearing agent).
* Python 3.12+ on the host where the daemon will run.
* A Slack workspace where you have permission to install custom apps.

## 2. Create + configure the Slack app

1. Go to <https://api.slack.com/apps> → **Create New App** → **From scratch**.
2. Name it (e.g. `ACC Bot`) + pick the workspace.
3. **OAuth & Permissions** → add Bot Token Scopes:
   - `app_mentions:read` — receive `@acc ...` events.
   - `chat:write`         — post replies.
   - `channels:history`   *(optional)* — read messages the bot was a
     party to (for threaded context).
4. **Socket Mode** → Enable.  Click *Generate Token & Scopes*; add
   the `connections:write` scope.  Save the **App-Level Token**
   (`xapp-...`) — this is your `SLACK_APP_TOKEN`.
5. **Event Subscriptions** → Enable Events.  Subscribe to the
   bot event:
   - `app_mention`
6. **Install App** → Install to Workspace.  Copy the **Bot User OAuth
   Token** (`xoxb-...`) — this is your `SLACK_BOT_TOKEN`.
7. Invite the bot into the channels you want to use it in:
   `/invite @ACC Bot`.

## 3. Install the daemon

```bash
pip install 'agentic-cell-corpus[slack]'
# Pulls in slack_bolt + aiohttp on top of the core deps.
# Equivalent for editable installs:
pip install -e '.[slack]'
```

## 4. Run

Pass the tokens + ACC connection details via environment variables:

```bash
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_APP_TOKEN=xapp-...
export ACC_NATS_URL=nats://acc-nats:4222
export ACC_COLLECTIVE_ID=sol-01
export ACC_DEFAULT_TARGET_ROLE=coding_agent
export ACC_SLACK_TIMEOUT_S=60       # optional, default 60s

acc-channel-slack
```

You should see in the logs:

```
slack_channel: connected nats=nats://acc-nats:4222 collective=sol-01
slack_daemon: starting Socket Mode (default_role=coding_agent timeout=60s)
```

## 5. Use it

In any Slack channel the bot is in:

```
@acc summarise the rate-limiter discussion in this thread
```

Within a few seconds (depending on the agent's LLM):

```
ACC Bot APP  •  just now
:hourglass_flowing_sand: dispatched to *coding_agent* (task `9b3a0d27`) — awaiting reply…

ACC Bot APP  •  just now
*coding_agent-deadbeef* _(latency 2143ms, task `9b3a0d27`)_
The thread proposes a token-bucket rate limiter with...
```

### Routing to a different role

Prefix the message with `role=<name>`:

```
@acc role=analyst summarise the cost analysis in #finance
```

This dispatches to the `analyst` role instead of the default
`coding_agent`.  The role must be a valid role label (matches a
directory under `roles/` on the agent side).

### Threading

The daemon ALWAYS replies in the same thread the message landed in.
If you @-mention the bot at the top level of a channel, it starts a
new thread.  If you @-mention it inside an existing thread, it
replies in that thread.  This keeps prompt + response pairs grouped.

## 6. Wire-protocol notes

The Slack channel publishes the same TASK_ASSIGN payload as the TUI
channel — agents on the bus can't tell which channel a request came
from.  The daemon stamps `from_agent="slack:bot"` so you can filter
audit records by channel via the existing `audit_broker` infrastructure.

`target_agent_id` is currently ALWAYS omitted from the Slack payload
(broadcast-by-role).  A follow-up PR can add `agent=<id>` parsing
alongside `role=<name>` for "talk to this specific agent".

## 7. Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Daemon exits with `SLACK_BOT_TOKEN and SLACK_APP_TOKEN must both be set` | Missing env vars; double-check `echo $SLACK_BOT_TOKEN` reports `xoxb-...`. |
| `slack_bolt` import error on startup | Forgot the extra: `pip install 'agentic-cell-corpus[slack]'`. |
| Bot ignores @-mentions | Bot wasn't invited into the channel: `/invite @ACC Bot`.  Or the `app_mention` event subscription is missing — recheck step 6 in §2. |
| Reply takes >60s and times out | Increase `ACC_SLACK_TIMEOUT_S`.  The agent's CognitiveCore latency is wall-clock; long LLM calls (large context, slow model) easily hit 30–90s. |
| `:x: dispatch failed: ConnectionError: nats unreachable` | The daemon can't reach `ACC_NATS_URL`.  Verify with `nats sub 'acc.>'` from the same host. |
| `:no_entry: blocked` reply with `cat_a:A-017 ...` | The agent's CognitiveCore blocked the request via Cat-A — usually because the prompt asked for a skill / MCP not in the role's whitelist.  See `docs/howto-skills.md` and `docs/howto-mcp.md`. |

## 8. Out of scope (future PRs)

* Streaming TASK_PROGRESS events into Slack as the agent works
  (currently a single final reply).  Channel will flip
  `supports_streaming()` to `True` once the protocol stabilises.
* Per-user routing (`agent=<id>` directive).
* Multiple workspaces / multi-collective fan-out.
* Slack slash commands (`/acc <prompt>`) — currently mention-only.

## 9. See also

* [`acc/channels/base.py`](../acc/channels/base.py) — the
  `PromptChannel` Protocol every channel implements.
* [`acc/channels/tui.py`](../acc/channels/tui.py) — sister
  implementation backing the TUI's prompt pane.
* [`acc/channels/slack.py`](../acc/channels/slack.py) — the code
  this guide describes.
* [`acc/tui/help/prompt.md`](../acc/tui/help/prompt.md) — TUI-side
  user guide for the same Protocol.
