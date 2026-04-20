# Proposal: Phase 1a — Backend Abstraction Layer

**Change ID:** 20260403-phase1a-backend-abstraction
**Date:** 2026-04-03
**Status:** Active
**Author:** Michael

---

## Problem Statement

The ACC v0.1.0 specification defines a dual-mode deployment model (standalone Podman
vs. RHOAI integrated) but has no code yet. The first implementation step must create
the backend abstraction layer so that all subsequent modules (cognitive core, membrane,
signaling) are immediately portable across both deployment modes without code branching.

Without this layer, every component that touches infrastructure (LLM, messaging,
vector DB, metrics) would need its own if/else logic for standalone vs. RHOAI mode,
making the codebase brittle and hard to test.

## Current Behavior

This capability does not exist. The project directory contains only specification
documents and regulatory rule templates.

## Desired Behavior

A Python package (`acc/`) is established with:
- Protocol-based backend interfaces (PEP 544 structural subtyping)
- Concrete implementations for all infrastructure targets defined in v0.2.0
- A config loader (`acc/config.py`) that reads `acc-config.yaml` and selects backends
- A `pyproject.toml` with all dependencies pinned
- A working `Containerfile` per service role using `ubi10-minimal` base
- A `podman-compose.yml` for standalone Podman deployment
- Unit tests for all backends using mocks/stubs (no live infrastructure required)

## Success Criteria

- [ ] `acc/backends/__init__.py` defines all 4 Protocol interfaces
- [ ] All 9 backend implementations are importable without errors
- [ ] `acc/config.py` selects correct backends from `acc-config.yaml`
- [ ] `pytest tests/` passes with 100% of new tests green
- [ ] `podman build` succeeds for `agent-core` image using `ubi10-minimal`
- [ ] `podman-compose up` starts at least one agent in REGISTERING state
- [ ] All containers use RHEL UBI10 base images only

## Scope

### In Scope
- `acc/` Python package (backends + config)
- `pyproject.toml` with pinned dependencies
- `Containerfile.agent-core` (UBI10-based)
- `podman-compose.yml` for standalone mode (NATS + agent-core + redis-sidecar + opa-sidecar)
- Unit tests for all backend implementations
- `.env.example` for developer setup

### Out of Scope
- Cognitive core implementation (Phase 1b)
- RHOAI deployment manifests (Phase 2a)
- NATS-Kafka bridge (Phase 2b)
- MCP server implementations (Phase 3)
- Live infrastructure beyond the initial smoke test

## Assumptions

1. Standalone target uses Podman (not Docker)
2. Target host architecture is ARM64 or AMD64 — will build multi-arch
3. NATS runs as a separate shared container (not embedded) in standalone mode
4. The embedding model (all-MiniLM-L6-v2) is downloaded at container build time
5. OPA binary is bundled in the agent-core image (not a separate sidecar initially — sidecar pattern comes in Phase 2a)
6. `ubi10-minimal` is available on registry.access.redhat.com without auth for pull
7. Python 3.12 is the target runtime
