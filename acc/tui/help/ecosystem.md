# Ecosystem — Extracellular Matrix

This screen is the **environment outside the cell**: every role
definition the operator could express, the live skills + MCP servers
loaded into the registries, and the active LLM backends per agent.

Operators use it to:

* Browse the role catalogue + read each role's full `role.yaml`.
* Schedule a role infusion → Nucleus pre-fills the role form.
* See which skills + MCP servers are loaded (with risk colour cues).
* **Upload new skill / MCP manifests from inside the TUI** (PR-A2).

## Panels

### ROLE LIBRARY (left)
One row per role discovered under `roles/` (env override:
`ACC_ROLES_ROOT`).  Columns: Role, Domain, Persona, Tasks count.

* **↑ / ↓** — cursor to a row.  ROLE DETAIL on the right updates **live**
  on every cursor move (PR-A — no Enter required).
* **Enter** — pin the selection (same effect as cursor stop).
* **Schedule infusion → Nucleus** button — dispatches a
  `RolePreloadMessage` to the App, which switches to the Nucleus screen
  and pre-fills the role form with the selected role's purpose / persona
  / task_types / domain receptors / Cat-B overrides.  Stays disabled
  until a row is selected; clicking with no selection toasts a warning.

### SKILLS (left, below ROLE LIBRARY)
One row per skill manifest discovered under `skills/`.  Columns:
Skill, Version, Risk (colour-coded LOW=green / MEDIUM=yellow / HIGH=red
/ CRITICAL=bold red), Requires (action labels the calling role must
hold).

**Upload skill** button (PR-A2) — opens a file-picker modal.  Navigate
to a directory containing the skill's `skill.yaml`, select that file,
press Confirm.  The Ecosystem screen copies the entire parent directory
(`skill.yaml` + `adapter.py` + any helper scripts) into the resolved
`skills/` root and refreshes the table.

* The modal's Confirm button enables only when the selected file's name
  is exactly `skill.yaml` (case-sensitive).
* Refuses to clobber an existing directory of the same name — remove
  the old one first if you really mean to overwrite.
* Esc / Cancel dismisses without uploading.

### MCP SERVERS (left, below SKILLS)
One row per MCP server manifest under `mcps/`.  Columns: Server,
Transport (`http` / `stdio`), Risk, Tools.

**Upload MCP** button (PR-A2) — same flow as Upload skill but
targeting `mcp.yaml`.  MCP source directories typically contain only
the manifest file.

### ROLE DETAIL (top right)
Read-only view of the selected role's `role.yaml` after deep-merge
with `roles/_base/role.yaml`.  Useful for confirming the effective
configuration before scheduling infusion.

### ACTIVE LLM BACKENDS (bottom right)
Per-agent live view of the LLM backend in use, model name, health
status, and rolling p50 latency.  Source: HEARTBEAT `llm_backend`
field.

## Configuring a model endpoint (per role / all roles)

ACC reads models from a central **`models.yaml`** registry (repo root,
mounted read-only into the containers; override with `ACC_MODELS_PATH`).
Add one entry per endpoint, then assign its `model_id` to any role.

**1. Register the endpoint** in `models.yaml`:

```yaml
models:
  - model_id: my-endpoint
    backend: openai_compat     # openai_compat | vllm | ollama | anthropic | llama_stack
    model: "the-model-the-server-exposes"
    base_url: "http://host:8000/v1"   # must be reachable from the AGENT containers
    api_key_env: "MY_ENDPOINT_KEY"    # NAME of the env var holding the key (never the key itself)
    label: "My endpoint"
```

**2. Assign it** to `coding_agent`, `auto_researcher`, or any role —
either from this screen (Agentset tab → highlight agent → **Model →**
dropdown → **Set model on selected** → Save → Apply), or in
`collective.yaml`:

```yaml
agents:
  - role: coding_agent
    model: my-endpoint
  - role: auto_researcher
    model: my-endpoint
```

**3. The API key never goes in `models.yaml`.** `api_key_env` names an
env var; set the actual key in the agent containers' environment
(compose `.env` / container env):

```
LITELLM_API_KEY=sk-...            # for openai_compat (LiteLLM/OpenAI/Groq/…)
ACC_ANTHROPIC_API_KEY=sk-ant-...  # for the anthropic backend
```

> **Keyed gateway → `openai_compat`, NOT `vllm`.** A gateway needing a key
> (LiteLLM, OpenAI, Groq, …) must use `backend: openai_compat` — it sends
> `Authorization: Bearer <key>`.  `backend: vllm` sends no auth header (for an
> unauthenticated self-hosted server only); pointing it at a keyed gateway
> returns 401.  Example (LiteLLM): `backend: openai_compat`,
> `base_url: https://litellm-prod…/v1`, `api_key_env: LITELLM_API_KEY`, with
> `LITELLM_API_KEY=…` in `.env`.

**Manual (no registry)** — set directly on an agent container:
`ACC_LLM_BACKEND` + `ACC_LLM_MODEL` + `ACC_LLM_BASE_URL`
(+ `ACC_LLM_API_KEY_ENV`), or the backend-specific `ACC_ANTHROPIC_MODEL`
/ `ACC_OLLAMA_MODEL` + `ACC_OLLAMA_BASE_URL`.

Full guide: `docs/multimodel_reviewer.md` and `docs/llm-backends.md`.

## Manifest layout convention (for uploads)

| Kind  | Required files in source directory |
|-------|------------------------------------|
| Skill | `skill.yaml` + `adapter.py` (skill adapter Python class) |
| MCP   | `mcp.yaml` only |

The directory's *name* becomes the manifest id — make sure it's
`lowercase_snake_case` (the rule the Pydantic models in
`acc/skills/manifest.py` and `acc/mcp/manifest.py` enforce).

## Path resolution

Manifest roots are resolved by
`acc.tui.path_resolution.resolve_manifest_root` in this order
(post-PR-A):

1. `ACC_ROLES_ROOT` / `ACC_SKILLS_ROOT` / `ACC_MCPS_ROOT` env var if
   set AND the path exists.  A bad env value is logged + skipped,
   never silently used.
2. Repo-anchored: `<repo>/<dir>` (works in editable installs and
   container layouts).
3. CWD-relative literal as last-resort fallback.

## Keybindings

| Key | Action |
|-----|--------|
| 1–6 | Navigate to other screens |
| ↑/↓ | Move cursor in role table; ROLE DETAIL updates live |
| Enter | Pin selection (also on row click) |
| q | Quit |
| ? | This help |

## See also

* `docs/howto-skills.md` — full skill authoring guide.
* `docs/howto-mcp.md` — MCP server integration guide.
