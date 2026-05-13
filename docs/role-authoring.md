# Role authoring — surface ownership

Where to write what when authoring a role.

This doc codifies the boundary memo from
[proposal 003 §10](../../../Documents/Notes/Notes/Development/AgenticCellCorpus/ACC%20Implementation/003%20-%20ACC%20TUI%20usability%20hardening.md)
into the runtime repo so contributors don't have to chase the
Obsidian vault.  Linter coverage lives in
[`acc-cli role audit`](../acc/cli/role_cmd.py) — see the LINT
codes at the bottom of this doc.

---

## Four surfaces, one role

A role exists across four operator-facing surfaces.  Each owns a
distinct concern; mixing them is the most common authoring trap.

### `role.md` — narrative

Human-authored prose.  Operator reads this to decide whether to
pick the role for a task.

**Goes in role.md**

- What this role is for, in plain English.
- When to pick it (and when not to).
- Example prompts that work well.
- Anti-patterns: prompts that look reasonable but produce poor
  results.
- Pointer to related roles if the operator is on the wrong page.

**Never goes in role.md**

- Field definitions that already live in role.yaml (purpose,
  persona, task_types, …).  Reference them in prose, don't
  re-state values.
- API contracts.  Tools, MCPs, skill IDs.  Those are
  role.yaml's job.

The arbiter never reads role.md.  It's a hint to humans.

### `role.yaml` — machine identity + defaults

The wire-format truth.

**Goes in role.yaml** (`role_definition:`)

- `purpose` — one-line scannable summary (≤ 200 chars).  Not
  the narrative; that's role.md.
- `persona` — categorical (concise / formal / exploratory /
  analytical).
- `version` — semver-ish.
- `task_types` — what task_type values the role accepts.
- `allowed_actions` — Cat-A action allow-list.
- `seed_context` — non-secret context injected into every
  task's system prompt.
- `domain_id` + `domain_receptors` — paracrine routing.
- `parent_role` — proposal 004 hierarchy.
- `category_b_overrides` — Cat-B setpoints (token_budget,
  rate_limit_rpm, …).
- `allowed_skills` / `default_skills` / `max_skill_risk_level`.
- `allowed_mcps` / `default_mcps` / `max_mcp_risk_level`.
- `eval_rubric_hash`.

**Never goes in role.yaml**

- Per-infusion overrides (those go into the Nucleus form for
  one run; they don't write back).
- Per-task content (that's the Prompt screen's job).

### Nucleus (TUI Infuse screen) — per-infusion delta

Not a role editor.  Loads role.yaml defaults; lets the operator
override for THIS infusion; publishes a ROLE_UPDATE.

**Goes in Nucleus form**

- Same fields as role.yaml — but pre-filled from disk and
  editable for the current infusion only.
- New: `llm_endpoint` if the operator wants a specific LLM
  backend for this run.

**Concrete rule.**  If a field appears in both role.yaml and the
Nucleus form, the Nucleus value is a **one-shot override** and is
**never** written back to role.yaml.  Writeback to role.yaml is
exclusively via the operator's external editor (PR-3 file
watcher catches the change).

### Prompt screen — task content only

No purpose / persona / context fields.  Just "what do you want
this already-infused role to do right now."  The TASK_ASSIGN
payload's `task_description` field.

---

## Linter — `acc-cli role audit <name>`

Proposal 006 ships a content-drift linter that flags common
authoring traps.  Default exit 0 (warning-only); `--strict`
exits 1 on any warning.

| Code | Triggers when | Fix |
|---|---|---|
| `LINT001` | role.yaml missing or unreadable | Add / repair `roles/<name>/role.yaml`. |
| `LINT002` | role.yaml `purpose` is empty | Set a one-line purpose. |
| `LINT003` | role.yaml `purpose` > 200 chars | Trim to a one-liner; move prose to role.md. |
| `LINT004` | role.md missing but role declares task_types | Author `roles/<name>/role.md`. |
| `LINT005` | role.md H1 heading appears unrelated to role.yaml `purpose` | Reconcile — usually means the purpose was edited without updating the H1 (or vice versa). |

Heuristic checks are warnings, not hard errors.  CI may
eventually gate on `--strict` once the rule set stabilises.

---

## Examples

### Good role.md heading + role.yaml purpose

```yaml
# role.yaml
role_definition:
  purpose: "Generate, review, and test code artefacts."
```

```markdown
# Coding agent

Generates source files, runs tests, and reviews diffs.  Pick this
role when you want the agent to write code.  …
```

LINT005 passes because "Coding" shares the meaningful token
`code` with the yaml purpose.

### Bad — drift

```yaml
role_definition:
  purpose: "Synthesise research findings."
```

```markdown
# Translator
```

LINT005 fires — H1 has no overlap with the yaml purpose.  Either
the role was repurposed and the H1 wasn't updated, or vice versa.

---

## Workflow recommendations

1. Edit role.md + role.yaml side-by-side; the TUI's file-watcher
   (proposal 003 PR-3) picks up changes live.
2. Before committing a role change, run
   `acc-cli role audit <name>` to catch the common traps.
3. The Nucleus form is for **infusion** decisions, not authoring.
   If you find yourself wanting to "save" a Nucleus edit, edit
   role.yaml instead.

---

## References

- Proposal 003 §10 — original memo (operator's Obsidian vault).
- Proposal 006 — this slot (operator's Obsidian vault).
- `acc/cli/role_cmd.py:_cmd_audit` — the linter implementation.
- `tests/test_role_audit.py` — covers each LINT code.
