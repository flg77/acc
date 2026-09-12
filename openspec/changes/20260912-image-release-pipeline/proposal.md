# 20260912-image-release-pipeline — proposal

Backlog: vault `20-backlog/install/IN-11` item **IN-11a** (the image release
pipeline) and **IN-11e** (`Containerfile.agent-core` copies an untracked file).
Follows `20260909-acc-install` IN-06 (the RPM) and its
`packaging/rpm/release-pipeline.sh`.

## Why

The package became reproducible in v0.15.0: `release-pipeline.sh <tag>` builds
from `git archive` of the tag, refuses an artefact that carries CUDA or misses
the layout, publishes it, and then makes a real host upgrade from the channel and
prove that `rpm -q` and `acc --version` agree.

The **images have nothing of the sort**, and they are what RHOAI runs:

* `acc-deploy.sh build` builds **the working tree**, tags with `git describe`,
  and is a developer's command — a tagged release does not produce images.
* Nothing verifies an image after it is built: no check that the code inside is
  the release, which is exactly the failure the RPM pipeline exists to prevent
  (an upgrade once left a host on the old version with the new package).
* A component reaches quay **only if someone remembers**, one at a time.
* `container/production/Containerfile.agent-core` does `COPY models.yaml`, and
  `models.yaml` is **not tracked** (only `models.yaml.example` is). The source
  agent image therefore builds from a developer's tree and **fails from a clean
  clone or a `git archive` of a tag** — IN-11e, and a blocker for any pipeline
  that builds from a tag.

The operator's `spec.imageRepository` points at images no pipeline produces.

## What changes

### Phase 1 (this ship)

1. **`packaging/images/build-images.sh <tag>`** — every component image from a
   release tag:
   * the tag must exist; the build context is `git archive <tag>` exported to the
     build host, never the working tree;
   * each component is built from its Containerfile with
     `--build-arg ACC_VERSION=<version>` and tagged
     `<prefix>:acc-<component>-<version>` — the convention
     `build-agent-rpm.sh` already uses and the operator already consumes;
   * **verified**: an ACC-code image must report the release version from inside
     (`import acc; acc.__version__`); an image with no ACC package must start;
   * a failure stages **nothing** and exits non-zero;
   * the push commands are printed and never run — pushing ACC images is the
     operator's step (the agent exfil classifier blocks it).
2. **Which components.** The eight ACC-code images (`agent-core`, `cli`, `tui`,
   `webgui`, `catalog`, `pkg`, `runtime-evidence-bridge`, `mcp-echo`) build at the
   release's version. The three MCP sidecars and `nats` / `redis` keep their own
   pinned versions, exactly as the compose refers to them, behind
   `--with-sidecars` / `--with-infra`: a release must not renumber redis.
   `agent-core-rpm` keeps its own pipeline (it installs the released package,
   IN-11 decision 2026-09-10); `Containerfile.flavour` is a per-deployment bake,
   not a release artefact.
3. **IN-11e fixed**: the source agent image copies `models.yaml.example`, as the
   RPM image already does. A clean clone and a tag both build.

### Phases 2–N (deferred)

* Publishing from the pipeline (a registry credential the agent may not hold).
* A digest manifest per release — what was built, from which tag, with which
  base image digests — beside the AgentBOM.
* `nats` / `redis` rebuilt only when their pinned version changes.
* Multi-arch (`arm64`) images for the edge.
* IN-11b: the images adopt the `/etc/acc` discovery layout.

## Impact

* **Affected code:** `packaging/images/build-images.sh` (new),
  `container/production/Containerfile.agent-core` (IN-11e),
  `tests/test_image_pipeline.py` (new), `docs/INSTALL.md` + `CHANGELOG.md`.
* **New env knobs:** none beyond the existing `BUILD_HOST`, `BUILD_ROOT`,
  `ACC_IMAGE_PREFIX`.
* **Tests:** `tests/test_image_pipeline.py` — every production Containerfile is
  covered or explicitly excluded, the pipeline builds from a tag, each image is
  tagged by component and version, ACC-code images are version-verified, a
  failure stages nothing, the push stays the operator's, the compose's images all
  exist in the pipeline, and the agent image no longer copies an untracked file.
* **Backward compatibility:** `acc-deploy.sh build` is untouched — it stays the
  developer's loop. Nothing is pushed by this change.

## What stays open after Phase 1

* The images are built and verified on **one** build host; nothing proves them in
  a cluster. That is the operator's rollout on bb3.
* The pipeline does not compare an image against the previous release (size
  drift, base-image bumps).
* `acc-deploy.sh build` and this pipeline still describe the same images twice —
  one for development, one for release.
