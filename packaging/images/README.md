# The images

Two pipelines, both taking a **release tag** and neither pushing anything.

| script | what it builds | why it is separate |
|---|---|---|
| `build-images.sh <tag>` | every component image, from the source at that tag | the components each carry a hand-picked dependency surface |
| `build-agent-rpm.sh <tag>` | `acc-agent-core` from the **released RPM** in the channel | one provenance chain for the one component whose weight already matches (IN-11, 2026-09-10) |

```bash
packaging/images/build-images.sh v0.17.2                      # the eight code images
packaging/images/build-images.sh v0.17.2 --only cli,tui       # a subset
packaging/images/build-images.sh v0.17.2 --with-sidecars --with-infra
packaging/images/build-images.sh v0.17.2 --dry-run            # say what would happen
```

`BUILD_HOST` (default `lighthouse`), `BUILD_ROOT` (`/git/ml/agentic`) and
`ACC_IMAGE_PREFIX` (`quay.io/flg77/acc_images`) select where it builds and how the
images are named.

## What it does, and why each step is there

* **The tag, never the working tree.** The context is `git archive <tag>` exported
  to the build host. `acc-deploy.sh build` builds what is in your checkout and
  tags it with `git describe` — that is the developer's loop and stays as it is.
* **One tag per component**: `<prefix>:acc-<component>-<version>`, the convention
  the RPM image pipeline already uses and the operator already consumes through
  `spec.imageRepository`.
* **Verified before it is staged.** An image carrying the ACC package must report
  the release's version from inside (`import acc; acc.__version__`); an image
  without one must start. The RPM pipeline learned this on a host: an upgrade
  once left the old code running while the package query said the new version.
* **A failure stages nothing** and exits non-zero.
* **The push is the operator's.** Agent pushes of ACC images are blocked by
  design; the script prints the `podman push` commands.

## The components

| component | version | notes |
|---|---|---|
| `agent-core`, `cli`, `tui`, `webgui`, `catalog`, `pkg`, `runtime-evidence-bridge`, `mcp-echo` | the release | the ACC-code images; version-verified |
| `mcp-web-fetch`, `mcp-web-search-brave`, `mcp-web-browser-harness` | 0.1.0 | `--with-sidecars`; their own version, as the compose refers to them |
| `nats` (2.10.22), `redis` (7.2) | pinned | `--with-infra`; a release must not renumber redis |

Not built here: **`Containerfile.agent-core-rpm`** (its own pipeline above) and
**`container/Containerfile.flavour`** (a per-deployment bake of packs and
governance, not a release artefact). `tests/test_image_pipeline.py` fails if a
new production Containerfile is neither covered nor excluded.

## Traps

* **An image must not copy an untracked file.** `Containerfile.agent-core` used to
  `COPY models.yaml`, which git does not track — it built from a developer's tree
  and failed from a tag. It bakes `models.yaml.example` now (IN-11e); operators
  still override with a mount or `ACC_MODELS_PATH`.
* **`--entrypoint python3`** is how the verification runs, because most images
  have an entrypoint of their own (`acc-cli`, `acc-webgui`, …).
* The sidecars and the infrastructure images are **not** rebuilt for every
  release; ask for them explicitly when their pinned version changes.
