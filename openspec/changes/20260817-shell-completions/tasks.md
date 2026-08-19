# Tasks: Shell completions for the operator commands

**Change ID:** 20260817-shell-completions
**Branch:** `feat/shell-completions`

---

## Phase 1 — Generation

- [ ] `[1]` Evaluate `shtab` against both parsers; confirm nested subparsers work
- [ ] `[2]` `acc-cli completion <shell>` emitting to stdout
- [ ] `[3]` Same for `acc-pkg`

## Phase 2 — Dynamic values

- [ ] `[4]` Complete role names from configuration (no live deployment required)
- [ ] `[5]` Complete installed package names from the registry
- [ ] `[6]` Ensure completion never blocks on network or bus access

## Phase 3 — Delivery

- [ ] `[7]` Install instructions per shell in the docs
- [ ] `[8]` Test that a newly added subcommand appears without extra work

