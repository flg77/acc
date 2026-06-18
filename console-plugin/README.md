# Agentic Cell Corpus — OpenShift Console Plugin

The cluster-native **oversight plane of record** for the Agentic Cell Corpus
(proposal 035, executing 020 WS-B). A
[dynamic console plugin](https://github.com/openshift/console-plugin-template)
that surfaces the four `acc.redhat.io` custom resources inside the OpenShift web
console, running in the console's own session with the **logged-in user's
token** — so per-user RBAC is automatic and there is no custom backend, no ACC
auth, and no new exposed port (proposal 035 G6).

> **Scope landed so far (035 PR-1…PR-4):** PR-1 scaffold + `src/models.ts` + the
> CRD↔models CI parity gate; PR-2 list/detail pages + nav for the four CRs; PR-3
> the `CatalogBrowse` catalog→install centerpiece (`src/components/CatalogBrowse.tsx`)
> — browse catalogs grouped by tier/priority, an install form that `k8sCreate`s an
> `AccPackageInstall`, and a live status monitor. PR-4 the OLM wiring: the
> `Containerfile`/`nginx.conf` serve the bundle over TLS `:9443` with an
> OpenShift service-serving cert, `manifests/` carries the `Deployment` +
> `Service` + `ConsolePlugin` (v1), and the operator CSV gains the
> `console.openshift.io/plugins` auto-enable annotation plus the plugin
> `Deployment` (Service + ConsolePlugin ship as standalone bundle objects).

## What it covers (target end-state)

| Surface | CRD | Operations |
|---|---|---|
| Corpora | `AgentCorpus` (`agentcorpora`) | watch, status conditions |
| Collectives | `AgentCollective` (`agentcollectives`) | watch, roster, model binding |
| Catalogs | `AccCatalog` (`acccatalogs`) | watch, tier/priority/signer |
| Installs | `AccPackageInstall` (`accpackageinstalls`) | watch + create (install path) |

All are `group: acc.redhat.io`, `version: v1alpha1`, `scope: Namespaced`.

## Version pin / support floor

| Thing | Pin | Why |
|---|---|---|
| Console SDK | `@openshift-console/dynamic-plugin-sdk@4.18.0` | 4.18-aligned line |
| SDK webpack | `@openshift-console/dynamic-plugin-sdk-webpack@4.18.1` | matches the SDK |
| PatternFly | `@patternfly/react-core@^5` | the SDK 4.18 peer |
| React | dev-pinned to **17** | the 4.18 console *shares* React 17 via module federation; `ConsoleRemotePlugin` fails the build if the plugin's React major differs |
| **Console / OCP support floor** | **4.18** | the pinned SDK's `@console/pluginAPI` floor |

The OpenShift console SDK is published with console-version-aligned dist-tags
(`4.18-latest`, `4.19-latest`, …). We pin **4.18** — a recent, broadly-deployed
GA release whose SDK peers PatternFly 5, matching this proposal's PatternFly-v5
constraint. **Proposal 035 Q3 (console version floor) is resolved to 4.18 here**;
revisit when the cluster fleet moves up. When bumping: change both
`@openshift-console/dynamic-plugin-sdk` and `…-webpack` together, bump
`consolePlugin.dependencies['@console/pluginAPI']` in `package.json`, and
re-run the parity gate + a CRC build.

## Build prerequisites

- **Node 18+ and npm** (CI/image build uses Node 22 — see `Containerfile`).
- For local console wiring later: an OpenShift cluster or
  [CRC](https://crc.dev/) with the console operator, plus `oc`.
- The CRD↔models **parity gate needs no Node** — it runs in the repo's pytest
  CI (`tests/test_console_plugin_models_parity.py`).

## Develop

```bash
cd console-plugin
npm install
npm run build         # production module-federation bundle -> dist/
npm run build-dev     # development bundle
npm start             # webpack-dev-server on :9001 (federation host)
npm run lint
npm run typecheck
```

A successful `npm run build` writes the federated assets and a
`plugin-manifest.json` into `dist/` — that manifest is what the console loads.

### Run against a console (dev)

The dev server serves the federated bundle on `:9001`. Point a running console
at it (the upstream template's `start-console` flow). The federated bundle now
advertises the PR-2/3 extensions (nav + list/detail + CatalogBrowse).

### Ship via OLM (PR-4)

In a cluster the plugin is delivered by the ACC operator bundle, not by hand:

- The image `quay.io/flg77/acc_images:acc-console-plugin-0.2.12` (built from
  `Containerfile`) runs nginx serving the bundle over **TLS `:9443`**, using an
  OpenShift **service-serving cert** minted from the `Service` annotation
  `service.beta.openshift.io/serving-cert-secret-name` and mounted at
  `/var/serving-cert`.
- `manifests/` holds the canonical `Deployment`, `Service`, and `ConsolePlugin`
  (`console.openshift.io/v1`, `spec.backend` schema; `metadata.name` =
  `acc-console-plugin`, the `consolePlugin.name` in `package.json`).
- The operator CSV
  (`operator/bundle/manifests/acc-operator.clusterserviceversion.yaml`) carries
  the plugin `Deployment` in `spec.install.spec.deployments` and the annotation
  `console.openshift.io/plugins: '["acc-console-plugin"]'`, which **auto-enables**
  the plugin in the console operator config on install. The `Service` and
  `ConsolePlugin` ship as standalone bundle objects (both are in
  operator-registry's supported-resource set; `Deployment` is not, hence the CSV).
- The `ConsolePlugin.spec.backend.service.namespace` is pinned to the CSV
  suggested namespace `acc-system`; if the operator is installed elsewhere the
  plugin Service namespace must be patched to match.

## CRD ↔ models parity (the drift guard, G4)

`src/models.ts` defines the four `K8sModel`s. They must match the operator CRD
bases exactly; a renamed kind or a mistyped plural would otherwise yield a
**silent empty list** in the console. `tests/test_console_plugin_models_parity.py`
loads every `operator/config/crd/bases/acc.redhat.io_*.yaml`, extracts
`(group, version, kind, plural)`, and asserts a bidirectional match with
`models.ts`. It is a plain pytest with no `acc` import and no Node, so it runs
in the existing CI:

```bash
python -m pytest tests/test_console_plugin_models_parity.py -q --no-cov
```

## Layout

```
console-plugin/
├── package.json            # deps + scripts + consolePlugin federation metadata
├── webpack.config.js       # ConsoleRemotePlugin (module federation)
├── console-extensions.json # extensions array (empty until PR-2 nav)
├── tsconfig.json
├── .eslintrc.json
├── Containerfile           # UBI9 node build -> UBI9 nginx serve (TLS :9443)
├── nginx.conf              # serves dist/ on TLS :9443 (service-serving cert), CORS
├── manifests/              # PR-4: Deployment + Service + ConsolePlugin (OLM-delivered)
└── src/
    ├── models.ts           # the four K8sModels (drift-guarded by the parity gate)
    ├── types.ts            # TS shapes for the four CRs (subset of spec/status)
    ├── components/
    │   ├── list.tsx        # shared list-page factory (watch + filter + table)
    │   ├── detail.tsx      # shared detail-page chrome (watch + sections)
    │   ├── status.tsx      # PhaseLabel / ConditionsTable / DetailItem / boxes
    │   └── CatalogBrowse.tsx  # PR-3: catalog browse -> install form -> status monitor
    └── pages/              # one List + one Details page per CR + gvk helper
```
