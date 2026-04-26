# ACC Container Package Analysis

Tracks which Python dependencies from `pyproject.toml` are available as
RPM packages (UBI / RHEL / EPEL) and which must be installed via pip.

Updated: 2026-04-26 | ACC version: 0.2.0

---

## Decision Criteria

- **Prefer RPM** when a package is available from UBI or RHEL channels without a
  subscription. RPMs provide Red Hat provenance, CVE tracking via RHSA advisories,
  and automatic updates through `microdnf upgrade`.
- **Use EPEL** only when a package is not in the core RHEL/UBI repos.
  EPEL is explicitly supported on UBI without a subscription.
- **Pip-only** when no RPM exists in RHEL or EPEL, or when the RPM lags the
  minimum required version.
- **Version mismatch** means an RPM exists but ships a version incompatible with
  ACC's `pyproject.toml` constraint — pip is used in that case.

---

## Dependency Table

| Package (pyproject.toml) | Version Constraint | RPM Available | RPM Name | RPM Channel | ACC Decision | Notes |
|--------------------------|-------------------|---------------|----------|-------------|--------------|-------|
| `nats-py` | `>=2.7,<3.0` | ❌ No | — | — | pip only | Not in RHEL/EPEL/Fedora as of 2026-04 |
| `lancedb` | `>=0.10,<1.0` | ❌ No | — | — | pip only | Embedded vector DB; not shipped by Red Hat |
| `pymilvus` | `>=2.4,<3.0` | ❌ No | — | — | pip only | Milvus client; not shipped by Red Hat |
| `anthropic` | `>=0.40,<1.0` | ❌ No | — | — | pip only | Anthropic Python SDK; not shipped by Red Hat |
| `httpx` | `>=0.27,<1.0` | ❌ No | — | — | pip only | Not in RHEL/EPEL (httplib2 is, but different) |
| `sentence-transformers` | `>=3.2,<4.0` | ❌ No | — | — | pip only | ML library; not shipped by Red Hat |
| `opentelemetry-sdk` | `>=1.25,<2.0` | ❌ No | — | — | pip only | OTel SDK; not shipped by Red Hat |
| `opentelemetry-exporter-otlp-proto-grpc` | `>=1.25,<2.0` | ❌ No | — | — | pip only | OTel exporter; not shipped by Red Hat |
| `pydantic` | `>=2.8,<3.0` | ⚠️ Version mismatch | `python3-pydantic` | UBI/RHEL | **pip only** | RHEL ships pydantic 1.x. ACC requires v2. Must use pip. |
| `pyyaml` | `>=6.0,<7.0` | ✅ Yes | `python3-pyyaml` | UBI/RHEL | **RPM** | Ships pyyaml 6.0 on RHEL 9/UBI9; compatible |
| `cryptography` | `>=42,<45` | ✅ Yes | `python3-cryptography` | UBI/RHEL | **RPM** | RHEL 9/UBI9 ships cryptography 42.x; compatible |
| `redis` (client) | `>=5.0,<6.0` | ✅ Yes | `python3-redis` | EPEL9 | **RPM (EPEL)** | EPEL9 ships redis-py 5.x |
| `msgpack` | `>=1.1,<2.0` | ✅ Yes | `python3-msgpack` | EPEL9 | **RPM (EPEL)** | EPEL9 ships msgpack 1.0.x — check version at build time |

### Optional dependencies (`[tui]`)

| Package | RPM Available | ACC Decision | Notes |
|---------|--------------|--------------|-------|
| `textual` | ❌ No | pip only | Terminal UI framework; not shipped by Red Hat |
| `rich` | ❌ No | pip only | Rich text/logging; not shipped by Red Hat |

### Optional dependencies (`[dev]`)

| Package | RPM Available | ACC Decision | Notes |
|---------|--------------|--------------|-------|
| `pytest` | ✅ Yes | pip only | `python3-pytest` in UBI, but pip version preferred for exact version control |
| `pytest-asyncio` | ❌ No | pip only | Not in RHEL/EPEL |
| `pytest-cov` | ❌ No | pip only | Not in RHEL/EPEL |

---

## Server Package (Redis binary)

| Component | RPM Available | Channel | Notes |
|-----------|--------------|---------|-------|
| `redis` (server binary) | ✅ Yes | EPEL9 | Ships Redis 7.x; compatible with ACC requirements |

---

## Build Tools (always RPM)

| Tool | RPM Name | Channel | Purpose |
|------|----------|---------|---------|
| `gcc` | `gcc` | UBI | Required to compile C extensions for pip packages |
| `python3-devel` | `python3-devel` | UBI | Python header files for C extensions |
| `curl` | `curl` | UBI minimal | NATS + EPEL RPM downloads |
| `tar`, `gzip` | `tar`, `gzip` | UBI | NATS binary extraction |

---

## Packages NOT Shipped by Red Hat

The following packages from `pyproject.toml` are **not available from Red Hat
or EPEL** and must always be installed via pip:

- `nats-py` — NATS.io Python client (pure Python, but no Red Hat packaging)
- `lancedb` — embedded vector DB; proprietary LanceDB project
- `pymilvus` — Milvus vector database Python client
- `anthropic` — Anthropic API Python SDK
- `httpx` — async HTTP client (not the same as `python3-requests`)
- `sentence-transformers` — ML embedding model loader (HuggingFace)
- `opentelemetry-sdk` — OpenTelemetry SDK for Python
- `opentelemetry-exporter-otlp-proto-grpc` — gRPC OTLP exporter
- `textual` — Textual TUI framework
- `rich` — Rich terminal output library

**Recommendation:** For airgapped OpenShift environments, these packages should
be mirrored to an internal PyPI proxy (e.g., Nexus, Artifactory, or
`pypi-server`). Set `PIP_INDEX_URL` in the Containerfile build args to point
to the internal mirror.

---

## Version Mismatch: pydantic

Red Hat ships `python3-pydantic` version **1.x** on RHEL 9/UBI9.
ACC requires pydantic **v2** (`>=2.8`), which introduced a breaking API change.
pydantic v2 cannot be substituted with v1 — the model validation API, field
types, and serialization are incompatible.

**Action required:** Always install pydantic via pip in production builds.
Do NOT install `python3-pydantic` from RPM in the agent-core build — it would
install v1 and conflict with pip-installed v2.

---

## Future Work

- Investigate whether the `nats-py` project would accept a Fedora/EPEL
  packaging contribution to allow RPM distribution.
- When `lancedb` publishes a stable 1.x release, re-evaluate RPM availability
  via Fedora/EPEL packaging.
- Consider vendoring `sentence-transformers` model weights into a separate
  base image layer to decouple model freshness from application builds.
