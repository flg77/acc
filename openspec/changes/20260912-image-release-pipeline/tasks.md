# 20260912-image-release-pipeline — tasks

## Phase 1 — every component image from a tag (IN-11a, IN-11e)

### 1.1 The pipeline
- [x] `packaging/images/build-images.sh <tag> [--only a,b] [--with-sidecars]
      [--with-infra] [--dry-run]`: the tag must exist; `git archive <tag>` to the
      build host; per component `podman build --build-arg ACC_VERSION=<version>`
      tagged `<prefix>:acc-<component>-<version>`
- [x] verification: an ACC-code image reports `acc.__version__` = the release;
      an image with no ACC package must start; a failure stages nothing (exit 1)
- [x] the push commands are printed, never run (operator-only)

### 1.2 The component tables
- [x] code images at the release version: `agent-core`, `cli`, `tui`, `webgui`,
      `catalog`, `pkg`, `runtime-evidence-bridge`, `mcp-echo`
- [x] pinned, behind flags: the three MCP sidecars (0.1.0), `nats` (2.10.22),
      `redis` (7.2) — a release must not renumber redis
- [x] excluded, with the reason in the script: `agent-core-rpm` (its own
      pipeline), `Containerfile.flavour` (a per-deployment bake)

### 1.3 IN-11e
- [x] `Containerfile.agent-core` copies `models.yaml.example`, so a clean clone
      and a `git archive` of a tag both build

### 1.4 Tests + docs
- [x] `tests/test_image_pipeline.py`
- [x] `packaging/images/README.md`: the two pipelines, the components, the traps
- [x] CHANGELOG

### Verification
- [x] `--dry-run` against a real tag
- [x] a real build of one component on lighthouse (`--only cli`) — built, verified
      (`acc 0.17.2`), 440 MB staged; it caught a CRLF bug in the script (LF-only now, tested)
- [ ] the full run for the next release, staged for the operator's push

## Phase 2 (deferred)
- [ ] a digest manifest per release, beside the AgentBOM
- [ ] publish from the pipeline (needs a credential the agent may not hold)
- [ ] multi-arch images for the edge
- [ ] IN-11b: the images adopt the `/etc/acc` discovery layout
