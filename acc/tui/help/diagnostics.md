# Diagnostics — Golden-Prompt Suite (Self-Test Assays)

Compose, save, and **run golden prompts** — deterministic behavioural
checks against the live stack — and review their pass/fail + latency
history. The pane is three full-width areas stacked vertically; focus
one (Tab / click / Enter) and it grows while the others collapse to
their header + action bar.

## The three areas

### ① GOLDEN PROMPTS (list)
The prompt table — No · Title · Description · Role · Mode · Version ·
Last (last run's ▲ pass / ▼ fail + latency). **Run selected** (`r`)
runs the highlighted prompt; **Run all** (`a`) runs the suite. Prompts
load from the shipped set + any installed pack's `golden/` dir + the
writable store + attached dirs.

### ② WORKSPACE (view / edit)
Highlight a row to see its rendered definition + last result.
**View** shows it read-only; **Edit** (`e`) opens the YAML editor.
**Save** validates + writes to the writable store; **New** starts a
template; **Versions** restores a previous saved blob; **→ Eval**
promotes a prompt into a role's behavioural-eval pack.

### ③ FORM (compose + send)
Title (required to save) · Description · Target role · Target agent ·
Mode · Timeout · Prompt. The action bar: **New · Export · Save ·
Send**. **Send** hands the prompt to the Prompt screen and fires it so
the reply streams there. The attach row (`+ Add` / `Import` / `Export`
/ `→ Pack` / `⇊ DC`) manages the store: attach a watch dir, import/
export a dir · `.csv` · `.json`, export the store as a signed-able
`@scope/*` pack, or **pull DC-refined prompts** from MLflow.

## Data & MLflow
Results are per-prompt and rendered on completion; run history persists
as JSONL. When `ACC_MLFLOW_TRACKING_URI` is set, saving logs the prompt
as a tagged MLflow run artifact and `⇊ DC` pulls the newest back
(edge↔datacenter round-trip). Unset ⇒ every MLflow path is a clean
no-op.

## Keybindings
- `r` — run selected · `a` — run all · `e` — edit · `esc` — back to list
- `1` … `9` — switch screens (also `ctrl+p` command palette)
- `?` — this help
