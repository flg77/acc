# Tasks — acc-podman-desktop extension

## Phase 1 — Bootstrap

- [x] `tools/bootstrap-podman-desktop.sh` scaffolds the empty repo
- [x] Proposal authored in this folder
- [ ] Operator runs the bootstrap script (one-shot)
- [ ] Operator merges the bootstrap PR on `flg77/acc-podman-desktop`

## Phase 1.5 — Marketplace listing

- [ ] Submit extension to Podman Desktop marketplace
- [ ] Document install instructions in `docs/PODMAN-DESKTOP.md`

## Phase 2 — Tracing pane parity

- [ ] Embed reasoning-trace webview parity with `acc-webgui`
- [ ] Wire NATSObserver client (or proxy through `acc-webgui`)
- [ ] Sign extension VSIX (operator's GitHub Actions OIDC keyless)
