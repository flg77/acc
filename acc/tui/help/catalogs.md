# Catalogs — Catalog Admin

Manage the **layered catalog sources** the Marketplace searches. The
layers stack **system → user → workspace**; this screen edits the
per-collective workspace override at `<workspace>/.acc/catalogs.yaml`.
Reach it with **`c`** from the Ecosystem screen.

## Panel
A table of the resolved catalogs — **ID · Tier · Mode · Endpoint ·
Priority · Signer** — sorted by priority (higher wins on a name clash).

## Add form
Three rows of fields:
- **id · tier · mode · priority** — the catalog identity.
- **url** (for `https` mode) / **path** (for `file` mode).
- **oidc issuer · subject pattern · key_path** — the `requiredSigner`
  the packages from this catalog must satisfy.

## Actions
- **`n`** — focus the New-catalog form.
- **`d`** — delete the highlighted catalog from the workspace override.
- **`r`** — reload the table.
- **`+` / `-`** — raise / lower the highlighted catalog's priority
  (clamped to 1..1000).

## Notes
- Only the **workspace** layer is editable here; system/user layers are
  shown for context but managed out of band (`~/.acc/catalogs.yaml`).
- A bad field surfaces the first validation error in the status line;
  fix and re-submit.

## Keybindings
- `n` — new · `d` — delete · `r` — refresh · `+` / `-` — priority
- `1` … `9` — switch screens (also `ctrl+p` command palette)
- `?` — this help
