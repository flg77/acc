# Migrating from in-tree roles to packaged roles

Stage 2 of the ACC ecosystem extracts the 44 movable roles from
this repo's `roles/` directory into the standalone
`@acc/workspace-roles`, `@acc/research-roles`, `@acc/business-roles`,
and `@acc/devops-roles` packages.  This runbook walks operators
through the two-release deprecation cycle without losing role
coverage.

> **TL;DR**: declare the packages in your `collective.yaml`'s
> `required_packages:` field; ACC's boot-time fetch installs them
> before agents spawn; the dual-source loader prefers the
> installed-package version over the in-tree fallback.  Operators
> who never edit `collective.yaml` see no change in Stage 2 release
> N, then need to declare the packages once before upgrading to
> N+1.

## Timeline

| Release | What changes |
|---|---|
| **Stage 2 release N** | In-tree role dirs still present.  `acc.role_loader` emits a `DeprecationWarning` when loading any of the 44 movable roles in-tree.  All four family packages published to `acc-roles.dev`. |
| **Stage 2 release N+1** | In-tree role dirs *deleted*.  Operators who haven't declared `required_packages:` see `RoleNotFound` errors on agent boot. |

The "one minor release" window is per the operator decision
captured in `openspec/changes/20260604-role-ecosystem-strategy/ecosystem-implementation.md`
("Stage 2 in-tree-removal trigger" — decided: one minor release).

## Migration in 5 minutes

### Step 1 — Add `required_packages:` to your `collective.yaml`

Find the 44 movable role names you actually use in your
`collective.yaml` agents list.  Map them to the package family:

| Family package | Roles it provides |
|---|---|
| `@acc/workspace-roles` | `coding_agent`, `coding_agent_architect`, `coding_agent_dependency`, `coding_agent_implementer`, `coding_agent_reviewer`, `coding_agent_tester`, `analyst`, `synthesizer` |
| `@acc/research-roles` | `research_planner`, `research_competitor`, `research_critic`, `research_economist`, `research_strategist`, `research_synthesizer` |
| `@acc/business-roles` | All 30 business roles (HR, sales, marketing, finance, legal, ops, IT, support) |
| `@acc/devops-roles` | `data_engineer`, `devops_engineer`, `ml_engineer`, `security_analyst` |

Add a `required_packages:` block:

```yaml
collective_id: my-corpus

required_packages:
  - "@acc/workspace-roles@^1.0"
  - "@acc/research-roles@^1.0"   # only if you use research_*

agents:
  - role: coding_agent_architect
    cluster_id: c-arch
  - role: coding_agent_implementer
    cluster_id: c-impl
  # ...
```

### Step 2 — Re-apply

```bash
./acc-deploy.sh apply collective.yaml
```

The `apply` command (Stage 1.5.3 — already shipped) runs
`acc-cli collective pkg-install` before synthesizing the
podman-compose overlay.  Each declared package gets resolved
via the layered catalog, downloaded, signature-verified, and
unpacked into `/var/lib/acc/packages/`.

Watch for the audit log lines:

```
acc.pkg.fetch: fetch @acc/workspace-roles@1.0.2 from acc-canonical (tier=trusted)
acc.pkg.install: installed @acc/workspace-roles@1.0.2 → /var/lib/acc/packages/acc/workspace-roles-1.0.2
```

### Step 3 — Verify the dual-source loader picks the package

When agents boot, you'll see (Stage 1.5.1's audit log):

```
acc.role_loader: resolved coding_agent_architect from installed:/var/lib/acc/packages/acc/workspace-roles-1.0.2/roles/coding_agent_architect/role.yaml
```

vs the old in-tree path:

```
acc.role_loader: resolved coding_agent_architect from in-tree
```

If you see the in-tree path on the new release, either the package
isn't installed (check `acc-pkg list`) or your `collective.yaml`
catalogs configuration isn't picking up the right hub (check
`/etc/acc/catalogs.yaml`).

## "What if I miss the window?"

You can always resurrect a deleted in-tree role from git history:

```bash
# Find the last release that had the in-tree dir
git log --oneline -- roles/coding_agent_architect | head -1

# Bring just that role back into your local checkout
git checkout v<N>.<x>.<y> -- roles/coding_agent_architect/
```

This is the long-term safety net — git history is more durable
than any parallel-window length.

## Verifying without a hub

If you don't have access to `acc-roles.dev` (air-gap, dev hub
slow, etc.), build the family packages locally from the cloned
source:

```bash
# From the acc repo root, before upgrading to N+1
python tools/build_family_pkg.py workspace -o dist/
python tools/build_family_pkg.py research  -o dist/
# ... etc.

# Stage them as a file-mode catalog
mkdir -p ~/.acc/dev-catalog/acc/
cp dist/*.accpkg ~/.acc/dev-catalog/acc/
# Generate sha256 sidecars
for f in ~/.acc/dev-catalog/acc/*.accpkg; do
    sha256sum "$f" > "${f}.sha256"
done

# Declare a file-mode catalog in your workspace
mkdir -p .acc
cat > .acc/catalogs.yaml <<'EOF'
catalogs:
  - id: dev-local
    tier: self
    mode: file
    path: ~/.acc/dev-catalog
    required_signer:
      issuer: dev
      subject_pattern: ".*"
    priority: 500
EOF

# Now `acc-deploy.sh apply` finds the packages locally.
ACC_ALLOW_UNSIGNED=1 ./acc-deploy.sh apply collective.yaml
```

> `ACC_ALLOW_UNSIGNED=1` is required because locally-built packages
> aren't cosign-signed.  Operator-explicit + audit-logged per the
> Stage 0 signing-floor contract.

## Rolling back to N

If the migration goes sideways and you need to roll back to
Stage 2 release N (in-tree roles still present):

```bash
# Pin the older ACC release
git checkout v<N>.<x>.<y>
./acc-deploy.sh apply collective.yaml
```

The `required_packages:` block in `collective.yaml` is harmless on
older releases — they silently ignore the field.

## Common errors

| Symptom | Likely cause |
|---|---|
| `RoleNotFound: coding_agent_architect` on Stage 2 release N+1 | `required_packages:` missing the family providing it |
| `MissingDependency: @acc/workspace-roles@^1.0` | Catalog doesn't advertise the package — check `/etc/acc/catalogs.yaml` and `acc-pkg list --available` |
| `SignatureRejected: signer doesn't match` | Catalog's `required_signer.subject_pattern` doesn't match the publisher's OIDC identity |
| `RoleLoader: resolved <name> from in-tree` even after install | Cache stale; restart agent processes (`acc-deploy.sh down && up`) |

## References

* Stage 1.5.1 (dual-source loader): [PR #21](https://github.com/flg77/acc-spearhead/pull/21)
* Stage 1.5.2 (`required_packages:`): [PR #22](https://github.com/flg77/acc-spearhead/pull/22)
* Stage 1.5.3 (boot-time fetch): [PR #23](https://github.com/flg77/acc-spearhead/pull/23)
* Architecture: `openspec/changes/20260604-role-ecosystem-strategy/ecosystem-implementation.md`
