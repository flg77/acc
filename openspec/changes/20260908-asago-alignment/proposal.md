# 20260908-asago-alignment — proposal

## Why

asago (Red Hat-led, announced 2026-08-04; `github.com/asago-ai`) is the
policy-to-controls loop OpenShift AI is leaning into: policy documents →
risks keyed on IBM's **AI Atlas Nexus** ids → scenarios (Gherkin, STPA) →
red-team artifacts (garak; *midojo* for agents) → evaluation on **EvalHub**
(the TrustyAI operator's service, a default RHOAI component) → recommended
controls → deployable configuration. ACC is already the *back half* of
that loop — Cat-A/B/C rules, the OWASP guardrail engine, D-014 ceilings,
the oversight queue, the signed AgentBOM, a compliance loop that maps rules
to framework controls and proposes rules into a human queue — and lacks
the *front half*: policy ingestion, scenario and artifact generation,
standard evaluation orchestration. The first thing in the way is
vocabulary: ACC's frameworks name controls in their own terms
(`GOVERN-1.1`, `ART-14`, the threat model's own ids) and nothing outside ACC can read
them. The vault analysis (`20-backlog/asago/AS-00`) set the order:
vocabulary → midojo against a cell → the AgentBOM as asago's agent
description → EvalHub in the promote gate → governance pack → recommended
controls as proposals → the enterprise collective → upstream.

## What

**AS-01 (this change, Phase 1).** ACC speaks the Nexus vocabulary.

* `regulatory_layer/nexus/vocabulary.yaml` — a pinned, dated snapshot of
  the Nexus ids ACC references (IBM Risk Atlas `atlas-*`, OWASP LLM 2025
  `llm*`, OWASP Agentic 2026 `asi*`, OWASP Agentic Skills `ast*`, NIST GenAI
  risks `nist-*`) plus the derived NIST subcategory form asago's policy
  mapper emits (`nist-<gv|mp|ms|mg>-<n.m>`). Validation is offline; an
  upstream rename fails a test here.
* `FrameworkControl.nexus_ids` and `Framework.nexus_taxonomy`; the NIST AI
  RMF catalog carries its own ids (`GOVERN-1.1` ↔ `nist-gv-1.1`); the
  threat model's 25 threats carry the risks they instantiate — in the
  **withheld** YAML twin only, never on the public mirror. EU AI Act, ISO
  42001 and SOC 2 stay unmapped: the Nexus has no entities for them.
* `acc/nexus.py` — the vocabulary, the reverse index (`nexus id → ACC
  controls`), coverage, unknown-id detection, and the mappings as an
  **SSSOM** TSV (`skos:exactMatch` for a control that *is* the Nexus entry,
  `skos:relatedMatch` for a threat that instantiates a risk;
  `semapv:ManualMappingCuration`).
* `acc-cli compliance mappings [--sssom|--json] [--framework]`, `compliance
  nexus <id>` (what in ACC answers an asago risk), `compliance coverage`.
* The gap report carries `nexus_ids` per control (JSON and markdown), so a
  policy risk can be followed from the extraction to the rule that covers
  it or the gap that does not.

## Decisions

* **Nexus ids are ACC's external risk vocabulary** — on top of the threat
  model's ids and the catalogs' own ids, never instead (AS-00 §7 Q1; built on that
  assumption, the operator having said "start").
* **Hand-curated, pinned, validated.** No LLM guesses a mapping; the file
  says what each control answers to and a test holds it to the snapshot.
* **The threat half stays withheld.** The mirror never sees a threat's
  Nexus ids; the export lists them only where the twin file exists.

## Not here (later phases, AS-00 §5)

AS-04 midojo against a cell; AS-03 the AgentBOM as asago's use case and a
`risk_profile` on the BOM; AS-05 an EvalHub client in the promote gate with
an edge fallback; AS-02 the governance pack; AS-06 recommended controls as
Cat-B/C proposals through oversight (never Cat-A, never a wider ceiling);
AS-07 the enterprise collective; AS-08 upstream contributions.
