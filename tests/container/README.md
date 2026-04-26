# ACC Container Tests

Test suite for ACC production container images. Organized into four tiers
by runtime requirement.

## Test Tiers

| Tier | Directory | Requires | When to run |
|------|-----------|----------|-------------|
| **Unit** | `unit/` | Python only (no podman) | Every commit, pre-merge |
| **Build** | `build/` | `podman` or `buildah` | After Containerfile changes |
| **Runtime** | `runtime/` | Built images | After successful build |
| **Integration** | `integration/` | `podman-compose` + images | Before release |

## Running Tests

### Tier 0 — Unit tests (fastest, no container runtime)

```bash
pytest tests/container/unit/ -v
```

These tests parse Containerfiles and YAML files as text. They verify:
- All `FROM` instructions use `registry.access.redhat.com` UBI bases
- `USER 0` precedes all `pip install` / `microdnf install` commands
- Final `USER` is non-root (1001)
- Required OCI labels are present
- `podman-compose.yml` schema is correct
- `.containerignore` excludes secrets, tests, and build artefacts
- `package-analysis.md` covers all deps from `pyproject.toml`

### Tier 1 — Build tests (requires podman)

```bash
# Build all images first (or let tests build them)
pytest tests/container/build/ -v --junit-xml=results/build.xml
```

### Tier 2 — Runtime tests (requires built images)

```bash
# Build images first
podman build -f container/production/Containerfile.agent-core -t localhost/acc-agent-core:0.2.0 .
podman build -f container/production/Containerfile.redis -t localhost/acc-redis:7.2 .
podman build -f container/production/Containerfile.nats --build-arg NATS_VERSION=2.10.22 -t localhost/acc-nats:2.10.22 .
podman build -f container/production/Containerfile.tui -t localhost/acc-tui:0.2.0 .

pytest tests/container/runtime/ -v --junit-xml=results/runtime.xml
```

### Tier 3 — Integration tests (requires podman-compose)

```bash
cd <repo-root>
pytest tests/container/integration/ -v --junit-xml=results/integration.xml
```

The integration tests start the full stack via `podman-compose`, verify all
services reach healthy state, then tear down automatically.

### Run all tiers (unit only, safe for CI without container runtime)

```bash
pytest tests/container/unit/ -v --junit-xml=results/container-unit.xml
```

## Tekton CI

The `tekton/` directory contains reusable Tekton Pipeline and Task definitions:

| File | Purpose |
|------|---------|
| `task-lint-containers.yaml` | Tier 0: lint + unit tests (no runtime) |
| `task-build-containers.yaml` | Tier 1: build all 4 images with buildah |
| `task-test-runtime.yaml` | Tier 2: runtime tests per image |
| `pipeline-container-ci.yaml` | Full pipeline: lint → build → runtime |

### Apply to OpenShift cluster

```bash
oc apply -f tests/container/tekton/

# Start a pipeline run
tkn pipeline start acc-container-ci \
  --workspace name=source,claimName=acc-source-pvc \
  --workspace name=results,claimName=acc-results-pvc \
  --param acc-version=0.2.0 \
  --showlog
```

### JUnit results

Each Tekton Task writes JUnit XML to the `results` workspace:
- `container-unit/results.xml` — lint + unit test results
- `container-build/results.xml` — build pass/fail per image
- `container-runtime/results.xml` — runtime test results

These files are consumed by `tkn pipeline describe` and visible in the
Tekton Dashboard under the Pipeline Run's task results.

## Containerfiles (Production)

| File | Base | Purpose |
|------|------|---------|
| `container/production/Containerfile.agent-core` | `ubi10/python-312-minimal` | ACC agent (ingester/analyst/arbiter) |
| `container/production/Containerfile.redis` | `ubi9/ubi-minimal` + EPEL | Redis 7 working memory |
| `container/production/Containerfile.tui` | `ubi10/python-312-minimal` | Textual TUI dashboard |
| `container/production/Containerfile.nats` | `ubi9/ubi-minimal` | NATS 2.10 JetStream server |

See `container/production/package-analysis.md` for the RPM vs pip decision
for every Python dependency.
