# Agentic Cell Corpus — OpenShift Console Plugin

The cluster-native **oversight plane of record** for the Agentic Cell Corpus
(proposal 035, executing 020 WS-B). A
[dynamic console plugin](https://github.com/openshift/console-plugin-template)
that surfaces the four `acc.redhat.io` custom resources inside the OpenShift web
console, running in the console's own session with the **logged-in user's
token** — so per-user RBAC is automatic and there is no custom backend, no ACC
auth, and no new exposed port (proposal 035 G6).

> **Scope of this PR (035 PR-1):** scaffold + `src/models.ts` + the CRD↔models
> CI parity gate only. The list/detail pages, nav extensions, and the
> catalog→install create path land in PR-2…PR-4. `console-extensions.json` is
> intentionally an empty array (`[]`) until PR-2 adds nav.

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

### Run against a console (later PRs)

The dev server serves the federated bundle on `:9001`. Point a running console
at it (the upstream template's `start-console` flow, or
`oc patch consoles.operator.openshift.io cluster` once the `ConsolePlugin` CR
ships in PR-4). PR-1 produces the bundle but advertises no extensions yet.

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
├── Containerfile           # UBI9 node build -> UBI9 nginx serve
├── nginx.conf              # serves dist/ on :8080, permissive CORS
└── src/
    └── models.ts           # the four K8sModels (drift-guarded by the parity gate)
```
