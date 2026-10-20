---
type: backlog-item
area: operator-release
id: OR-15
status: done
size: S
depends: [OR-09]
created: 2026-10-05
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-15 — Stable channel and the certified path

**Stable channel.** The first listing goes out on `candidate-v0` (OpenShift semver template) and `alpha` (operatorhub.io). `Fast` and `Stable` are one line each in `release-config.yaml` (OpenShift) and a `CHANNELS`/`DEFAULT_CHANNEL` change (operatorhub.io). Open them when the CRDs stop moving: `v1beta1` of the four APIs with conversion, and a release that has run on a lab OpenShift cluster for a month without a CRD change. Until then every release is a candidate, honestly.

**Certified operators (Red Hat Connect).** Closed with a reason: a certified listing needs a Technology Partner account, a certification project, preflight `--submit` with a Pyxis token, and the runtime images certified container by container to carry the mark for the whole deployment. None of that is a release-engineering task; it is a commercial decision that a customer's ask triggers. The path is written down in the operator's `docs/catalogs-and-certification.md` and the preflight report already ships with every release as an advisory artifact, so the first step is a day's work when the ask comes.

**Done when.** `Stable` carries a release, or this note records why not yet; the certified path stays closed until a customer asks.

---

## Progress

**2026-10-05 — done, as "why not yet"** (acc-spearhead-operator, branch `or-15-channels`). Stable does not carry a release and the note records why: the four APIs are `v1alpha1` and still moving (OR-14 added `status.roleIndex` the same day), 0.3.0 has not shipped, and no release has run on a lab OpenShift cluster for a month. What the item built instead is the mechanism that makes the promotion one honest commit: `hack/channel-gate.sh`, run by `make bundle` and `make catalog-submissions`, refuses Fast and Stable while the CSV `maturity`, the CRD storage versions, the conversion webhooks and the days since the last change to `config/crd/bases`/`api/` (`SOAK_DAYS`, 30) say no; `make channel-report` prints today's verdict (today: closed for stable — maturity alpha, four alpha storage versions, 0 days). Promotion = `maturity:` in the CSV base + the Makefile defaults `CHANNELS` / `DEFAULT_CHANNEL` / new `OCP_CHANNELS` (which now renders the OpenShift `release-config.yaml` channels), run on a clone with full history. `docs/catalogs-and-certification.md` has the tier table (Candidate → `alpha`/`candidate-v0`; Fast → `beta`/`fast-v0`; Stable → `stable`/`stable-v0`), the per-channel semver graph note, and the certified path marked **closed until a customer asks** with the reason; `RELEASING.md` §7 is the promotion step; `test/parity/test_channel_gate.py` pins the committed channels to the maturity and the gate's verdicts. The month on a lab cluster stays the maintainer's word in the release notes; the gate checks the half it can.

**Reopens when** the APIs reach `v1beta1` with conversion and a release has soaked: then `make channel-report` goes green for Fast first, Stable after the month.
