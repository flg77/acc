# 20261018-grant-once-allowances — proposal

Vault proposal: `40-proposals/ACC Implementation/060 — Grant once, enforced by
the sandbox`. Builds on OpenSpec `20261015-ask-once-p3-sandbox-mandatory`
(PR #552), `20261015-ask-once-p5-standing-grants` and
`20260817-egress-brokering`.

## Why

The operator, 2026-10-18: "OpenShell sandbox is our default – in this case
shell_exec etc. are granted once by the user, same goes for hosts and 3rd
systems access. We need to have the ability to create hosts allowance lists.
OpenShell is a must have."

Today:
- **ssh hosts** come from the env `ACC_SSH_HOST_ALLOWLIST`
  (`skills/ssh_exec/adapter.py`); changing them means an env edit and a
  restart.
- **Egress policy.** `acc/egress.py` has a per-role destination policy and
  credential injection that no skill or MCP transport consults.
- **Standing grants** (058 P5, `acc/grants.py`) exist only through
  `acc-cli grants accept` after an approval pattern; they have no host
  dimension.
- **Sandbox coverage.** Only `shell_exec` and `python_exec` run in OpenShell.
  `ssh_exec` and stdio MCP servers run on the host, and stdio MCP servers
  inherit the agent's full environment (`acc/mcp/transports.py`
  `StdioTransport`).
- **No network policy.** ACC writes no OpenShell `network_policies`, although
  OpenShell enforces per-host, per-binary egress and has a
  "denied → propose → approve" loop.

## What changes

### Phase 1 (this change)
- `acc/allowances.py`: one store per collective.
  - Kinds: `capability`, `host`, `endpoint`; role or collective scope;
    granted_by; source (question, plan, CLI); optional expiry.
  - It absorbs `StandingGrant`.
  - `endpoint` entries merge into `acc/egress.Policy`, which keeps
    `egress-policy.yaml` as the seed.
- "Always allow" on system-access, host and endpoint questions. The dispatcher
  consults allowances where it consults missions: after the ceiling and
  destructive checks, before the mode gate.
- `ssh_exec` reads hosts from the store (the env is imported once as a seed).
  MCP HTTP endpoints are checked by host. An unknown host gets a question.
- `acc/sandbox/policy.py` renders allowances into OpenShell `network_policies`:
  one host per rule, only the binaries that need it. It pushes the policy to
  the gateway on change: `openshell policy set` standalone, the operator
  sandbox spec on Kubernetes.
- `ssh_exec` joins `_CONFINED_EXEC_SKILLS`. `StdioTransport` spawns through the
  sandbox with a declared-only environment. Both fail closed when the sandbox
  is required and absent.
- Surfaces: `acc-cli allowances list|add|revoke`, a WebGUI Governance page,
  TUI `/allow` and the Compliance list.

### Deferred
- Credentialed endpoints auto-approved through OpenShell's prover. In Phase 1,
  a request with credentials in scope stays a per-call question.
- Kubernetes NetworkPolicy generation from the same list.

## Impact
- **Affected code:**
  - new `acc/allowances.py` and `acc/sandbox/policy.py`;
  - `acc/grants.py`, `acc/egress.py`, `acc/capability_dispatch.py`,
    `acc/question.py`, `acc/mission.py`;
  - `skills/ssh_exec/*`, `acc/mcp/transports.py`, `acc/sandbox/runner.py`,
    `deploy/openshell/*`;
  - CLI, WebGUI and TUI surfaces.
- **New env knobs:** none. `ACC_SSH_HOST_ALLOWLIST` becomes a one-time seed.
- **Tests:**
  - the store (add, revoke, expiry, scope);
  - the grants migration;
  - the egress merge;
  - "always allow";
  - unknown host → question;
  - policy rendering;
  - fail-closed ssh and stdio MCP;
  - environment scoping.
- **Backward compatibility:** existing standing grants migrate as
  `capability` allowances. Without a sandbox, `ssh_exec` and stdio MCP refuse
  when `ACC_SANDBOX_REQUIRED` is on (the 058 default).

## What stays open after Phase 1
- The default scope of "always allow" (role vs collective), expiry, and who may
  grant: vault 060 Q1–Q3. Phase 1 uses role scope, no expiry, any
  operator-tier principal.
- Sandbox images need the binaries of stdio MCP servers; posture (061) flags a
  missing one.
