# Design: acc-agent-core image slimming

## Approach

Two independent, compounding reductions:
1. **CPU-only PyTorch** — install `torch` from the CPU wheel index so the CUDA runtime and
   `nvidia-*` wheels (~the bulk of the 5.8 GB layer) are never pulled.
2. **Multi-stage build** — compile C-extensions in a throwaway builder that has
   `gcc`/`python3-devel`, then copy the populated site-packages (and baked model) into a
   clean runtime stage on `ubi10/python-312-minimal` with no compilers.

Both preserve runtime behavior; the agent's only use of the heavy stack is CPU embedding.

## Files to modify

- `container/production/Containerfile.agent-core` — rewrite as multi-stage (builder +
  runtime). Builder: `microdnf install gcc python3-devel`, then pip install deps with the
  CPU torch index, then bake the embedding model. Runtime: copy `/opt/app-root` (or the
  venv/site-packages path) + `/app/models` + app source; set USER 1001, entrypoint, env.
- `container/production/torch-cpu-constraints.txt` *(new, optional)* — pip constraints /
  index directive pinning torch to the CPU build, referenced from the builder stage. Keeps
  the version policy explicit and auditable.
- `container/production/.containerignore` — verify it excludes tests, docs, `.git`, the
  `operator/` tree, and any local `data/` so build context stays minimal.

## Key build logic (builder stage)

```dockerfile
# install CPU torch first so sentence-transformers resolves against it
RUN pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu torch
# then the rest of the deps (torch already satisfied → no CUDA pull)
RUN pip install --no-cache-dir <deps-from-pyproject minus torch handling>
```

Runtime stage copies only the install tree:
```dockerfile
FROM registry.access.redhat.com/ubi10/python-312-minimal:latest
COPY --from=builder /opt/app-root /opt/app-root
COPY --from=builder /app/models /app/models
COPY acc/ regulatory_layer/ entrypoint.sh ...
USER 1001
```

## Data model / API changes

None. No CRD, no runtime config, no API surface changes.

## Error handling

- If the CPU torch index is unreachable at build time, the build fails fast in the builder
  stage (surfaced in build logs) — no silent fallback to CUDA.
- If `--copy-from` misses a path (e.g. the actual site-packages root differs on the S2I
  image), the embedding smoke test in Phase 4 catches it before any push.

## Alternatives considered

- **Strip CUDA libs post-install in one stage** — rejected: fragile, leaves pip metadata
  inconsistent; multi-stage is cleaner.
- **Replace sentence-transformers with onnxruntime + a quantized MiniLM** — large potential
  win (~hundreds of MB total) but a behavior/accuracy change; deferred to a separate spec.
- **Drop the baked model, download at runtime** — rejected: reintroduces the offline-start
  and egress fragility we just fought on the sandbox.

## Testing strategy

- Build on acc1; record `podman images` size and `podman history` layer sizes before/after.
- `podman run --rm <img> python3 -c "import torch; print(torch.__version__, torch.cuda.is_available())"` → CUDA `False`, no nvidia libs.
- Embedding smoke test: encode a sentence, assert shape `(384,)`.
- Import test: `python3 -c "import acc.agent"`; lancedb open/create a temp table.
- Assert absence: `pip list | grep -i nvidia` empty; `which gcc` empty in runtime stage.
