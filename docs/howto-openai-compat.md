# How to point an OpenAI client at ACC

ACC's web GUI can serve the standard chat-completions API, so a client written
for OpenAI can send its work to a governed collective without changing code:
point its base URL at ACC and give it an ACC key.

This is the surface most likely to be pointed at by something you did not
write. It is **off by default**, it is not mounted at all until you configure a
key, and every key is a requester whose work stops at MEDIUM risk (D-014).

Change: `openspec/changes/20260829-openai-compat-server` (HG-24).

## 1. Make a key and register its digest

A key maps to a **principal** — the subject that is accountable for the work,
and whose memory scope the work runs in. ACC stores the SHA-256 digest of the
key, never the key, so a leaked configuration file does not leak a usable
credential.

```bash
KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
DIGEST=$(printf '%s' "$KEY" | sha256sum | cut -d' ' -f1)
```

Give `$KEY` to the client (its secret store, not a file in its repository).
Give ACC the digest and a subject:

```bash
ACC_COMPAT_API_KEYS=<digest>:reporting-bot
```

Several keys are comma-separated: `<digest1>:reporting-bot,<digest2>:alice-notebook`.
The subject appears in traces and attribution as `compat:<subject>`.

The variable goes in the web GUI's environment (`.env` for the podman stack, a
Secret on Kubernetes). Restart the web GUI: the router is mounted, or not, at
start-up.

Check it:

```bash
acc-cli doctor --check compat
```

It says whether the endpoint is on and which subjects hold keys. It never prints
a key or a digest. It fails on an entry that is not a lowercase 64-character hex
digest — such an entry can never authenticate anyone, and the usual cause is the
key itself pasted where its digest belongs. If that happened, rotate the key.

## 2. Point the client at ACC

```python
from openai import OpenAI

client = OpenAI(base_url="http://<acc-host>:8080/v1", api_key=KEY)
reply = client.chat.completions.create(
    model="analyst",
    messages=[{"role": "user", "content": "Summarise yesterday's incidents."}],
)
print(reply.choices[0].message.content)
print(reply.usage)          # None when the agent did not report usage
```

`Authorization: Bearer <key>` and `x-api-key: <key>` both work.

If the web GUI sits behind oauth2-proxy, the proxy has to pass `/v1/` through
to it (for example with `--skip-auth-route=^/v1/`): the endpoint does its own
key authentication, and a client library cannot complete a browser login.

## 3. What `model` means

`model` names an ACC **role**, not a model. The client chooses who does the
work; which model that role runs on stays the deployment's decision.
`GET /v1/models` lists the roles.

## 4. What comes back

| Status | Meaning |
|---|---|
| 200 | The completion. `usage` holds this task's token counts. |
| 202 | The work is still running after 120 s. The body carries a `task_id`; poll it (below). The usual reason is a tool call waiting for a person on the oversight queue. |
| 400 | Malformed request, or `stream: true` — streaming is not supported, and says so rather than returning something that silently is not a stream. |
| 401 | No key, or an unknown one. |
| 403 | Refused: the role is HIGH risk or above (this endpoint cannot arrange oversight for it), or the collective refused the work while doing it. Nothing is returned that could be mistaken for an answer. |
| 503 | No collective is attached, or no keys are configured. |

**`usage`** is the task's own count, summed over every model call it made —
including the follow-up turn that reads tool results. It is `null`, not zeros,
when the agent did not report usage (an agent older than 0.24, or a backend that
does not count tokens). A client that bills on it can tell "free" from
"unknown".

## 5. Polling a 202

```bash
curl -s -H "Authorization: Bearer $KEY" http://<acc-host>:8080/v1/tasks/<task_id>
```

```json
{
  "id": "…",
  "object": "chat.completion.task",
  "status": "awaiting_approval",
  "oversight_id": "ov-…",
  "waiting_on": {"summary": "shell_exec — list the build dir", "risk_level": "MEDIUM", "decide_by": 1790000000},
  "model": "analyst",
  "result": null,
  "expires_at": 1790086400
}
```

| `status` | Meaning |
|---|---|
| `running` | Still working, and no oversight row names this task. |
| `awaiting_approval` | A pending oversight row holds it. `oversight_id` and `waiting_on` say which and what; an operator decides in the TUI, the web GUI or `acc-cli oversight`. `decide_by` appears when the arbiter's heartbeat carries the row's deadline. |
| `completed` | `result` is the full chat-completion body, `usage` included. |
| `refused` | The collective refused it; `result.detail` says why. |
| `expired` | Nobody decided within an hour; the channel stopped waiting. |
| `failed` | The background wait failed; the web GUI log has the task id. |

A caller can poll only its own tasks. Another principal's task id, and an
unknown one, both answer 404 — a 403 would confirm the task exists.

`expires_at` is when the handle itself is forgotten (24 h after the 202). The
handle lives in the web GUI process: a restart loses it, and a second replica
does not see it.

## 6. Continuing a conversation

Send `X-ACC-Session: <id>` to name a thread. With it, ACC sends only the latest
user message and replays earlier turns from its own trace log; without it, the
whole `messages` array is joined into one turn. Never both — that would charge
the same turns twice. A session id names a thread, it does not open one: another
principal's thread replays empty.

## 7. What it does not do

- Streaming.
- Embeddings and the other OpenAI routes.
- HIGH-risk roles — refused until completions have a pre-flight oversight gate.
- The `user` field does not change who is accountable: it is recorded as the
  application's end user on the trace, and the key's principal stays the
  requester.
