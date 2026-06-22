# `/new-agent` + the Agent Bill of Materials (A-BOM)

*Shipped in **v0.5.4** (proposals 039 + 040). Grounded in `acc/slash_commands.py`,
`acc/tui/widgets/slash_palette.py`, and `acc/pkg/agent_bom.py`.*

This is the ACC answer to "launch your agent": an operator describes what they want in
plain English, the Assistant concierge turns it into a **governed agentset**, and the
output is a **signed, exact-pin Agent Bill of Materials** that is reproducible and
air-gap-installable — not an opaque hosted agent.

---

## 1. The slash-command palette (proposal 039)

The Prompt pane has an interactive `/` command palette. The moment the input buffer
starts with `/`, an inline dropdown (`acc/tui/widgets/slash_palette.py`) lists the
matching commands, alphabetical; **`Tab`** completes the top match. The verb list is
defined **once** in the `COMMANDS` registry in `acc/slash_commands.py` — both the
palette and the generated `/help` read from it, so they never drift.

| Command | Category | Summary | Prod-locked |
|---|---|---|---|
| `/cancel <task\|cluster>` | control | Cancel a task or cluster | |
| `/catalog [<@scope\|filter>]` | query | Browse the role/package catalog | |
| `/clear` | control | Clear the transcript | |
| `/cluster show\|kill` | query | Inspect or kill a cluster | |
| `/goal [<text>\|clear]` | control | Set a pinned objective | |
| `/help` | general | List the available commands | |
| `/loop <30s\|5m\|2h> <prompt>` | control | Re-run a prompt on an interval | **yes** |
| `/mode <AUTO\|PLAN\|…>` | control | Set the operating mode | |
| `/model` | query | List the `models.yaml` registry | |
| `/new-agent <intent>` | control | Scaffold + launch a governed agentset (signed A-BOM) | **yes** |
| `/oversight pending\|approve\|reject` | oversight | Review the oversight queue | |
| `/role list` | query | List available roles | |
| `/skill <name> [args]` | control | Ask the active role to use a skill (governed prompt) | |
| `/skills` | query | List skills for the current target | |
| `/sleep` · `/wake` | control | Assistant dormant-watcher toggle | |
| `/status` | query | Show prompt state (role/mode/workspace) | |

**Prod/dev gate (`is_allowed`, proposal 039 PR-6 / 033 WS-F):** verbs marked
`prod_locked` are refused when the operator is in **prod** mode and allowed in **dev**.
The shipped default locks `/loop` (recurring auto-dispatch) and `/new-agent`
(deploy-class). The *policy* — which verbs are locked — is the operator's call.

`parse()` is **pure** (no I/O): it returns a `SlashIntent(kind, args)` the Prompt
screen dispatches on. Unknown verbs return a friendly `unknown` intent rather than
raising, and non-`/` input returns `not_slash` so normal LLM dispatch continues —
every pre-existing keystroke still works.

---

## 2. `/new-agent` — guided "launch your agent" (proposal 040)

```
/new-agent build me a finance research desk that reads filings and drafts memos
```

This is a **control / deploy-class** verb (`prod_locked=True`). It does **not** deploy
anything directly. `parse()` maps it to `KIND_NEW_AGENT` carrying the free-text
`intent`, and `new_agent_intent_prompt()` synthesizes a *governed onboarding request*
that is dispatched to the **`assistant`** role (the concierge). The request instructs
the Assistant to:

1. **Elicit** the missing details — the roles, the per-role model, the required
   packages, the **deploy target** (`rhoai` / `edge` / `standalone`), and the
   **data-residency** posture.
2. **Produce a signed `AgentBOM`** plus a `collective.yaml` for review.
3. **Stop for oversight approval** — nothing deploys without it.

So `/new-agent` is a front door to the same governance path everything else uses: the
Assistant proposes, the operator approves in the oversight/Compliance queue, and only
then does the agentset launch. The plain-English intent is captured verbatim into the
A-BOM's `spec.intent`.

> A bare `/new-agent` with no intent still works — the Assistant opens by asking what
> the agent should do.

