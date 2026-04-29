# Nucleus — Role Infusion

The **nucleus** is the cell's DNA. This screen lets you compose a new
role definition (or modify an existing one) and publish it to the
collective as a `ROLE_UPDATE` signal. The arbiter must countersign
(Ed25519) before agents apply the change.

## Typical workflow

1. **Pick a starting point.** Two options:
   - Open the **Ecosystem** screen (`6`), select a role row, and click
     **Schedule infusion**. The form here pre-fills with that role's
     full definition. This is the recommended path.
   - Or use the **Role** dropdown at the top to pick a role; task types
     and domain fields auto-fill. Other fields stay as-typed.

2. **Edit fields.** All fields are free-text/dropdown:
   - **Purpose** — one sentence; the cell's reason for existing.
   - **Persona** — `concise` / `formal` / `exploratory` / `analytical`.
   - **Task types** — comma-separated UPPER_SNAKE_CASE strings. Defines
     which TASK_ASSIGN messages this role accepts.
   - **Allowed actions** — comma-separated tool/action names. Anything
     outside this list is blocked by Cat-A.
   - **Domain ID** + **Domain receptors** — the cell's tissue type and
     which signals it listens for (PARACRINE filtering).
   - **Seed context** — domain-specific priming injected into every LLM
     call.
   - **Cat-B overrides** — `token_budget` and `rate_limit_rpm` numeric
     overrides for this role.

3. **Apply** (Ctrl+A or button). The form posts a `ROLE_UPDATE` payload
   on `acc.{collective_id}.role_update`. The status bar shows
   `Awaiting arbiter approval…`. When an agent's heartbeat reports the
   new `version`, status flips to `✓ Role applied`.

4. **History** (Ctrl+H or button) — show the last 20 applied versions
   with timestamps and approver IDs.

## What happens after Apply

- Arbiter validates the payload against Cat-A constitutional rules.
- Arbiter signs the payload with its Ed25519 key.
- Each agent in the named collective verifies the signature, swaps in
  the new role definition at its next heartbeat boundary, and bumps
  `role_version` in subsequent heartbeats.

## Keybindings
- `Ctrl+A` — Apply
- `Ctrl+L` — Clear form
- `Ctrl+H` — Toggle history
- `1` … `6` — switch screens
- `?` — this help
