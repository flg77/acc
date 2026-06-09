# Packaging capability — delta: acc-agent-core image slimming

## ADDED

### Agent-core image footprint

**REQ-PKG-IMG-001** The `acc-agent-core` production image SHALL install PyTorch from the
CPU wheel index. The resulting image SHALL contain no CUDA runtime libraries and no
`nvidia-*` Python packages.

**REQ-PKG-IMG-002** The `acc-agent-core` image SHALL be built multi-stage. Build-only
toolchain packages (`gcc`, `python3-devel`) SHALL be present only in the builder stage and
SHALL NOT appear in the final runtime image.

**REQ-PKG-IMG-003** The final runtime image SHALL retain the baked embedding model
(`all-MiniLM-L6-v2`) such that, with no network access, the agent can load the model and
produce a 384-dimensional embedding.

**REQ-PKG-IMG-004** Runtime behavior SHALL be unchanged: the image SHALL run as UID 1001,
expose the same entrypoint/CMD, preserve all existing runtime environment variables, and
allow `import acc.agent` and a lancedb table open to succeed.

**REQ-PKG-IMG-005** The final image size SHALL be at most 2.0 GB. (Target; the pre-change
baseline is 6.4 GB.)

## Notes

This capability has no prior baseline spec; this delta is the first `packaging` spec and is
written as an ADDED section only. The slimmed image produced under these requirements is the
artifact published by the `20260609-operator-single-repo-images` rollout — the oversized
6.4 GB image SHALL NOT be pushed.
