# 20260605-acc-pkg-trust-and-assistant — proposal

## Why

Stage 0 (`20260603-acc-pkg-pilot`) shipped the `acc-pkg` CLI +
manifest + build/install/verify/catalog substrate, with cosign
signature verification as the signing floor.  It deliberately
deferred:

* **Trust-chain depth** — Stage 0's `verify` checks only the
  cosign signature.  Enterprise Contract policy (build provenance,
  eval-pass attestations, Cat-A/B/C smoke) is the missing layer.
* **Publish path** — Stage 0 hand-signs with a local cosign
  keypair via `tools/cosign-pilot-keygen.sh`.  Stage 1 swaps in
  OIDC-keyless via Fulcio + Rekor (community publishers'
  afternoon-setup path; brainstorm Q8).
* **Eval format + runner** — `.accpkg` ships an `evals/`
  directory (behavioral + safety against a curated LLM panel, per
  brainstorm Q7) but Stage 0 has no parser or runner.  Without it,
  the EC policy can't enforce "eval-pass" attestations.
* **Assistant runtime path** — `PROPOSE_INFUSE` is a marker shape
  defined in the architecture but not wired into
  `acc/assistant_proposal.py`.  Without it, the Assistant can't
  emit infuse proposals through the AoA-P2b queue.
* **Dual-source role loader** — `acc/cognitive_core.py` only
  reads from `roles/<name>/role.yaml`.  Until it also reads from
  `/var/lib/acc/packages/<scope>/<name>-<version>/roles/<name>/role.yaml`,
  installed packages don't actually serve their roles.
* **DC declarative install** — the OpenShift operator needs
  `AccPackageInstall` + `AccCatalog` CRDs to land before GitOps
  + ArgoCD can drive package state declaratively.

Stage 1 closes those gaps.  It does NOT extract any roles to a
separate repo (Stage 2), does NOT ship a public hub (Stage 2), and
does NOT introduce the bootc bundler (Stage 3).  The internal acc1
Kubernetes hub (deploy manifests landing alongside this proposal
under `gitops/acc-hub/`) remains the single catalog endpoint
through Stage 1.

## Scope

Six sub-slices, each independently shippable as its own PR.  The
sub-slices roughly mirror Stage 0's slicing discipline:

| Sub-slice | Module | What ships |
|---|---|---|
| 1.1 | `acc/pkg/evals.py` + format spec | `evals/behavior/*.yaml` + `evals/safety/*.yaml` + `evals/curated-llms.yaml` schemas; runner that re-uses `acc-bench` |
| 1.2 | `acc/pkg/verify.py` (extend) + `policy/enterprise-contract.yaml` | EC policy depth: required attestations enumerated; verify rejects when EC fails |
| 1.3 | `acc/pkg/publish.py` + Konflux pipeline template | OIDC keyless cosign sign + Fulcio + Rekor; `acc-pkg publish` subcommand |
| 1.4 | `acc/assistant_proposal.py` (extend) | `PROPOSE_INFUSE` marker family member; AoA-P2b routing |
| 1.5 | `acc/cognitive_core.py` (extend) + `acc/collective.py` (extend) | Dual-source role loader + `CollectiveSpec.required_packages:` + per-collective catalog override |
| 1.6 | `operator/api/v1alpha1/` + controllers + `gitops/argocd/` | `AccPackageInstall` + `AccCatalog` CRDs; reconciler; sample ArgoCD Application |

Sub-slice **1.4 depends on 1.5** (the marker handler invokes the
installed-role loader on approval); other sub-slices are
independent.

### Out of scope (defers to Stage 2)

* Family extractions (`@acc/workspace-roles`, etc.)
* Public hub at `acc-roles.dev`
* `flg77/acc-ecosystem` repo split
* Marketplace TUI/WebGUI panes
* `acc-podman-desktop` plugin
* `docs/CONTRIBUTING-ROLE.md`

## Sub-slice details

### 1.1 — Eval format + runner

* `evals/behavior/*.yaml` schema (Pydantic): prompt, expected
  behavior-signature rubric, max latency, max output tokens.
* `evals/safety/*.yaml` schema: adversarial prompt, expected
  refusal verdict.
* `evals/curated-llms.yaml` schema: `include_rhoai_default:` flag
  + `additional_models:` list (operator extends per brainstorm
  Q7).
* `acc/pkg/evals.py`: loader + runner; reuses `acc-bench`'s
  JSONL writer for per-package eval history.
* `acc-pkg eval <pkg>` CLI subcommand (manual smoke).
* New env knob: `ACC_RHOAI_PANEL_PATH` (where to find the
  RHOAI-shipped curated panel; absence → eval skipped with WARN).
* ~30 new tests; full sweep at 2791+30.

### 1.2 — Enterprise Contract policy depth

* `policy/enterprise-contract.yaml` ships with the installer;
  declares required attestations:
  * `build_provenance` (in-toto from Konflux Tekton Chains)
  * `eval_pass` (sha256 of the eval JSONL output + verdict)
  * `cat_abc_smoke` (Cat-A/B/C governance trace pass)
* `acc/pkg/verify.py` extended: after cosign succeeds, parse
  attestations from the sigstore bundle and check against the
  policy file (Rego or CUE — Stage 1.2 picks one, see open
  decisions).
* `EnterpriseContractRejected` exception added to the install
  failure envelope; new exit code 6.
* `--ec-policy <path>` CLI flag overrides the default policy.
* ~25 new tests.

### 1.3 — OIDC-keyless publish

* `acc/pkg/publish.py` — wraps `cosign sign-blob` with `--identity-token`
  from the current OIDC environment (GitHub Actions / GitLab CI /
  workload identity).
