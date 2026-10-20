# The ACC operator

The Kubernetes / OpenShift operator lives in its own repository since the
split (OpenSpec `20261004-operator-release-split`, decision D-032):
**https://github.com/flg77/acc-operator** (public). It installs and manages
`AgentCorpus`, `AgentCollective`, `AccCatalog` and `AccPackageInstall`, and
deploys a pinned release of this runtime by default (`ACC_RUNTIME_VERSION`
there). Operator 0.3 also embeds that release's `roles/ skills/ mcps/` and
delivers them as ConfigMaps; from the operator release after runtime 0.28 the
agent image is the source of the trees and the operator reads what it serves
(`python3 -m acc.roles_index`, decision D-033).

The operator documentation moved with it:

| Was here | Now |
|---|---|
| `docs/operator-install-local.md` | [`docs/install.md`](https://github.com/flg77/acc-operator/blob/main/docs/install.md): OperatorHub, the bundle image, your own catalog, kustomize; the Category-A ConfigMap; verify; uninstall |
| `docs/operator-agentset-guide.md` | [`docs/agentcollective-guide.md`](https://github.com/flg77/acc-operator/blob/main/docs/agentcollective-guide.md): `collective.yaml` → `AgentCollective`, roles, per-agent models via `extraEnv`, autoscaling, packages |
| `docs/operator-standalone-parity.md` | [`docs/standalone-parity.md`](https://github.com/flg77/acc-operator/blob/main/docs/standalone-parity.md): the contract between this standalone path and the operator path, and the gap table |
| `docs/operator-certification.md` | [`docs/catalogs-and-certification.md`](https://github.com/flg77/acc-operator/blob/main/docs/catalogs-and-certification.md): the two community catalogs, the road to a certified listing |
| — | [`README.md`](https://github.com/flg77/acc-operator#readme): compatibility table, install from OperatorHub, the 15-minute kind quickstart |

Quick start on any cluster with OLM:

```bash
kubectl create namespace acc-system
operator-sdk run bundle quay.io/flg77/acc-operator-bundle:0.3.0 -n acc-system --timeout 10m
```

**The parity rule still applies to this repository.** A change that adds an
`AgentSpec` field, a role-definition field or a new `ACC_*` variable must
either be expressible through the operator's `extraEnv` or the image's
`role.yaml`, or open a paired issue on `flg77/acc-operator`. A field of the
roles index (`acc/roles_index.py`, `schema`) that changes meaning is such a
change: the operator parses it.

The `operator/` and `console-plugin/` trees and the two parity tests left this
repository with OR-11; their history is in `git log -- operator console-plugin`
up to that commit, and their development continues in the operator repository.
