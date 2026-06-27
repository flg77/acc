# How-to — office connections (Google Workspace, OAuth-brokered)

Integration **pillar 2 (act)** — let the assistant read your office suite on your
behalf, with **per-operator** OAuth and **read-first** safety. Design:
`ACC-PR/Proposals/PR-PROPOSAL-B`. First cut: **Google read-only**.

## How auth works (you stay in control)

ACC never enters your credentials. **You** consent in Google's own screen; ACC
stores only the resulting refresh token (encrypted, keyed by your `operator_id`)
and mints short-lived access tokens on demand.

```
operator → consent in Google's browser screen → code → broker stores refresh token
agent MCP call → broker mints a fresh, short-lived bearer (per operator) → consumed Google MCP
```

## Mechanism (and why MCP, not a skill)

Office suites are dozens of tools per service → **MCP** (tool multiplexing +
per-tool `allowed_tools` sandbox). We **consume** a mature Google Workspace MCP
server (e.g. `taylorwilsdon/google_workspace_mcp` or Google's official CLI+MCP)
and govern it; we do **not** re-implement the Google APIs.

`mcps/google_workspace/mcp.yaml`:
- `auth: oauth`, `oauth_provider: google` → resolved per-request by
  `acc.credentials.CredentialBroker` (not a static key).
- `allowed_tools:` read-only (calendar/gmail/drive/sheets reads).
- `denied_tools:` `gmail_send`, `drive_delete`, `sheets_write_range`, … —
  **writes are denied in this slice** (oversight-routed in a follow-up).

## Set up

1. Deploy the consumed Google Workspace MCP server (operator's trust boundary);
   point `mcps/google_workspace/mcp.yaml:url` at it.
2. Create a Google OAuth client (desktop/PKCE) and set:
   `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` (if any),
   optionally `GOOGLE_OAUTH_SCOPES` (defaults to read-only cal/gmail/drive).
3. Token store key (edge/standalone): `ACC_CRED_KEY` (a Fernet key —
   `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`).
4. Connect (operator surface — `/connect google` slash command + a Config
   "Connections" panel — lands next; the broker engine + this manifest ship now).

## Governance

Cat-A A-018 gates each call against the role's `allowed_mcps` + risk ceiling; the
`allowed_tools`/`denied_tools` lists sandbox per tool; Cat-A `A-OFF-1`
(data-egress) bounds what office content may leave the cell; every call is
audited with `operator_id` + tool + scopes.

## Test

```bash
python -m pytest tests/test_credential_broker.py -q
```
Hermetic — the OAuth token endpoint is mocked: connect→mint→refresh lifecycle,
per-operator isolation, encrypted-at-rest store, and the MCP transport's
oauth-bearer injection.
