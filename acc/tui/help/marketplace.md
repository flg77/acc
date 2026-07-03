# Marketplace — Package Discovery

Browse every `@scope/name` package advertised across the **layered
catalogs** (system → user → workspace) and stage one for install. Reach
it with **`m`** from the Ecosystem screen (it has no number-key slot —
the nav strip is full at 1..9).

## Panel
A single table: **Package · Version · Tier · Catalog · Signer**. The
tier badge (`trusted` / `tp` / `community` / `self`) and signer come
from the catalog entry, so you can see the provenance before installing.

## Actions
- **`/`** — focus the filter; type `@scope/name…` to narrow the list.
- **`r`** — re-query the catalogs.
- **`Enter`** (row highlighted) — **stage the install**: emits a
  `PROPOSE_INFUSE` marker into the oversight queue. It does **not**
  install directly — switch to **Compliance** to approve it, then the
  arbiter drives the `AccPackageInstall`. This keeps package additions
  under the same governance as everything else.

## Notes
- Discovery is read-only; nothing is fetched or executed here.
- To manage *which* catalogs are searched, see the **Catalogs** screen
  (`c` from Ecosystem).

## Keybindings
- `/` — filter · `r` — refresh · `Enter` — stage install
- `1` … `9` — switch screens (also `ctrl+p` command palette)
- `?` — this help
