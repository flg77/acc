---
type: backlog-item
area: operator-release
id: OR-01
status: done
size: S
depends: []
created: 2026-10-04
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-01 — Record the decisions

**What.** One `docs/DECISIONS.md` entry fixing A–G from [[OR-00 — Operator release and repo split]]: two repos with public `flg77/acc-operator`; the API group rename and the chosen group; one bundle with the console plugin by discovery; pin-and-fetch for the runtime trees; a public Quay organisation; GitHub Actions; 0.3.0 on `alpha`, semver-mode.

**Why first.** OR-04 and OR-09 cannot start without the group name, and OR-02 cannot without the repo names.

**Done when.** The entry is merged; the domain for the group is owned; the Quay organisation name is free.

**2026-10-04.** Recorded as D-032 in `docs/DECISIONS.md`. Group: **`acc.ic3networks.com`**.
