# Configuration — Genome Config

Tune the collective's runtime knobs: the **LLM endpoints**, the live
per-agent backend health, the **role → model** map, and the installed
Skills / MCP servers. Edits here write to `.env` / `collective.yaml`
through the bus — the TUI never mutates a running agent directly.

## Panels

### LLM ENDPOINTS (editable)
- **Config summary** — the current `ACCConfig` (read-only mirror).
- **Backend / Model / Base URL / Timeout** — edit and **Save** to
  upsert `.env` and broadcast a `config.reload` so new tasks pick up
  the change. **Test connection** pings the endpoint (stdlib `urllib`,
  no keys logged) and shows the HTTP status + round-trip time.
- **Live backends** — per-agent backend + health from the snapshot.
- **Model registry** — every endpoint declared in `models.yaml`.
- **Role → Model** — per-role model binding; persists to
  `collective.yaml`.

### SKILLS / MCPs
The capability inventory available to roles (moved here from Ecosystem
in proposal 003). Read-only browse of installed skills + MCP servers
with their trust/signer columns.

### TRACING (read-only)
Where the turns of this deployment go and what they carry: the backend
(`otel` exports, `log` keeps spans in the agents' logs), the collector the
agents send to, the MLflow workspace + experiment the collector fans out to,
and whether message text (prompts, answers, tool payloads) is recorded. In a
cluster this is `AgentCorpus.spec.observability`; in a checkout it is
`acc-config.yaml` + the environment. Nothing here is a switch.

## Notes
- Secrets are **never** shown or written in plaintext beyond the local
  `.env`; keys come from the environment.
- A model swap is collective-wide config, not a per-task override — use
  the Prompt screen's mode/agent selectors for one-off routing.

## Keybindings
- `1` … `9` — switch screens (also `ctrl+p` command palette)
- `?` — this help
