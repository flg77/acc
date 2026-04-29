# Ecosystem — Extracellular Matrix

This screen is the **environment outside the cell**: every role
definition the operator could express, the active LLM backends, and
(coming soon) the Skills and MCPs available for attachment.

## Panels

### ROLE LIBRARY (left)
A table of every role discovered under `roles/` (env override:
`ACC_ROLES_ROOT`). Columns:

- **Role** — directory name
- **Domain** — `domain_id` from the role's `role.yaml`
- **Persona** — concise / formal / exploratory / analytical
- **Tasks** — count of declared `task_types`

Click any row to load the full `role.yaml` content into the **Role
Detail** panel.

### ROLE DETAIL (top right)
Read-only view of the selected role's `role.yaml` after deep-merge
with `roles/_base/role.yaml`. Useful for confirming the effective
configuration before scheduling infusion.

### Schedule infusion → Nucleus
Below the detail panel. Once a role is selected, click this button to
pre-fill every field of the **Nucleus** form with the chosen role's
definition. The TUI then jumps to the Nucleus screen so you can
review and Apply.

### ACTIVE LLM BACKENDS (bottom right)
Per-agent live view of the LLM backend in use, model name, health
status, and rolling p50 latency. Source: HEARTBEAT `llm_backend`
field.

### Skills / MCPs (roadmap)
Plug-in capability modules (Skills) and Model Context Protocol tool
servers (MCPs) will surface here when Phase 4 ships. Each role
declares `default_skills`, `allowed_skills`, `default_mcps`, and
`allowed_mcps` in its `role.yaml`. The UI will mark defaults in
teal, allowed in white, forbidden in dim grey.

## Keybindings
- `Enter` — load selected role into detail panel
- `1` … `6` — switch screens
- `?` — this help
