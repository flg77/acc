# 20261018-grant-once-allowances — tasks

Lands after the 058 stack, #559 and #560 (059).

## Phase 1 — grant once, enforced by the sandbox
### 1.1 Store
- [x] **Changed from the plan:** no new `acc/allowances.py`. The 058 Phase 5 `GrantStore` already sits where the dispatcher must look (`_standing_grant`, before the mode gate) and already records every use as `AUTO_APPROVED(policy=grant:<id>)`. Its target gains the `host:<name>[:<port>]` kind; `skill:` and `mcp:` are unchanged. One list, no migration.
- [ ] `endpoint` entries merged into `acc/egress.Policy` — **deferred to Phase 2**: nothing calls the egress broker yet; HTTP MCP servers are already limited to the role's `allowed_mcps`, and their tools are granted per `mcp:` target
### 1.2 Question and dispatcher
- [x] `grant_question` (allow once / always allow for <role> / don't run it) on a system-access call that no mission or grant answers
- [x] "always allow" → `grants.give_from_answer`: a grant given by the named approver (none for an anonymous one); a host grant also goes to the sandbox policy
- [x] Never for CRITICAL, destructive, acts-on-behalf or posture-blocked calls (same rules as 058 grants)
### 1.3 Hosts
- [x] `_host_gate` for `ssh_exec`: pre-allowed (local, seed list `ACC_SSH_HOST_ALLOWLIST`) → host grant → the approved plan's hosts (059) → host question; answering it also answers the call's system-access question; a destructive remote command is still asked
- [x] `acc/hosts.py`: the dispatcher's decision reaches the adapter in a context variable set around the one call, never in the model-written arguments
### 1.4 OpenShell policy
- [x] `acc/sandbox/policy.py`: `openshell policy update <sandbox> --add-endpoint host:port --binary /usr/bin/ssh --wait` on grant, `--remove-endpoint` on revoke (incremental; static sections untouched)
- [x] `acc-cli grants sync-sandbox` re-applies every active host grant
### 1.5 ssh and MCP in the sandbox
- [x] `ssh_exec` runs through `maybe_run_sandboxed` (fail-closed when the sandbox is required and absent); `identity_file` is a path inside the sandbox
- [ ] stdio MCP servers inside the sandbox — **blocked**: non-TTY `openshell sandbox exec` reads stdin to EOF before running (OpenShell `crates/openshell-cli/src/run.rs` ~1434), so it cannot carry an interactive JSON-RPC stream. Options for the operator: run stdio servers behind an HTTP bridge inside the sandbox, or use the sandbox's ssh transport. #559 already limits their environment.
### 1.6 Surfaces
- [x] `acc-cli grants add --role --target --by`, `revoke` (removes a host from the sandbox policy), `sync-sandbox`
- [x] TUI / WebGUI / `acc ask`: the question envelope renders the new options with no surface change
- [ ] A WebGUI Governance → Grants page — Phase 2
### 1.7 Tests
- [x] `tests/test_060_grant_once.py` (12): always allow → grant → next call not asked; allow once → no grant; anonymous approver → no grant; destructive still asked; unknown host → question → host grant → next call not asked; plan host not asked; no queue → refused; adapter never trusts arguments; adapter refuses an unapproved host; adapter runs in the sandbox; policy argv; policy only with a sandbox
- [x] Two 058-era tests updated: a per-call AUTO question now carries the "always allow" options
### Verification
- [x] 058_*, 059, 060, capability dispatch, question envelope, ssh_exec, sandbox, oversight, os_basics, xplatform skills, assistant_*, handover, escalation, gate cards, principal ceiling, preflight, configstore, mcp, tests/pkg: 1278 passed + the fixed test (workstation, 2026-10-19)
- [ ] lighthouse: `ssh` to an unlisted host fails inside the sandbox; "always allow" adds it without a restart
- [ ] `acc-pr-test` evidence doc

## Phase 2 (deferred)
- [ ] Endpoint grants through the egress broker; credentialed endpoints through OpenShell's prover
- [ ] stdio MCP servers in the sandbox (HTTP bridge or ssh transport)
- [ ] WebGUI Grants page; Kubernetes NetworkPolicy from the same list
