# Compliance — Genetic Immune Layer

This screen shows the cell's **immune response**: OWASP LLM Top 10
violations detected, the live compliance health score, and the human
oversight queue (EU AI Act Art. 14).

## Panels

### OWASP GRADING
Per-code pass-rate table (LLM01 prompt injection, LLM02 output
validation, LLM04 DoS, LLM06 PII, LLM08 excessive agency). Green means
no violations in the rolling window; red means one or more.

### COMPLIANCE HEALTH
Composite score: 40 % Cat-A pass rate + 40 % OWASP clean rate + 20 %
audit completeness. Score is reported per agent in HEARTBEAT and the
TUI shows the worst-agent value as the collective summary.

### OVERSIGHT QUEUE
Tasks classified as **HIGH** or **UNACCEPTABLE** risk by the EU AI
Act risk classifier are paused here and wait for human approval before
their output is acted upon.

- Each row: `ID`, `Agent`, `Risk`, `Submitted`, `Status`.
- `Enter` on a row → approve the task.
- `r` on a row → reject the task with a reason prompt.
- The arbiter receives `OVERSIGHT_DECISION` and proceeds or escalates.

A task that times out (`oversight_timeout_s`, default 300 s) is still
allowed to proceed but is recorded with `oversight_bypassed=True` in
the audit trail.

### OWASP VIOLATION LOG (last 50)
Reverse-chronological feed: timestamp, OWASP code, agent, risk level,
matched pattern.

## Detection sources

| Source | What it detects |
|--------|-----------------|
| `acc.guardrails.prompt_injection` | LLM01 — regex + embedding distance |
| `acc.guardrails.output_handler` | LLM02 — schema + tool whitelist |
| `acc.guardrails.dos_shield` | LLM04 — token / recursion |
| `acc.guardrails.pii_detector` | LLM06 — Microsoft Presidio |
| `acc.guardrails.agency_limiter` | LLM08 — `allowed_actions` whitelist |

When `ACC_OWASP_ENFORCE=false` (default for new deployments) the
guardrails run in **observe** mode — violations populate this screen
and the audit log but do not block the task.
