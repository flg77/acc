# Proposal: Operator preflight — one command that says whether a deployment is sane

**Change ID:** 20260817-operator-preflight-doctor
**Date:** 2026-08-17
**Status:** Implemented
**Author:** flg

---

## Problem Statement

Every configuration failure ACC has in practice is **silent**. The deployment
keeps running, the TUI keeps rendering, the agents keep answering — on the wrong
model, with no key, or against a dead endpoint. Five distinct instances are
documented from live operation:

1. A `role_models` entry naming a `model_id` that is not in the `models:` registry
   does not error. `acc.models.apply_role_model_env()` logs a warning nobody reads
   and the role falls back to the global `ACC_LLM_*` default, so every agent
   appears to share one model.
2. An `api_key_env` naming a variable absent from `.env` produces no startup
   error; the backend simply sends no `Authorization` header and every call 401s
   at task time.
3. A provider outage surfaces as a downstream error that names the wrong thing —
   a gateway returning 503 for every model behind it was diagnosed twice as a
   per-model problem before the gateway root was probed.
4. Editing `models.yaml` without restarting leaves the agents on the previous
   mapping. The file is correct, the runtime is not, and nothing says so.
5. Two `role_models:` keys in one file is valid YAML; PyYAML keeps the last, so
   an operator's mapping can be silently ignored.

Each was found by hand, on a live box, after the symptom appeared somewhere else.

## Current Behavior

No single command answers "is this deployment sane, and if not, what
precisely is wrong?". Diagnosis today means reading five per-host YAML files,
inspecting container mounts and environment, and correlating per-agent logs.

Partial coverage exists but is scattered and outside the product: the
`acc-profiles` ansible role implements checks (1), (2) and (3) as preflight
tasks, and `acc-cli llm` smoke-tests a single backend.

## Desired Behavior

A single read-only command reports the health of a deployment's
configuration, with one line per check and a non-zero exit when any check fails.

    acc-cli doctor [--json] [--fix]

Checks fall into three classes, and the report must distinguish them because the
operator's next action differs:

* **Broken** — the deployment cannot work as configured (unknown `model_id`,
  missing key name, unreadable config).
* **Degraded** — configured correctly but an external dependency is unhealthy
  (endpoint unreachable, catalog unavailable).
* **Drifted** — declared state and running state disagree (config edited without
  restart; container running an older image than the checkout).

The same check implementations must be callable in-process so the TUI and web
GUI report identical results. A second implementation is how the three surfaces
start disagreeing.

## Success Criteria

- Running against a deliberately broken config reproduces each of the five
  documented failures with a message that names the offending role or key.
- Exit code is non-zero when any **broken** check fails; `--json` emits a
  machine-readable report suitable for monitoring.
- The check set is importable and used by at least one other surface, proving the
  single-implementation requirement rather than asserting it.
- A healthy deployment produces no false positives — verified on a live edge node.

## Scope

**In scope**

- Read-only checks over configuration, credentials **by name**, endpoint
  reachability, and declared-vs-running drift.
- A shared check API in `acc/` that CLI, TUI and web GUI can all call.
- `--json` output and meaningful exit codes.

**Out of scope**

- Repairing a *running collective*. `--fix`, if implemented at all, is limited
  to local configuration; mutating a governed runtime belongs on the oversight
  path, not behind a CLI flag (see open questions).
- Vulnerability or supply-chain scanning — a separate concern.
- Log analysis; that is its own change.

## Implementation options

**A. A check registry in `acc/preflight.py`.** Each check is a small object with
an id, a class (broken/degraded/drifted), a `run(context) -> Result`, and an
optional `fix()`. The CLI renders them; other surfaces import them. Straight-
forward, testable, and makes the "one implementation" requirement structural.

**B. Extend `acc-cli llm`/`acc-cli role` with more validation.** Cheaper, but
spreads checks across commands and does not give the TUI or web GUI anything.

**C. Port the ansible preflight verbatim.** Fastest to a working command, but
inherits a shell-and-YAML shape that will not serve the in-process callers.

Option A is the recommendation on the grounds that the second caller (the TUI)
is the actual requirement; A and C are compatible if the ansible checks are used
as the initial test corpus.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Does `--fix` exist at all in v1? Repairing local config is safe; anything
   touching a running collective is a governed mutation and should emit a
   proposal rather than act.
2. Should **drift** be a failure or a warning? An operator mid-edit will trip it
   routinely, but drift is also exactly the state that wastes an afternoon.
3. Where does the check context come from when run outside a container — does
   `doctor` need podman access, or does it read config only and degrade
   gracefully?

## Assumptions

- Checks may read configuration and probe endpoints, but must never read,
  print, or log a secret **value**. Presence-by-name only.
- The command runs on the deployment host, over SSH, and inside a container.
