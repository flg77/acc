# 20261030-acc-mcp-server — proposal

## Why

The coding agents on the operator's machine — Claude Code via Vertex, Codex,
Cursor — all consume MCP servers, but ACC is only an MCP *client*. So they had no
way to tell ACC what they work on, to hand work to each other or to read what the
others learned (vault proposal 062, backlog CS-02 and CS-05). With
`20261030-external-work-board` the board has a place for their work; this change
lets them write it.

## What changes

* **`acc/mcp/server.py`** — MCP over stdio (newline-delimited JSON-RPC 2.0, protocol
  2025-06-18 / 2025-03-26 / 2024-11-05) written with `acc.a2a.jsonrpc`, so no new
  dependency. `Coordinator` holds the nine tools: `register`, `board`, `claim`,
  `progress`, `handoff`, `complete`, `remember`, `recall` and `ask`.
  * Only the executor working on an item moves it. A handed-over item can only be
    claimed by the executor it was handed to. A finished item is final.
  * Writes go to ACC's Redis (`ExternalWorkStore`, `ExternalNoteStore`) and are
    announced as `EXTERNAL_WORK` on `acc.{cid}.external.work`. A bus that is down
    only delays the board; the arbiter heartbeat replays the store.
  * `complete` also keeps the outcome as a note.
  * `ask` goes to the assistant through `TUIPromptChannel` (as `acc-cli ask`
    does). One receive per question lives for 30 min; a call that stops waiting
    returns the task id, and the next call keeps waiting on the same answer.
  * The server never executes anything and never reads a file. stdout carries the
    protocol only. It refuses to start without Redis rather than keep work in
    memory.
* **`acc-cli mcp serve --executor NAME`** — the entry point each tool registers.
* **Notes** (`acc/external_work.py`) — `ExternalNoteStore`: the newest 1000 notes,
  one key each, so a note can be replaced in place. `search` matches words and tags,
  newest first among equals. The collective's document store is not used: it needs
  the embedding model inside the agent processes, and this server runs on the host.
  Sessions are recorded for a week (`touch_session` / `sessions`).
* **`acc/session_ingest.py` + `acc-cli ingest session`** (CS-05) — run by each
  tool's session-end hook.
  * It reads a transcript (Claude Code or Codex JSONL, or text) or the hook event
    itself (Codex `notify`).
  * It writes one note per session, replaced on re-runs: a digest, or a summary by
    the `models.yaml` entry named with `--model`, through the CLI's recorded LLM
    construction site. The note adds the branch and `git diff --stat`; the
    transcript is never stored.
  * It closes the items the session left RUNNING: those carrying the session's id,
    or with the session's executor, repo and branch.
  * It exits 0 unless `--strict`, so it never breaks the tool.
* **Docs** — `docs/howto-coding-agents.md` (registration, rules, hooks for the
  three tools); `docs/acc-cli.md` lists both commands.

## What does not change

* No agent, role or prompt changes; the assistant does not read these notes yet
  (CS-11).
* No change to ACC's own MCP client or to any governance gate.
