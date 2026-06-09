# Tasks: acc-agent-core image slimming

## Phase 1 — Foundation (baseline + context)
- [ ] Record baseline: `podman images localhost/acc-agent-core:0.1.0` size and `podman history` layer table; note the 5.8 GB pip layer and 228 MB build-tools layer.
- [ ] Confirm the S2I site-packages root on `ubi10/python-312-minimal` (e.g. `/opt/app-root/lib/python3.12/site-packages`) so the runtime-stage COPY path is correct.
- [ ] Review/refresh `container/production/.containerignore` to keep build context minimal.

## Phase 2 — Core logic (CPU torch)
- [ ] Add `container/production/torch-cpu-constraints.txt` (or an inline index directive) pinning torch to the CPU wheel index.
- [ ] In the builder stage, install CPU `torch` first, then the remaining pyproject deps so `sentence-transformers` resolves against the already-present CPU torch.

## Phase 3 — Integration (multi-stage rewrite)
- [ ] Rewrite `Containerfile.agent-core` into `builder` + runtime stages; builder keeps gcc/python3-devel, runtime does not.
- [ ] Runtime stage: `COPY --from=builder` the site-packages tree and `/app/models`; re-add app source, entrypoint, `/etc/passwd` UID 1001 entry, dirs, perms, env, USER 1001.
- [ ] Preserve all existing runtime ENV (`ACC_CONFIG_PATH`, `SENTENCE_TRANSFORMERS_HOME`, etc.) and the entrypoint/CMD.

## Phase 4 — Testing (verify on acc1)
- [ ] Build the new image; record new size and layer table; compare to baseline.
- [ ] Assert no GPU: `python3 -c "import torch; assert not torch.cuda.is_available()"`; `pip list | grep -i nvidia` returns nothing.
- [ ] Embedding smoke test: load `all-MiniLM-L6-v2` from the baked cache, encode a string, assert a 384-dim vector — offline (no network).
- [ ] Import + storage test: `import acc.agent` succeeds; lancedb opens/creates a temp table.
- [ ] Assert no build tools in runtime stage (`gcc` absent).

## Phase 5 — Polish & handoff
- [ ] Update the Containerfile header comment block to document the multi-stage + CPU-torch rationale and the new expected size.
- [ ] Record before/after sizes in the change proposal's success-criteria checklist.
- [ ] Hand off: this slimmed image is the artifact to be pushed in the `20260609-operator-single-repo-images` rollout — do NOT push the 6.4 GB image first.
