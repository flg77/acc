# 20260912-governance-pack-pin — proposal

Backlog: vault `20-backlog/asago/AS-00` item **AS-09** (operator answer
2026-09-11, §7b). Follows `20260908-asago-alignment` (AS-01, the Nexus
vocabulary) and proposal 040 (the AgentBOM).

## Why

The AgentBOM is ACC's answer to "what is this agent, exactly": roles and their
models, and a **capability set of exact pins** (`@scope/name@version`) that must
resolve in a signed catalog and satisfy a signing floor (`acc/pkg/agent_bom.py`:
`is_pinned`, `unresolved_packages`, `signing_floor_ok`, `verify`). That is what
makes a launched agentset reproducible and air-gap installable.

The **governance** side has none of that rigour:

* `spec.policy` is a **free string** defaulting to `enterprise-contract/default`
  — and nothing in the tree reads it. The Enterprise Contract policy that
  actually runs comes from `--ec-policy` or
  `/etc/acc/policy/enterprise-contract.yaml` (`acc/pkg/ec_policy.py`).
* The risks, scenarios and control map an agentset was assessed against (AS-02's
  governance pack, the operator's *new pack kind*) have **no place in the BOM at
  all**. AS-00 §3 ties an instance's posture and a hub note's review date to a
  governance pack *version* — a version the BOM cannot name.

So the document that claims "every capability is an exact pin" says nothing
verifiable about the governance it was assessed under, and its one governance
field is decorative.

## What changes

### Phase 1 (this ship)

1. **`spec.governance: [@scope/name@x.y.z]`** on the AgentBOM — the governance
   packs this agentset was assessed against, under the same rules as
   `spec.packages`:
   * exact pins only, rejected at validation with their own message;
   * resolved against the catalog (`unresolved_governance`);
   * carried into the verdict, and counted against `ok` — evidence the catalog
     cannot offer is evidence nobody can check;
   * the existing `required_signer` floor already applies to everything the
     catalog serves, so a governance pack is signed like any other pack.
2. **The `spec.policy` decision.** It stays what it is — the *install-time EC
   policy reference* — and does **not** fold into `governance`: they are
   different things (a policy the checker evaluates vs. pinned evidence about an
   assessment), and folding them would misrepresent the one that actually runs.
   What changes is that they can no longer drift: a `policy` written as a pack
   pin must also appear in `spec.governance`, and the field's description says
   which is which.

### Phases 2–N (deferred)

* **Recorded beside every eval result** (the rest of AS-09): the governance pins
  belong in the evidence an eval run writes — which is AS-05's EvalHub client
  and the local garak/midojo fallback. Neither exists yet; adding a field to an
  artefact nothing writes would be decoration of the kind this proposal removes.
* AS-02: the governance pack **kind** itself (risks + scenarios + artifacts +
  control map as a signed `.accpkg`), and `instance` carrying one per posture.
* AS-07: the hub's review date tied to the pinned governance version.
* The operator's `AgentBOM` CRD wrapper gains the same field.

## Impact

* **Affected code:** `acc/pkg/agent_bom.py`, `tests/test_agent_bom.py`,
  `docs/HOWTO-asago-policy.md`, `CHANGELOG.md`.
* **New env knobs:** none.
* **Tests:** 7 added — the default, the pin rule, an unresolvable pack failing
  the verdict, a resolvable one passing, the `policy`-as-a-pin rule both ways,
  the untouched default policy ref, and the schema round-trip.
* **Backward compatibility:** `governance` defaults to `[]`, so every existing
  BOM validates unchanged and `verify()` behaves as before. The only new
  rejection is a `policy` written as a pack pin without a matching governance
  entry — which was never valid in meaning, only in syntax.

## What stays open after Phase 1

* Nothing yet *produces* a governance pack (AS-02), so the field is filled by
  hand until then.
* The BOM still has no `risk_profile` (AS-03): which scenarios were run and what
  the verdict was. The pin says what was assessed against, not what happened.
* `acc-pkg` has no `bom` command — the BOM is produced by `/new-agent` and
  verified in code, so `governance` has no CLI surface yet.
