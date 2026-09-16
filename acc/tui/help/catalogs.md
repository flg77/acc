# Catalogs — Catalog Admin

Manage the **layered catalog sources** the Marketplace searches. The
layers stack **default → system → user → workspace** (narrowest wins on an
id clash); this screen shows all of them and edits the per-collective
workspace override at `<workspace>/.acc/catalogs.yaml`.
The nav strip is full at 1..9, so this overflow pane uses a **leader chord**:
press **`Ctrl+A` then `1`** from anywhere. (`c` from the Ecosystem screen
and `ctrl+p` → "Go to Catalogs" also work.)

## Panel
A table of every catalog — **id · layer · name · roles · description ·
url · oidc issuer**.  The layer column is `bundled` (offline day-0 content),
`default` (compiled in), `system` (`/etc/acc/catalogs.yaml`, rendered by the
operator from AccCatalog resources in-cluster), `user`
(`~/.acc/catalogs.yaml`) or `workspace`.  A row redefined by a narrower layer
says "shadowed by …".  URLs in the table are shortened without their scheme;
the detail pane under the table shows the full URL and signer, and scrolls.

## Add form
Collapsed by default — press **`n`** to open it.  Three rows of fields:
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
- Only the **workspace** layer is editable here; `d` / `+` / `-` on any
  other row says which layer (and file) owns it.  System/user layers are
  managed out of band (the operator's AccCatalog, `/catalog add`).
- A bad field surfaces the first validation error in the status line;
  fix and re-submit.

## Keybindings
- `n` — new · `d` — delete · `r` — refresh · `+` / `-` — priority
- `1` … `9` — switch screens · `Ctrl+A` `0` Marketplace · `Ctrl+A` `1` Catalogs · `ctrl+p` palette
- `?` — this help