* `acc-pkg login` subcommand — primes the OIDC flow for
  interactive operators.
* `acc-pkg publish <pkg> --catalog <id>` — pushes to the catalog
  (https mode only; file mode is operator-side `kubectl cp` as
  in Stage 0).
* Konflux pipeline template at
  `gitops/tekton/pipelines/accpkg-build.yaml`: build →
  Tekton-Chains attestation → cosign sign → push.
* ~20 new tests + manual smoke against a test OIDC issuer.

### 1.4 — `PROPOSE_INFUSE` marker family member

* `acc/assistant_proposal.py` gains `PROPOSE_INFUSE` parsing
  (per v0.3.47 marker-form-tolerance pattern).
* Marker payload: `@scope/name@version` + optional `catalog=<id>`
  + optional `tier=<t>` + optional `signer=<identity>`.
* Routes to AoA-P2b queue → Compliance pane → on approve,
  invokes `acc-pkg install` via subprocess with the resolved
  catalog.
* New Compliance pane tab "Package proposals" — covered in
  sub-slice **deferred to Stage 1.4b**: TUI screen + WebGUI parity
  in `acc/tui/screens/compliance.py` + `acc/webgui/routes_governance.py`.
* ~25 new tests (marker parsing + dispatch + post-approve hook).

### 1.5 — Dual-source role loader + `required_packages:`

* `acc/cognitive_core.py` extended: dual-source loader checks
  installed packages first, falls back to in-tree.  Resolution
  path logged for audit.
* `acc/collective.py` gains `CollectiveSpec.required_packages:
  list[str]`; resolved at collective boot before agent spawn.
* `<workspace>/.acc/catalogs.yaml` loaded if present (per-collective
  catalog override).
* `acc-deploy.sh` extension: parse `required_packages:` + invoke
  `acc-pkg install` for each before agent boot.
* ~30 new tests.

### 1.6 — DC declarative install (operator + GitOps)

* `operator/api/v1alpha1/accpackageinstall_types.go` — CR with
  `.spec.{name, version, catalog}`.
* `operator/api/v1alpha1/acccatalog_types.go` — CR with the
  per-cluster catalog list (renders to per-pod ConfigMap).
* Controllers under `operator/internal/controller/`.
* OLM bundle + RBAC updates.
* `gitops/argocd/applications/accpackage-sample.yaml`.
* ~40 new tests in `tests/operator/`.

## Impact

* **Affected code (cumulative across 1.1–1.6):**
  * NEW `acc/pkg/evals.py`, `acc/pkg/publish.py`
  * MODIFY `acc/pkg/verify.py` (EC policy depth)
  * MODIFY `acc/pkg/cli.py` (subcommands: `eval`, `login`, `publish`)
  * MODIFY `acc/assistant_proposal.py`, `acc/cognitive_core.py`,
    `acc/collective.py`, `acc-deploy.sh`
  * NEW Compliance pane tab + WebGUI route
  * NEW operator CRDs + controllers + OLM bundle update
  * NEW `policy/enterprise-contract.yaml`
  * NEW `gitops/tekton/pipelines/accpkg-build.yaml`,
    `gitops/argocd/applications/accpackage-sample.yaml`
* **New env knobs:** `ACC_RHOAI_PANEL_PATH`, `ACC_EC_POLICY_PATH`,
  `ACC_OIDC_ISSUER` (publish flow).
* **Tests:** ~170 new tests cumulative across sub-slices.
  Target full sweep: ~2961+ passing.
* **Backward compatibility:** strict — in-tree roles still load
  unchanged through the dual-source loader's fallback path.
  Existing collectives without `required_packages:` boot exactly
  as before.

## Open strategic decisions

1. **EC policy language** — Rego (Open Policy Agent) or CUE?  Rego
   has the larger ecosystem + RHTAP precedent; CUE has tighter
   schema validation.  **Recommendation: Rego** (matches Enterprise
   Contract for RHTAP's choice per the
   `<vault>\ACC Openspec\ACC Role Ecosystem\Ecosystem split — brainstorm.md`
   Q4 references).
2. **`PROPOSE_INFUSE` autonomy in AUTO mode** — per SIP-P2 rail
   6, AUTO doesn't thaw policy; does `PROPOSE_INFUSE` execute
   directly without operator approval in AUTO?  **Recommendation:
   No.** Infuse is irreversible (filesystem state); always go
   through Compliance pane regardless of mode.
3. **OIDC issuer for publish** — public Sigstore vs. RH Trusted
   Artifact Signer self-hosted?  **Recommendation: public Sigstore
   default, TAS instance discoverable via env knob for air-gap
   operators.**  Per brainstorm Q4 shipping topology.
4. **Sub-slice ordering** — 1.1 + 1.2 + 1.3 + 1.5 are independent;
   1.4 depends on 1.5.  **Recommendation: ship 1.5 → 1.1 → 1.2 →
   1.3 → 1.4 → 1.6.**  1.5 first because every downstream slice
   benefits from installed-role loading working.

## References

* Stage 0: `openspec/changes/20260603-acc-pkg-pilot/proposal.md`
* Architecture: `openspec/changes/20260604-role-ecosystem-strategy/ecosystem-implementation.md`
* Format: `openspec/changes/20260531-acc-role-package-format/proposal.md`
* Brainstorm Q3b (catalog + signing floor), Q4 (trust chain), Q7
  (evals): `<vault>\ACC Openspec\ACC Role Ecosystem\Ecosystem split — brainstorm.md`
* Naming convention: `openspec/RENAMES.md` (functional, no
  `-role-proposal-` infix).
