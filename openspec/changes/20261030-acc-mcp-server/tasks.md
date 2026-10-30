# 20261030-acc-mcp-server — tasks

Backlog CS-02 and CS-05 (vault `20-backlog/chief-of-staff`); stacked on `20261030-external-work-board`.

## 1. Server
- [x] `acc/mcp/server.py`: `Coordinator` (9 tools, ownership rules, notes on complete), `McpServer` (initialize / ping / tools/list / tools/call, notifications, errors), stdio loop
- [x] `ask` over `TUIPromptChannel`, one receive per question, resumable by task id
- [x] `acc-cli mcp serve`; refuses without Redis; stdout = protocol only

## 2. Notes and sessions
- [x] `ExternalNoteStore` (bounded, replace in place, word + tag search)
- [x] `ExternalWorkStore.touch_session` / `sessions` (7-day TTL)

## 3. Ingest (CS-05)
- [x] `acc/session_ingest.py`: Claude Code / Codex / text transcripts, Codex notify events, digest, model summary via `_build_llm_only`
- [x] one note per session, items closed by session id or executor + repo + branch
- [x] `acc-cli ingest session`; exit 0 unless `--strict`

## 4. Docs, tests, close-out
- [x] `docs/howto-coding-agents.md`; `docs/acc-cli.md`
- [x] `tests/test_acc_mcp_server.py` (21)
- [x] CLI / MCP / prompt-record / board suites green
- [ ] PR
- [ ] lighthouse: register the three tools (CS-04) and the hooks (CS-06); one claim per tool on the WebGUI board
