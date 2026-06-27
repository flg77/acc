# How-to — outbound messenger skills (Telegram / Slack / Mattermost / Signal)

Integration **pillar 1 (reach)** — let a role *send* a message to a chat. First
cut: the assistant holds three thin-REST skills + an MCP for Signal. See the
design in `ACC-PR/Proposals/PR-PROPOSAL-A`.

## Mechanism (and why)

| Platform | Mechanism | Token (env, per-deploy, never committed) |
|---|---|---|
| Telegram | skill `telegram_send` | `TELEGRAM_BOT_TOKEN` (from @BotFather) |
| Slack | skill `slack_post` | `SLACK_BOT_TOKEN` (scope `chat:write`) |
| Mattermost | skill `mattermost_post` | `MATTERMOST_URL` + `MATTERMOST_BOT_TOKEN` |
| Signal | MCP `signal` | run `bbernhard/signal-cli-rest-api`; set `ACC_SIGNAL_API_URL` |

Thin REST → **skills** (in-process, edge-lean, no extra service). Signal has no
bot API → it needs a `signal-cli` sidecar → **MCP** (external service).

## Enable for the assistant

Already wired in `roles/assistant/role.yaml`:
- `allowed_skills:` `telegram_send`, `slack_post`, `mattermost_post`
- `allowed_mcps:` `signal`
- `allowed_actions:` `send_message`

Set the relevant tokens in `.env`, then the assistant can send. Tokens absent →
the skill fails closed (raises) — safe by default.

## Governance

Sending is an externally-visible action. The agent **describes** it
(`[ACTION: send_message …]`); `capability_dispatch` validates the marker against
`allowed_skills`; Cat-A `A-MSG-1` requires the task be operator-invoked (or match
an approved proactive-notify rule); Cat-B caps daily sends. Every send is audited
with `agent_id` + `operator_id` + target.

## Inbound (roadmap)

Inbound (user → assistant) is a **channel** like the existing Slack daemon
(`acc-channel-slack`). Telegram/Mattermost/Signal inbound daemons mirror it
(`acc-channel-telegram`, …) and are deploy-level processes, not role grants —
landing in a follow-up.

## Test

```bash
python -m pytest tests/test_messenger_skills.py -q
```
Hermetic — the HTTP seam (`acc/integrations/messenger_http.py:post_json`) is
monkeypatched; no network, no real tokens.
