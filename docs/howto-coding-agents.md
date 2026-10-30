# How to: let Claude Code, Codex and Cursor report to ACC

ACC's assistant can act as chief of staff for the coding agents on the same machine.
Each agent starts `acc-cli mcp serve` as an MCP server. Through it the agent claims,
progresses, hands over and completes work items on ACC's work board, leaves notes
for the other agents and asks ACC's assistant. A session-end hook runs
`acc-cli ingest session`, so a session that never called `complete` still leaves a
note and closes what it left running.

Design: vault proposal 062, backlog CS-02 / CS-04 / CS-05 / CS-06; OpenSpec
`20261030-acc-mcp-server`.

## What it needs

- The ACC stack running on this host. Its NATS (4222) and Redis (6379) are
  published on the host by `container/production/podman-compose.yml`.
- `acc-cli` on the `PATH` of every coding tool.
- These variables in each tool's MCP server environment (defaults in brackets):

| Variable | Default | Notes |
|---|---|---|
| `ACC_REDIS_URL` | `redis://localhost:6379` | The server refuses to start when Redis is unreachable; it never keeps work only in memory |
| `ACC_REDIS_PASSWORD` | — | When the stack sets `REDIS_PASSWORD` |
| `ACC_NATS_URL` | `nats://localhost:4222` | Used for the board signal and `ask`; a bus that is down delays the board, nothing is lost |
| `ACC_COLLECTIVE_ID` | `sol-01` | |

## Register the server

Use one `--executor` name per tool. The board shows that name on every item.

**Claude Code**

```bash
claude mcp add --scope user acc -- acc-cli mcp serve --executor claude-vertex
```

**Codex** (`~/.codex/config.toml`)

```toml
[mcp_servers.acc]
command = "acc-cli"
args = ["mcp", "serve", "--executor", "codex"]
```

**Cursor** (`~/.cursor/mcp.json`)

```json
{ "mcpServers": { "acc": { "command": "acc-cli", "args": ["mcp", "serve", "--executor", "cursor"] } } }
```

Then add one rule to each tool's user instructions (`~/.claude/CLAUDE.md`,
`~/.codex/AGENTS.md`, Cursor's user rules):

> On start call `acc.register`. Call `acc.recall` before you design. `acc.claim`
> before you work, `acc.progress` at milestones, `acc.complete` when done,
> `acc.handoff` when another agent should continue.

## The tools

| Tool | Does |
|---|---|
| `register` | Records the session; returns this repo's open items, items handed to you and recent notes |
| `board` | Lists the agents' work items (filter by repo, executor, status) |
| `claim` | Takes a queued item, or one handed to you, or starts a new one by title. It becomes RUNNING |
| `progress` | Adds one progress line |
| `handoff` | Gives the item back to QUEUED for another executor, with a brief |
| `complete` | Marks it DONE or FAILED with an outcome, PR, diffstat and the cost and tokens you report; the outcome is also kept as a note |
| `remember` / `recall` | Notes for the other agents, matched by words and tags, newest first |
| `ask` | Asks ACC's assistant. It waits up to `wait_s`; call again with the returned `task_id` to keep waiting |

Only the agent working on an item can move it. ACC cannot cancel, retry or reassign
an external item from the board.

## The session-end hooks

Check each tool's hook names against the version you run. All three tools changed
their hooks during 2025–26.

**Claude Code** (`~/.claude/settings.json`). The hook's JSON on stdin carries
`session_id`, `transcript_path` and `cwd`.

```json
{ "hooks": { "SessionEnd": [ { "hooks": [
  { "type": "command", "command": "acc-cli ingest session --executor claude-vertex --event -" } ] } ] } }
```

**Codex** (`~/.codex/config.toml`). `notify` appends the event JSON as the last
argument. It fires once per turn, and the session's note is replaced each time.

```toml
notify = ["acc-cli", "ingest", "session", "--executor", "codex", "--event"]
```

**Cursor** (`~/.cursor/hooks.json`). The `stop` event's JSON comes on stdin.

```json
{ "version": 1, "hooks": { "stop": [ { "command": "acc-cli ingest session --executor cursor --event -" } ] } }
```

By default the summary is a plain digest: the goal, the last ask and how the
session ended, plus the branch and `git diff --stat`. `--model <models.yaml id>`
has a model write it instead. **Name a local model**, because it reads the
conversation. The transcript itself is never stored. The hook always exits 0 so it
cannot break the tool; add `--strict` while you test it.

## Removing it

Remove the `acc` MCP entry and the hook from each tool. The items and notes stay in
Redis (`acc:<cid>:ext_work*`, `acc:<cid>:ext_note*`) as the record of the work.
