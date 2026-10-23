# 20261015-ask-once-p5-standing-grants — proposal

**058 Phase 5: quieter over time, never by itself.** Backlog B5 (HG-35 apply +
revoke) and EC-02 (the agent-configuration posture scan, from the ECC
evaluation of 2026-10-15) as the check before any grant.

## What changes

- **`acc/grants.py`: standing grants.** `acc.approval_patterns` keeps
  proposing only; its no-apply test is unchanged. A person gives a grant
  with `acc-cli grants accept <evidence> --by <name>`. It covers exactly the
  role and target the pattern was found on (`<role>/<kind>:<target>`), is
  revocable (`acc-cli grants revoke`), and each call it answers is an
  `AUTO_APPROVED` row with approver `policy:grant:<id>`. `grants detect`
  reads the queue's decision history; `grants reject` makes a rejection
  stick.
- **The dispatcher consults a grant** only for `system_access`, non-CRITICAL,
  non-destructive, non-escalated calls, after the ceiling and the role
  grants. `ask_stats` counts `by_grant`.
- **`acc/posture_scan.py` (EC-02):**
  - PS-01: a skill granted above the role's ceiling.
  - PS-02: an MCP granted above the role's ceiling.
  - PS-03: exec without a sandbox (LOW when required, MEDIUM on a developer
    box).
  - PS-04: an agentset package without an exact version.
  - PS-05: a shell or interpreter in the hook allowlist (HIGH).
  - PS-06: the sandbox requirement off.

  `acc-cli posture scan [--sarif FILE]` exits 1 on HIGH, and
  `acc-cli doctor --check posture` reports the deployment-wide part.
- **The check before a grant.** With a blocking (HIGH/CRITICAL) finding for
  the role, no mission card is raised, no mission answers and no grant
  answers: every call is asked as before. `grants accept` refuses too.

## First run on this checkout (2026-10-15)

Run with `ACC_SANDBOX_REQUIRED=1`: 16 findings, none blocking.

- 14 × PS-04: every collective pins its packages with `^` ranges.
- 1 × PS-02: `capacity_advisor` grants MCP `web_fetch` (MEDIUM) above its
  `max_mcp_risk_level` LOW, so every such call is refused or escalated.
- 1 × PS-03: `assistant` holds `shell_exec` and `python_exec`.

## Not in this change

- A long decision history for `grants detect`: the queue keeps the last 50
  decided rows, which a busy site may outrun before the 7-day window. Reading
  the audit log is the follow-up.
- Fixing the PS-04 and PS-02 findings themselves (operator calls).