---

## 3. The Agent Bill of Materials (`acc/pkg/agent_bom.py`)

The A-BOM is the artifact `/new-agent` produces. It is a **signed manifest describing a
customized agentset**: its roles + per-role model bindings, the pinned signed package
set, the governance policy, and the deploy scenarios it is *trusted on*. It is the
enterprise differentiator over a hosted "launch your agent": **every capability is an
exact `@scope/name@version` from a signed catalog**, so a launched agent ships a
reproducible, air-gap-installable, auditable bill of materials.

It is **CRD-shaped** on purpose (`apiVersion` / `kind` / `metadata` / `spec`): the
operator's future `AgentBOM` CRD is a thin wrapper over this same schema, and one file
is meant to drive `acc-pkg` resolution + `acc-deploy` across RHOAI / edge / standalone.

### Schema

```yaml
apiVersion: acc.redhat.io/v1alpha1
kind: AgentBOM
metadata:
  name: finance-research-desk
spec:
  intent: "a finance research desk that reads filings and drafts memos"
  roles:
    - name: equity_analyst
      model: ""                      # empty = corpus default; else a models.yaml id
    - name: memo_writer
      model: llama-3-70b
  packages:                          # MUST be exact pins — ranges are rejected
    - "@acc/capital-markets-roles@0.1.2"
  policy: "enterprise-contract/default"
  targets: [rhoai, standalone]       # subset of {rhoai, edge, standalone}
  residency: on-prem
  required_signer:                   # the signing floor every package must meet
    issuer: "https://token.actions.githubusercontent.com"
    subject_pattern: ".*/acc-ecosystem.*"
    key_path: ""
```

### Validation rules (enforced at load)

- **Exact pins only.** Every entry in `packages` must match
  `@scope/name@MAJOR.MINOR.PATCH(-pre/+build)`. A bill of materials is reproducible by
  definition, so ranges / floating tags are rejected (`is_pinned`).
- **Known targets only.** `targets` must be a non-empty subset of
  `{rhoai, edge, standalone}`.
- **`kind` must be `AgentBOM`** and **`metadata.name` is required**.

### Verification (pure)

`AgentBOM.verify(available=…)` combines three checks into one `AgentBOMVerdict`:

| Check | Method | Meaning |
|---|---|---|
| Resolution | `unresolved_packages(available)` | Every pinned package is offered by the catalog (`available` = the set of `@scope/name@version` the resolver reports) |
| Signing floor | `signing_floor_ok()` | The `required_signer` names a non-empty keyless identity (issuer + subject) |
| Targets | (in `verify`) | At least one trusted deploy target is declared |

Verification is **pure** — it takes the catalog facts as input (`available`), so the
module unit-tests without a live catalog or cosign. The catalog + cosign binding is a
thin adapter the caller supplies. `agent_bom_json_schema()` emits the JSON Schema that
will drive the future CRD `openAPIV3Schema` and a WebGUI form.

---

## 4. What's shipped vs. what's a follow-on

**Shipped in v0.5.4:**

- The `/` palette + prod/dev gate (proposal 039).
- `/new-agent` → governed Assistant onboarding → signed A-BOM + `collective.yaml`,
  oversight-gated (proposal 040).
- The A-BOM **schema + pure verifier library** (`acc/pkg/agent_bom.py`) + its JSON
  Schema.

**Declared follow-ons (NOT yet implemented):**

- An `acc-pkg` resolve verb that takes an A-BOM and resolves/verifies its pins against
  a live catalog.
- `acc-deploy` adapters that realise an A-BOM on `rhoai` / `edge` / `standalone`.
- The operator **`AgentBOM` CRD** (the thin wrapper over this schema).
- An A-BOM **import** tool and A2A (agent-to-agent) wiring.

See [`acc-pkg.md`](./acc-pkg.md) for the package toolchain the A-BOM's pins resolve
against, and [`marketplace-design-DRAFT.md`](./marketplace-design-DRAFT.md) for how the
A-BOM underpins a community marketplace's download-and-trust path.
