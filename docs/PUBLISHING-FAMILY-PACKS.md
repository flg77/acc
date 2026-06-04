# Publishing the family packs to `flg77/acc-ecosystem`

Operator runbook for promoting the role packages into the public
`acc-ecosystem` repo.  Run this once per release that bumps a pack
version.

> **Source of truth.** The editable role sources + family manifests
> live in the **private** `flg77/acc-ecosystem-spearhead` repo (mirrors
> the `acc-spearhead` → `flg77/acc` model). Build there; only the built
> `.accpkg` artifacts flow to the public registry.

> **The corporate split.** `@acc/business-roles` is no longer a single
> 25-role monolith — it is seven per-domain packs (`@acc/hr-roles`,
> `@acc/finance-roles`, `@acc/sales-roles`, `@acc/marketing-roles`,
> `@acc/legal-roles`, `@acc/support-roles`, `@acc/operations-roles`)
> plus the `@acc/business-roles@2.0.0` **umbrella** meta-pack that
> `depends_on` all seven. **Keep the frozen `@acc/business-roles@1.0.x`
> monolith published** so existing `^1.0` pins keep resolving.

## Prerequisites (one-time)

* `flg77/acc-ecosystem` repo exists (✅ created by operator)
* `cosign` installed
* Either:
  * **OIDC keyless** (recommended): GitHub Actions workflow with
    `id-token: write` permission, OR
  * **Local keypair**: cosign keypair via
    `tools/cosign-pilot-keygen.sh` (Stage 0 pilot path)
* `gh` CLI authenticated against `flg77/acc-ecosystem`

## Step 1 — Build the packs locally

The corporate domain packs + umbrella build from the
`acc-ecosystem-spearhead` checkout (`acc` must be importable — `pip
install -e ../agentic-cell-corpus` or set `PYTHONPATH`):

```bash
cd <acc-ecosystem-spearhead>
./sync-sources.sh ../agentic-cell-corpus      # refresh vendored inputs
PYTHONPATH=../agentic-cell-corpus ./build-all.sh
ls dist/*.accpkg
```

Expected output:

```
dist/acc-hr-roles-1.0.0.accpkg           (3 roles)
dist/acc-finance-roles-1.0.0.accpkg      (3 roles)
dist/acc-sales-roles-1.0.0.accpkg        (6 roles)
dist/acc-marketing-roles-1.0.0.accpkg    (5 roles)
dist/acc-legal-roles-1.0.0.accpkg        (2 roles)
dist/acc-support-roles-1.0.0.accpkg      (3 roles)
dist/acc-operations-roles-1.0.0.accpkg   (7 roles)
dist/business-roles-2.0.0.accpkg         (umbrella → depends_on the 7)
```

Each is **byte-deterministic** — rebuilding produces identical bytes.
The foundational families (`workspace`, `research`, `devops`) build the
same way from their own manifests. **Do not rebuild
`@acc/business-roles@1.0.x`** — the frozen monolith stays as published.

> When staging into the registry, drop the `acc-` filename prefix so the
> file-mode/HTTPS catalog parses the scope correctly, e.g.
> `dist/acc-sales-roles-1.0.0.accpkg` → `packages/acc/sales-roles-1.0.0.accpkg`.

## Step 2 — Verify shapes

```bash
for pkg in dist/acc-*-roles-*.accpkg; do
  echo "=== $pkg ==="
  python -m acc.pkg.cli --json inspect "$pkg" \
    | jq '{name, version, role_count: (.roles | length), skill_count: (.skills | length), mcp_count: (.mcps | length)}'
done
```

## Step 3 — Sign each pack

### Option A — Local keypair (Stage 0 pilot)

```bash
KEY=~/.acc/keys/acc-pilot.key
for pkg in dist/acc-*-roles-*.accpkg; do
  cosign sign-blob --yes --key "$KEY" \
    --output-signature "${pkg}.sig" \
    "$pkg"
done
```

### Option B — OIDC keyless (production)

Inside a GitHub Actions workflow with `id-token: write`:

```bash
for pkg in dist/acc-*-roles-*.accpkg; do
  cosign sign-blob --yes \
    --oidc-issuer https://token.actions.githubusercontent.com \
    --output-signature "${pkg}.sig" \
    --output-certificate "${pkg}.pem" \
    "$pkg"
done
```

## Step 4 — Push to `flg77/acc-ecosystem`

Two paths — pick one:

### Path 1 — GitHub Releases (simplest)

```bash
TAG=v1.0.0
gh release create "$TAG" \
  --repo flg77/acc-ecosystem \
  --title "Family packs v1.0.0" \
  --notes-file <(cat <<EOF
Initial Stage 2 family extraction from acc@<commit-sha>.

| Family | Roles | Size |
|---|---|---|
| @acc/workspace-roles | 8 | 9.2 KB |
| @acc/research-roles | 6 | 6.4 KB |
| @acc/business-roles | 25 | 11.2 KB |
| @acc/devops-roles | 4 | 3.3 KB |
EOF
) \
  dist/acc-*-roles-*.accpkg dist/acc-*-roles-*.accpkg.sig
```

