# HOWTO — drop an asago policy into ACC

*`20260908-asago-alignment` AS-01. Stamped for the tree that carries
`acc-cli compliance` (PR #373).*

[asago](https://asago.ai) turns an AI governance policy into risks, the risks
into test scenarios, the scenarios into red-team artifacts, and the results
into recommended controls — keyed end to end on the ids of IBM's
[AI Atlas Nexus](https://github.com/IBM/risk-atlas-nexus). ACC is the control
plane those recommendations land on: the constitutional / setpoint / learned
rule layers, the OWASP guardrail engine, the per-principal ceiling, the
oversight queue, the compliance loop that maps rules to framework controls.
This page is the join between the two, as it works today, and where it stops.

The one thing to hold on to: **a policy enters ACC as risks with Nexus ids,
and ACC answers each risk with the controls and threats that speak the same
id, and with whether a loaded rule covers them.** Nothing is applied
automatically. Everything that would change ACC's behaviour goes through a
proposal a person approves (see §6).

---

## 1. What you need

| | Where | Notes |
|---|---|---|
| ACC ≥ the tree with `acc-cli compliance` | any host with `acc-cli` | `acc-cli compliance coverage` must print the vocabulary line |
| `asago-policy-mapper` | `git clone https://github.com/asago-ai/asago-policy-mapper && cd asago-policy-mapper && uv sync --locked` | Python 3.11+, `uv`; the command is `asago-policy-mapper` |
| a clone of the Nexus | `git clone https://github.com/IBM/risk-atlas-nexus` | the mapper reads it via `--nexus-base-dir` |
| an OpenAI-compatible LLM endpoint | the same MaaS / vLLM endpoint ACC uses | the mapper's grounding calls need `--max-tokens` (default 8192) on vLLM |
| an embedding endpoint or a local model | optional; local `all-mpnet-base-v2` works | the README's "local baseline" |
| the policy | PDF, DOCX or HTML | a corporate AI policy, an acceptable-use standard, an internal control catalogue |

asago is five weeks old at the time of writing; pin the commit you used and
expect flag names to move.

## 2. Extract the risks (asago)

```bash
uv run asago-policy-mapper extract policy.pdf -o output/ \
  --nexus-base-dir /path/to/risk-atlas-nexus \
  --base-url https://<your-llm>/v1 --model <model> --api-key "$LLM_API_KEY" \
  --max-tokens 8192
```

The run writes `output/risk-extraction.json` (and an `.html` report; `--output-format yaml`
for YAML). Every risk carries a Nexus id, a confidence, the passages it was
grounded on, and cross-mappings:

```json
{"risk_extraction": {"risks": [
  {"nexus_id": "atlas-hallucination", "confidence": 0.92,
   "evidence": [{"exact": "The AI system must not generate content that could be construed as medical advice",
                 "document": "policy.pdf", "page": 12}],
   "cross_mappings": ["nist-ms-2.5 (Confabulation)", "owasp-llm09-2025 (Misinformation)"]},
  {"nexus_id": "llm01-prompt-injection", "confidence": 0.85,
   "evidence": [{"exact": "Instructions found in retrieved documents must never be executed", "page": 7}]}
]}}
```

Review the low-confidence rows before going on — the confidence exists so a
person can drop what the mapper guessed.

## 3. Drop it into ACC

```bash
acc-cli compliance risks output/risk-extraction.json
```

For every risk the policy names, ACC prints the controls and threat model
entries that carry the same Nexus id (or, failing that, one of the risk's
cross-mapped ids), and whether the deterministic gap analysis finds a loaded
governance rule covering each:

```
4 risk(s) in output/risk-extraction.json

atlas-hallucination conf=0.92
    evidence: 'The AI system must not generate content that could be construed as medical advice'
    -> nothing in ACC answers to this risk

llm01-prompt-injection conf=0.85
    evidence: 'Instructions found in retrieved documents must never be executed'
    -> atlas_threat_model   <threat id>   Indirect prompt injection via fetched content   covered
    -> atlas_threat_model   <threat id>   Direct prompt injection / jailbreak of a governed role   covered
    -> atlas_threat_model   <threat id>   Replayed history re-presents filtered content   GAP

asi07-insecure-inter-agent-communication conf=0.71
    -> atlas_threat_model   <threat id>   Injected instruction propagating between agents   covered

3/4 risk(s) answered by ACC; 1 unanswered
```

Read it in three parts:

* **answered + covered** — the policy's concern is a modelled threat or a
  catalogued control, and a governance rule already speaks to it. Nothing to
  do but record it (the `--json` form is the record).
* **answered + GAP** — ACC knows the risk but no loaded rule covers the
  control. This is the input for §6.
* **nothing in ACC answers to this risk** — the id is real (`known`) but no
  framework or threat carries it. Either ACC does not model it (an honest
  gap to file), or the catalog is thin there: ACC's NIST AI RMF catalog is a
  *representative* subset, so `nist-ms-2.5` (MEASURE 2.5) is not in it until
  someone adds the control — §5.

Exit codes: `0` every risk answered, `2` at least one unanswered, `1` the
file is not an asago extraction. `--json` gives the same rows as data
(`nexus_id`, `known`, `taxonomy`, `confidence`, `evidence`, `via_cross_mapping`,
`controls[]` with `covered` true / false / null); `--no-gaps` skips the gap
analysis and leaves `covered` null.

One risk at a time:

```bash
acc-cli compliance nexus llm01-prompt-injection
```

## 4. Hand ACC's vocabulary back to asago

The scenario generator cross-walks taxonomies with SSSOM. ACC's own
mappings — every control and threat to its Nexus ids — export in that format:

```bash
acc-cli compliance mappings -o acc.sssom.tsv
```

`skos:exactMatch` rows are controls that *are* the Nexus entry (the NIST
subcategories); `skos:relatedMatch` rows are threats that instantiate a
Nexus risk. Whether the generator accepts a second SSSOM file beside the
Nexus one is an asago question; the file is the interchange format either
way, and it is what an auditor can diff.

Then generate scenarios against ACC as the system under test:

```bash
uv run asago-scenario-generator generate \
  --use-case @use-case.txt --risk-extraction output/risk-extraction.json \
  --sssom mappings.sssom.tsv --output-dir output/acc
```

`use-case.txt` is free text today. Until AS-03 lands (the AgentBOM exported
as asago's use case), write it from the collective: which roles run, which
skills and MCP servers they may call, what the operator's ceiling is, which
surfaces admit requesters and at which tier. The closer the text is to
`collective.yaml` and the role definitions, the closer the scenarios are to
ACC's real attack surface.

## 5. When a risk is unanswered

Add the control (or the threat) and its Nexus id, then re-run.

A framework control — for example MEASURE 2.5, which asago cross-maps
`atlas-hallucination` to:

```yaml
# regulatory_layer/frameworks/nist_ai_rmf.yaml  (or an imported catalog)
  - control_id: MEASURE-2.5
    nexus_ids: [nist-ms-2.5]
    category: MEASURE
    title: "AI system evaluated for validity and reliability"
    description: >
      The AI system to be deployed is demonstrated to be valid and reliable.
      Limitations of the generalizability beyond the conditions under which
      the technology was developed are documented.
```

The id must exist in `regulatory_layer/nexus/vocabulary.yaml` (pinned ids,
or the `nist-<gv|mp|ms|mg>-<n.m>` pattern); `acc-cli compliance coverage`
reports any id the vocabulary does not know, and the test suite fails on it.
A threat goes into the threat model the same way, with the risks it
instantiates (the threat model's own contributing guide says how).

Refreshing the vocabulary itself: re-fetch the taxonomy files under
`src/ai_atlas_nexus/data/knowledge_graph/` of the Nexus repository, update
the lists, bump `fetched`.

## 6. From a GAP to a rule — through a person

A GAP is a control ACC catalogues but no loaded rule covers. The route from
there to behaviour is the compliance loop that already exists:

1. `python -m acc.compliance_scan` writes a gap report per framework
   (JSON + markdown, each control with its `nexus_ids`) and emits **rule
   proposals** into the Compliance pane's proposals table.
2. A person reviews them there. Proposals are **Cat-B or Cat-C only** —
   never Cat-A, and never a wider per-principal ceiling (D-014: an admission
   may only narrow). The `learned_rule_promotion` setpoint decides whether an
   accepted proposal is applied or held.
3. The compliance officer role's `COMPLIANCE_GAP_SCAN` task does the same
   with the LLM's semantic reading, and its `LEARNED_RULE_PROPOSE` turns
   clustered violations into proposals — the place an asago recommended
   control will be handed in once the recommender is published (AS-06).

Nothing on this path writes a rule on the strength of a policy document
alone. asago's "human oversight at each stage" and ACC's are the same rule.

## 7. Where this stops today

* asago's cross-mapping ids are its own category-level forms
  (`owasp-llm09-2025`), not Nexus ids; only the `nist-<fn>-<n.m>` form is
  recognised as a fallback. The primary `nexus_id` is the join.
* The recommender and the deployment stage of asago are not published; AS-06
  waits for them.
* EvalHub runs model endpoints, not agents; agent-level evidence comes from
  midojo / garak artifacts (AS-04, AS-05).
* The threat model and its ids are not on the public mirror; on a mirror
  checkout `compliance risks` answers with framework controls only.

Backlog and sequence: the asago analysis in the operator's vault
(`20-backlog/asago/AS-00`), the openspec change `20260908-asago-alignment`.

## 8. The governance pack, pinned in the AgentBOM

An assessment is only worth what you can point at later. The AgentBOM already
pins every capability — `@scope/name@version`, resolved in a signed catalog,
under a signing floor — and since AS-09 it pins the **governance** the agentset
was assessed against the same way:

```yaml
spec:
  packages:
    - "@acc/workspace-roles@1.2.0"
  governance:
    - "@acc/governance-acme-2026@1.0.0"   # risks + scenarios + control map
  policy: "enterprise-contract/default"    # the EC policy applied at INSTALL
```

Rules, and the reason for each:

* **Exact pins only.** A range would let the evidence change under the document
  that cites it.
* **It must resolve.** `verify()` reports `unresolved_governance` and fails the
  verdict — evidence the catalog cannot offer is evidence nobody can check.
* **It is signed like everything else**, through the BOM's `required_signer`.
* **`policy` is not a pack.** It names the Enterprise Contract policy applied at
  install (`acc/pkg/ec_policy.py`, `--ec-policy`, or
  `/etc/acc/policy/enterprise-contract.yaml`). A governance pack belongs in
  `governance`; if `policy` is written as a pack pin it must appear there too, so
  the two cannot drift apart.

What this does **not** yet do: nothing produces a governance pack yet (AS-02), so
the pin is filled by hand; and the eval artefacts do not carry it, because the
run that would write them is AS-05. The BOM says what the agentset was assessed
*against*, not yet what the assessment *found* (AS-03's `risk_profile`).
