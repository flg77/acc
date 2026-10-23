---
topic: security
applies_to: ">=0.29.0"
status: unreleased
owner: operator
---

# What an agent may do, and what still asks

This page lists the rules that decide whether an agent's action runs, asks you
first, or is refused, and where each rule is enforced. It describes the
autonomy model the operator set on 2026-10-15/18: **the assistant lays out a
plan, you confirm it once, and from then on the work runs by itself.** Only a
single risky action asks again.

It applies to the release that ships the "ask once" work (058, 059, 060 and the
scoped MCP environment) and later. Earlier releases asked about every system
call in AUTO. See the [CHANGELOG](../../CHANGELOG.md) for the exact version.

## The order of checks on every call

Every skill or MCP call an agent makes goes through these checks in this order
(`acc/capability_dispatch.py`, `_dispatch_one`). The first check that decides
the call wins.

| # | Check | Outcome | Enforced by |
|---|---|---|---|
| 1 | **Requester ceiling.** A requester (for example a Slack user) cannot trigger anything above their tier's ceiling: viewer LOW, requester MEDIUM, operator CRITICAL. | **Refused**, never asked: no approval could make it allowed. | `acc/identity.py` |
| 2 | **Needs a human every time.** A capability that declares `requires_oversight`. | Asked; refused when there is nobody to ask. | dispatcher |
| 3 | **Destructive.** The command deletes or overwrites data (`rm`, `find -delete`, `git push --force`, `kubectl delete`, `DROP TABLE`, …) or the capability declares itself destructive. | **Asked, in every mode except Plan.** Exception: inside a mission, a workspace `rm` through `shell_exec` that runs on the agent host is first backed up to `.acc-quarantine/` and then runs; the mission's closing report lists it, and your purge is the final allowance. A sandboxed `rm` (the default) is asked: the sandbox's filesystem and working directory are not the agent's, so a backup taken by the agent could miss the file. | dispatcher (`destructive_evidence`), `acc/mission.py` |
| 4 | **Outside the role's grants.** A skill or MCP the role does not allow, or one above its risk ceiling. | Asked as "allow for this task?"; approving widens the role for that one call only. | dispatcher (A-017 / A-018) |
| 5 | **Another system (ssh).** `ssh_exec` to a host that is not local and not on the seed list. | Answered by a host grant or the approved plan's hosts (not under "Ask me each step", not for a role with a blocking posture finding); otherwise asked, with "always allow this host". Every approved host is put into the sandbox's network policy before the call; a host allowed once leaves it again afterwards. | dispatcher (`_host_gate`), `acc/hosts.py`, and the OpenShell network policy |
| 6 | **System access** (shell, exec, file writes, deploy). | Not asked when the call runs **inside the OpenShell sandbox** under Ask once / AUTO. Otherwise answered by a mission or a standing grant (no grant under "Ask me each step"); otherwise asked. Under Ask once the question is the mission card (allow the mission / just this call / don't run it / **always allow**); in other modes it is *allow once / always allow for <role> / don't run it*. | dispatcher, `acc/sandbox/` |
| 7 | **Acting in your name** (send, post, publish, mail). | **Asked every time.** No mission or grant covers it. | dispatcher (`gate_categories`) |
| 8 | **CRITICAL** risk level. | **Asked every time.** No mission or grant covers it. | dispatcher |

## Staffing: infuse, spawn, route

Under **Ask once** (the default mode), the assistant's staffing proposals are
held in **one plan card**. Staffing proposals are: install a role pack
(infuse), start a role (spawn), hand work to a role (route), and pause, resume,
scale or stop a worker.

- The card shows each step:
  - the role, and whether it is reused, installed or authored;
  - the packs and hosts the plan needs;
  - on every install step, **the installed roles that are not running**.
- **Allow the plan:** a mission is created. Every step in the plan runs, and so
  do the follow-ups in the same thread.
- **Just these steps:** the steps run once and no mission is created. An approval
  with no option chosen (the Compliance pane, `acc-cli oversight approve`) means
  the same: the narrower yes.
- A later proposal outside the plan raises one **amendment** card.
- Publishing a note across a boundary, and a role-gap finding, are always asked.
  A plan never covers them.

In the other modes:
- **Ask per system call (AUTO):** staffing runs without a card.
- **Ask before edits (ACCEPT_EDITS):** route, spawn, infuse, pause and resume run
  without asking; role updates, scale and stop are asked.
- **Ask me each step:** every proposal is asked.
- **Plan only:** nothing runs.

**Installing a pack is never asked to make it trusted.** A pack must be signed by
its catalog's required signer. An unsigned pack is refused at install, not
queued for approval, because an approval cannot make it signed.

## Missions and grants: what "once" means

| Mechanism | Given by | Covers | Ends |
|---|---|---|---|
| **Mission** (Ask once) | You, on the plan card or the first gated call | System access in the thread's workspace, the plan's steps and hosts, follow-ups in the same thread | Close, revoke, 200 calls (`ACC_MISSION_MAX_CALLS`) or 8 h (`ACC_MISSION_TTL_S`) |
| **Standing grant** | You, by "always allow" on a question or the mission card, or `acc-cli grants add` | Exactly one role and one target: `skill:<id>`, `mcp:<server.tool>` or `host:<name>[:<port>]` | `acc-cli grants revoke` |
| **Allow for this task** | You, on an escalation question | One call | That call |

A grant or mission is never given for:
- CRITICAL calls;
- calls made in your name;
- destructive calls (apart from the backed-up workspace `rm` above);
- a role whose configuration has a blocking posture finding (`acc-cli posture scan`).

Every call a mission or grant answers is recorded as an `AUTO_APPROVED` row
that names the mission or grant, and therefore names you.

## The sandbox is mandatory

- `shell_exec`, `python_exec` and `ssh_exec` run inside the agent's OpenShell
  sandbox.
- With no sandbox they **refuse to run** (`ACC_SANDBOX_REQUIRED=1`, the default).
  `ACC_SANDBOX_REQUIRED=0` exists for a developer's own box, and `acc doctor`
  reports it as drift.
- Settings:
  - `ACC_SANDBOX_NAME`: the agent's sandbox; setting it turns delegation on.
  - `OPENSHELL_GATEWAY`: required with a name.
  - `ACC_SANDBOX_ENABLED=0`: forces delegation off.
  - `ACC_OPENSHELL_BIN`: the CLI path.
- Set up the standalone/edge sandbox with [`deploy/openshell/`](../../deploy/openshell/README.md).
- Host grants are written into the sandbox's network policy:
  - `openshell policy update --add-endpoint host:port --binary /usr/bin/ssh`;
  - so a connection to an ungranted host fails inside the sandbox, even if a
    skill skipped ACC's own check;
  - `acc-cli grants sync-sandbox` re-applies them.
- **Not yet in the sandbox:** stdio MCP servers. `openshell sandbox exec` cannot
  carry their interactive stream yet. They run with a limited environment (next
  section).

## MCP servers see only what they declare

A stdio MCP server starts with:
- a non-secret base: `PATH`, `HOME`, locale, temp dirs, proxy and CA-bundle
  variables;
- plus the variables its manifest declares in `env:`.

An empty value in `env:` passes the agent's value through, read from the
environment or the mounted secret. A server never sees the agent's LLM keys,
Redis or NATS settings, or another server's token.

## Where to look

| Question | Command |
|---|---|
| What missions are open, what did they run, what did they back up? | `acc-cli mission list`, `acc-cli mission show <id>` |
| Which roles were planned and not needed, or needed and not planned? | `acc-cli mission patterns` |
| What is granted? | `acc-cli grants list` |
| Is the configuration safe to grant on? | `acc-cli posture scan`, `acc doctor --check posture` |
| Is the sandbox set up? | `acc doctor --check sandbox` |
| How many times did a task ask? | `TASK_COMPLETE.asks` in the trace and MLflow |