### Path 2 — Direct repo content (when the GitHub Pages hub lands)

```bash
git clone https://github.com/flg77/acc-ecosystem
cd acc-ecosystem

for fam in workspace research business devops; do
  mkdir -p "packages/acc"
  cp "<acc-repo>/dist/acc-${fam}-roles-1.0.0.accpkg" \
     "packages/acc/${fam}-roles-1.0.0.accpkg"
  cp "<acc-repo>/dist/acc-${fam}-roles-1.0.0.accpkg.sig" \
     "packages/acc/${fam}-roles-1.0.0.accpkg.sig"
  sha256sum "packages/acc/${fam}-roles-1.0.0.accpkg" \
    | awk '{print $1}' > "packages/acc/${fam}-roles-1.0.0.accpkg.sha256"
done

# Regenerate the static index.json
python <<'PY'
import json, hashlib
from pathlib import Path
packages = []
for accpkg in sorted(Path("packages").rglob("*.accpkg")):
    scope = accpkg.parent.name
    name_ver = accpkg.stem
    name, version = name_ver.rsplit("-", 1)
    sha = hashlib.sha256(accpkg.read_bytes()).hexdigest()
    rel = "/" + accpkg.as_posix()
    packages.append({
        "name": f"@{scope}/{name}",
        "version": version,
        "tarball_sha256": sha,
        "tarball_url": rel,
        "signature_url": rel + ".sig",
    })
Path("index.json").write_text(json.dumps({
    "schema_version": 1,
    "packages": packages,
}, indent=2))
PY

git add packages/ index.json
git commit -m "Publish family packs v1.0.0"
git push
```

## Step 5 — Verify by installing back into the ACC repo

```bash
cd <acc-repo-root>

# Point a workspace catalog at the new repo
mkdir -p .acc
cat > .acc/catalogs.yaml <<EOF
catalogs:
  - id: acc-ecosystem
    tier: trusted
    mode: https
    url: https://flg77.github.io/acc-ecosystem   # or wherever you serve from
    required_signer:
      issuer: https://token.actions.githubusercontent.com
      subject_pattern: "^https://github\\.com/flg77/acc-ecosystem/"
    priority: 100
EOF

# List + install one to confirm round-trip
acc-pkg list --available
acc-pkg install @acc/research-roles@1.0.0 \
  --signature <(curl -sSL https://flg77.github.io/acc-ecosystem/packages/acc/research-roles-1.0.0.accpkg.sig) \
  --key ~/.acc/keys/acc-pilot.pub
```

Stage 1.5.1 dual-source loader prefers the installed-package path
over in-tree — verify by checking the audit log line on agent boot:

```
acc.role_loader: resolved research_planner from installed:/var/lib/acc/packages/acc/research-roles-1.0.0/roles/research_planner/role.yaml
```

If it still says `in-tree`, the catalog didn't resolve — re-check
`acc-pkg list --available` and the signer pattern.

## Step 6 — Update `examples/catalogs.yaml`

Once `flg77/acc-ecosystem` serves the packages publicly, update
`examples/catalogs.yaml` to default to the new URL.

## Subsequent releases — bumping versions

Family packs follow independent semver:

```bash
python tools/build_family_pkg.py workspace --version 1.1.0
# author the changelog
# release v1.1.0 to flg77/acc-ecosystem
```

Operators pinning `@acc/workspace-roles@^1.0` automatically pick up
`1.1.0` on next `acc-deploy.sh apply`.  Stage 1.5.3 boot-time fetch
honors the constraint.

## CI (optional)

Drop this into `.github/workflows/publish-family-packs.yml` in this
repo to publish on tag:

```yaml
name: publish family packs
on:
  push:
    tags: ['family-v*']

permissions:
  id-token: write
  contents: write

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -e . cosign
      - run: |
          mkdir -p dist
          for fam in workspace research business devops; do
            python tools/build_family_pkg.py "$fam"
          done
      - run: |
          for pkg in dist/acc-*-roles-*.accpkg; do
            cosign sign-blob --yes \
              --output-signature "${pkg}.sig" \
              --output-certificate "${pkg}.pem" \
              "$pkg"
          done
      - uses: softprops/action-gh-release@v2
        with:
          repository: flg77/acc-ecosystem
          tag_name: ${{ github.ref_name }}
          files: |
            dist/acc-*-roles-*.accpkg
            dist/acc-*-roles-*.accpkg.sig
            dist/acc-*-roles-*.accpkg.pem
          token: ${{ secrets.ACC_ECOSYSTEM_RELEASE_TOKEN }}
```
