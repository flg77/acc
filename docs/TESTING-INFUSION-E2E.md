# End-to-end role infusion — manual test procedure

How to verify, on a **test node**, that a role pack (e.g.
`@acc/capital-markets-roles`) goes from artifact → installed → extracted →
**a running role serving user requests**. Two paths:

- **Test A — Manual**: the operator adds the roles directly (build → stage
  → install → drive).
- **Test B — Assistant-driven**: the ACC assistant handles download +
  extraction + execution after one approval.

Both exercise the Slice 1 dual-source loader (packaged skills/MCPs are now
discovered under `ACC_PACKAGES_ROOT`, not just in-tree).

## Prerequisites (both tests)

- A test node with ACC installed (`pip install -e .` of the runtime).
- A built pack staged into a local catalog. From an
  `acc-ecosystem-spearhead` checkout (`acc` on PYTHONPATH):
  ```bash
  python tools/build_family_pkg.py --manifest manifests/capital-markets.yaml --version 0.1.0
  python tools/stage_pkg.py dist/acc-capital-markets-roles-0.1.0.accpkg \
      --name @acc/capital-markets-roles --version 0.1.0 --into ~/.acc/test-catalog
  # remote node: rsync -a ~/.acc/test-catalog/ testnode:~/.acc/test-catalog/
  ```
- A `self`/`file` catalog on the test node — `<workspace>/.acc/catalogs.yaml`:
  ```yaml
  catalogs:
    - id: capital-markets-test
      tier: self
      mode: file
      path: ~/.acc/test-catalog
      required_signer: { issuer: local, subject_pattern: ".*" }
      priority: 500
  ```
- A model endpoint (Ollama / vLLM / Anthropic) configured for the agent.

---

## Test A — Manual: add roles directly

**Goal:** operator installs the pack by hand and drives a role to answer.

| Step | Command | Expected (anti-check) |
|---|---|---|
| A1. Resolve | `acc-pkg list --available` | `@acc/capital-markets-roles 0.1.0` appears (NOT: empty / not-found) |
| A2. Install | `ACC_ALLOW_UNSIGNED=1 acc-pkg install @acc/capital-markets-roles@^0.1` | `installed @acc/capital-markets-roles@0.1.0 -> /var/lib/acc/packages/acc/capital-markets-roles-0.1.0` + an `AUDIT: --allow-unsigned` line |
| A3. Extraction | `ls /var/lib/acc/packages/acc/capital-markets-roles-0.1.0/` | `roles/  skills/  accpkg.yaml` present (13 role dirs, 6 skill dirs) |
| A4. Role surfaces | boot an agent with `ACC_AGENT_ROLE=equity_analyst` (or `acc-cli` role load) | audit log: `acc.role_loader: resolved equity_analyst from installed:/var/lib/acc/packages/...` (NOT: `RoleNotFound`) |
| A5. Skill auto-loads (Slice 1) | `python -c "from acc.skills.registry import SkillRegistry; r=SkillRegistry(); r.load_from(); print('compute_ratios' in r.list_skill_ids())"` | `True` (the packaged skill is discovered, NOT silently ignored) |
| A6. Serve a request | TUI **Prompt** screen (role=equity_analyst) → "Thesis on ACME: rev $1.2B +8%, net margin 12%, P/E 18, peers 22x." | structured JSON: `rating` ∈ BUY/HOLD/SELL, `confidence`, cited figures, disclaimer — produced using `compute_ratios` |
| A7. Score (optional) | `python tools/run_evals.py --suite evals/capital-markets --backend <…> --model <…>` | per-eval PASS/FAIL table + pass rate |

**A passes when:** the pack installs + extracts, the role resolves from the
installed path, the packaged skill auto-loads, and the role answers an FSI
prompt → **ready to serve.**

---

## Test B — Assistant-driven infusion

**Goal:** from a user/assistant request, the **system** downloads +
extracts on the test node + brings the role up — no manual `acc-pkg`.

Mechanism: the assistant emits a `PROPOSE_INFUSE` marker → it lands in the
**Compliance pane → Package Proposals** tab → operator approves →
`assistant_proposal._dispatch_infuse` calls `fetch_and_install_closure`
(resolve → verify → install/extract) → the dual-source loader surfaces the
role/skills → the role serves.

**Prereqs:** a running collective (NATS up) with the `assistant` role + a
Compliance surface (TUI or WebGUI); the test catalog from above visible to
the assistant. For unsigned test packs set `ACC_ALLOW_UNSIGNED=1` in the
agent env (the dispatch passes it through).

| Step | Action | Expected (anti-check) |
|---|---|---|
| B1. Ask | Prompt screen → assistant: "We need equity-research capability — add the capital-markets roles." | assistant replies with a `PROPOSE_INFUSE` for `@acc/capital-markets-roles` (NOT: it invents an answer / hallucinates the role) |
| B2. Proposal surfaces | open **Compliance → Package Proposals** | a pending row: `@acc/capital-markets-roles @ ^0.1`, proposer = assistant |
| B3. Approve | select the row → **Approve** | action stamped to your identity; status → approved |
| B4. Auto download+extract | (dispatch runs) | log: `fetch (closure): @acc/capital-markets-roles@0.1.0 from catalog capital-markets-test` → `installed … -> /var/lib/acc/packages/...`; extraction tree present |
| B5. Role comes up | `acc-tui` Dashboard / `kubectl get`/`podman ps` for the new role | the role (e.g. `equity_analyst`) registers + heartbeats ACTIVE |
| B6. Serve | route an FSI request to it (Prompt screen / a delegated task) | a grounded, disclaimed response — **ready for the user** |
| B7. Audit | Compliance pane / episode log | the infusion + approval recorded (proposer, approver, pkg, signer/allow-unsigned) |

**B passes when:** a single approval drives download → extraction →
execution → a serving role, with the whole chain audited.

---

## Negative / safety checks (run in both)

- **Unsigned without override:** drop `ACC_ALLOW_UNSIGNED` → install must
  **refuse** (`signing floor` / VerifyError). (Signed packs from a
  `trusted` catalog install without the flag.)
- **Tamper:** flip a byte in the staged `.accpkg` → install fails with
  `ContentHashMismatch`.
- **Empty registry isolation:** point `ACC_PACKAGES_ROOT` at an empty dir →
  the role does NOT resolve (confirms it was coming from the install, not
  in-tree).

## Notes

- `/var/lib/acc/packages` is the pod's writable layer by default (no PVC
  yet — Slice 5); a pod restart re-fetches via the idempotent reconciler.
- In DC/RHOAI, B4's install runs inside the pod via the operator's
  `AccPackageInstall` reconciler (`acc-cli collective pkg-install-direct`);
  the same audit lines apply. Gateway-served MCPs (Slices 2–4) replace the
  bundled-MCP path for external capabilities like cuOpt/fmp.
