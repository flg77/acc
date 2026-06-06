# 20260605-acc-pkg-trust-and-assistant — tasks

Recommended sub-slice order: **1.5 → 1.1 → 1.2 → 1.3 → 1.4 → 1.6**.
Each sub-slice is its own PR cut.

## Sub-slice 1.5 — Dual-source role loader + required_packages (ships first)

### 1.5.1 Dual-source role loader in `acc/cognitive_core.py`
- [ ] Check `/var/lib/acc/packages/<scope>/<name>-<version>/roles/<role>/role.yaml`
      via `Registry.find(...)` first.
- [ ] Fall back to `roles/<role>/role.yaml` when not installed.
- [ ] Log the resolution path at INFO for audit (`installed:<pkg>@<ver>` vs `in-tree`).
- [ ] Test: package-installed role loads from package; in-tree fallback still works.

### 1.5.2 `CollectiveSpec.required_packages` in `acc/collective.py`
- [ ] New optional field, semver-pinned list (`"@acc/foo@1.2.0"`).
- [ ] Per-collective `<workspace>/.acc/catalogs.yaml` loaded if present.
- [ ] Validation: refuse cycle, refuse impossible constraint, refuse missing catalog.

### 1.5.3 `acc-deploy.sh` boots required_packages
- [ ] Parse `required_packages:` from collective.yaml before agent boot.
- [ ] Invoke `acc-pkg install` for each (idempotent — already installed = no-op).
- [ ] Refuse boot if any install fails (deterministic exit code propagation).

### Verification
- [ ] `pytest tests/pkg/ tests/test_cognitive_core* --no-cov -q`
- [ ] Full sweep stays green
- [ ] Manual: collective with `required_packages:` boots; role from package wins over in-tree.

## Sub-slice 1.1 — Eval format + runner

### 1.1.1 Schemas
- [ ] `acc/pkg/evals.py` — Pydantic models for `BehaviorEval`,
      `SafetyEval`, `CuratedLLMs`.
- [ ] JSON Schema export under `acc/pkg/schema/`.

### 1.1.2 Runner
- [ ] Reuse `acc-bench` JSONL writer; one entry per (eval, model)
      tuple.
- [ ] Verdict: pass / fail / skipped (model unavailable).
- [ ] Support `include_rhoai_default: true` — load panel from
      `ACC_RHOAI_PANEL_PATH` if set; warn if absent.

### 1.1.3 CLI subcommand
- [ ] `acc-pkg eval <pkg>` — runs against the operator's configured
      model panel; emits JSONL.

### Verification
- [ ] ~30 new tests; full sweep stays green.

## Sub-slice 1.2 — Enterprise Contract policy depth

### 1.2.1 Default policy
- [ ] `policy/enterprise-contract.yaml` — Rego module declaring
      required attestation kinds.
- [ ] Ships installed at `/etc/acc/policy/enterprise-contract.yaml`;
      operator overrides via `--ec-policy <path>`.

### 1.2.2 `acc/pkg/verify.py` extension
- [ ] After cosign verify succeeds, parse attestations from the
      sigstore bundle (`cosign verify-attestation --type slsaprovenance`).
- [ ] Evaluate policy via OPA (subprocess) or eval bundled-Rego.
- [ ] New exception `EnterpriseContractRejected`.

### 1.2.3 CLI flag
- [ ] `--ec-policy <path>` for both `verify` + `install`.
- [ ] New exit code 6 = EC failure.

### Verification
- [ ] ~25 new tests; cover missing-attestation refusal + policy override.

## Sub-slice 1.3 — OIDC-keyless publish

### 1.3.1 `acc/pkg/publish.py`
- [ ] Wrap `cosign sign-blob --identity-token` (auto-discovers from
      `SIGSTORE_ID_TOKEN` env or GitHub Actions environment).
- [ ] Push tarball + signature + Rekor entry to catalog.

### 1.3.2 CLI subcommands
- [ ] `acc-pkg login` — primes interactive OIDC flow.
- [ ] `acc-pkg publish <pkg> --catalog <id>` — full publish path.

### 1.3.3 Konflux pipeline template
- [ ] `gitops/tekton/pipelines/accpkg-build.yaml` — fetch → build
      → Tekton-Chains attest → cosign sign → push.

### Verification
- [ ] ~20 new tests; manual smoke against test OIDC issuer.

## Sub-slice 1.4 — PROPOSE_INFUSE marker family

### 1.4.1 Marker parsing
- [ ] `acc/assistant_proposal.py` — gain `PROPOSE_INFUSE` member
      (per v0.3.47 form-tolerance pattern).
- [ ] Payload schema: `@scope/name@version` + optional `catalog=`,
      `tier=`, `signer=`.

### 1.4.2 Routing
- [ ] Route to AoA-P2b queue → Compliance pane "Package
      proposals" tab.
- [ ] On approve, subprocess `acc-pkg install` with the resolved
      catalog.

### 1.4.3 Compliance pane tab
- [ ] `acc/tui/screens/compliance.py` — new tab.
- [ ] `acc/webgui/routes_governance.py` — WebGUI parity.

### Verification
- [ ] ~25 new tests.

## Sub-slice 1.6 — DC declarative install (operator + GitOps)

### 1.6.1 CRDs
- [ ] `operator/api/v1alpha1/accpackageinstall_types.go`
- [ ] `operator/api/v1alpha1/acccatalog_types.go`

### 1.6.2 Controllers
- [ ] `operator/internal/controller/accpackageinstall_controller.go`
- [ ] `operator/internal/controller/acccatalog_controller.go`

### 1.6.3 RBAC + OLM
- [ ] RBAC for new CRs.
- [ ] OLM CSV + CRD manifests under `operator/bundle/manifests/`.

### 1.6.4 GitOps sample
- [ ] `gitops/argocd/applications/accpackage-sample.yaml`.

### Verification
- [ ] ~40 new tests in `tests/operator/`.
- [ ] OLM bundle validates (`operator-sdk bundle validate`).

## Open strategic decisions (block sub-slice starts)

- [ ] EC policy language — Rego (recommended) vs CUE
- [ ] `PROPOSE_INFUSE` in AUTO mode — defer to Compliance pane (recommended) vs autonomous
- [ ] OIDC issuer for publish — public Sigstore (recommended) + TAS env knob
- [ ] Final sub-slice ordering — 1.5 → 1.1 → 1.2 → 1.3 → 1.4 → 1.6 (recommended)
