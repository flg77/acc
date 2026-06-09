# Proposal: Slim the acc-agent-core container image

## Problem

The production `acc-agent-core` image is **6.4 GB**. A layer breakdown shows one pip
dependency layer accounts for **5.8 GB** — `sentence-transformers` transitively pulls the
**CUDA build of PyTorch** plus `nvidia-*` wheels. The agent only uses sentence-transformers
for CPU embedding (`all-MiniLM-L6-v2`, 384-dim); all LLM inference is remote (the in-cluster
`qwen3-8b` vLLM endpoint). The GPU runtime is dead weight. A single-stage build also leaves
`gcc` + `python3-devel` (228 MB) in the final image. This oversized image makes pulls onto
the sandbox slow and is especially costly over the private `acc_images` repo.

## Current behavior

`pip install` resolves `torch` to the default (CUDA) wheel; build tools remain in the
runtime image; final size ≈ 6.4 GB.

## Desired behavior

PyTorch is installed from the CPU wheel index, eliminating CUDA/nvidia payload. The image is
built multi-stage: a builder compiles C-extensions (lancedb) with gcc/python3-devel, and the
runtime stage copies only the resulting site-packages onto the minimal UBI base — no
compilers in the final layer. The baked embedding model is retained (offline startup).
Runtime behavior is unchanged: the agent imports, the embedding model encodes, lancedb works.

## Success criteria

- [ ] Final image ≤ 2.0 GB (target; stretch ≤ 1.5 GB).
- [ ] No `nvidia-*` or CUDA libraries present in the image.
- [ ] No `gcc`/`python3-devel` in the final stage.
- [ ] Embedding smoke test passes: load `all-MiniLM-L6-v2`, encode a string, get a 384-vector.
- [ ] `python3 -m acc.agent --help` (or equivalent import) succeeds; lancedb opens a table.

## Scope

In: rewrite `container/production/Containerfile.agent-core` (multi-stage + CPU torch), an
optional pip constraints mechanism, `.containerignore` review, build+verify on acc1.
Out: changing embedding model choice, switching to onnxruntime, the operator image-address
change (separate spec `20260609-operator-single-repo-images`), pushing images.

## Assumptions

- CPU-only inference is acceptable for embeddings (it already runs CPU today).
- The CPU torch index `https://download.pytorch.org/whl/cpu` is reachable from acc1.
- `all-MiniLM-L6-v2` stays the default embedding model.
