#!/usr/bin/env bash
# Bootstrap the flg77/acc-podman-desktop TypeScript repo.
#
# Stage 2.5 of the role-ecosystem strategy: ACC ships a Podman
# Desktop extension that lets operators infuse @acc/* packs through
# the Podman Desktop marketplace.  This script scaffolds the empty
# repo with the minimum that gets a "hello world" extension loading,
# then the operator iterates from there.
#
# Requires:
#   * gh CLI authenticated
#   * empty (or non-existent) public repo flg77/acc-podman-desktop
#
# Usage:
#   tools/bootstrap-podman-desktop.sh [--remote flg77/acc-podman-desktop]

set -euo pipefail

REMOTE="flg77/acc-podman-desktop"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote) REMOTE="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

if ! gh repo view "$REMOTE" >/dev/null 2>&1; then
  echo "creating $REMOTE as public..."
  gh repo create "$REMOTE" --public \
    --description "Podman Desktop extension for the ACC role ecosystem" \
    --license apache-2.0 \
    --confirm
fi

gh repo clone "$REMOTE" repo -- --depth=1
cd repo
git checkout -b bootstrap-v0.1.0 || git checkout bootstrap-v0.1.0

mkdir -p src

cat > package.json <<'JSON'
{
  "name": "@flg77/acc-podman-desktop",
  "displayName": "ACC Role Ecosystem",
  "description": "Browse, infuse, and manage @acc/* role packages from Podman Desktop.",
  "version": "0.1.0",
  "license": "Apache-2.0",
  "publisher": "flg77",
  "engines": { "podman-desktop": ">=1.10.0" },
  "main": "./dist/extension.js",
  "contributes": {
    "commands": [
      { "command": "acc.openMarketplace", "title": "ACC: Open Role Marketplace" },
      { "command": "acc.installPack", "title": "ACC: Install Family Pack" }
    ],
    "configuration": {
      "title": "ACC",
      "properties": {
        "acc.catalog.url": {
          "type": "string",
          "default": "https://flg77.github.io/acc-ecosystem",
          "description": "Canonical ACC catalog URL"
        }
      }
    }
  },
  "scripts": {
    "build": "tsc -p tsconfig.json",
    "watch": "tsc -p tsconfig.json --watch",
    "lint": "eslint src --ext .ts",
    "test": "vitest run"
  },
  "devDependencies": {
    "@podman-desktop/api": "^1.10.0",
    "@types/node": "^20.11.0",
    "typescript": "^5.4.0",
    "vitest": "^1.4.0",
    "eslint": "^8.57.0"
  }
}
JSON

cat > tsconfig.json <<'JSON'
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "commonjs",
    "outDir": "dist",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true
  },
  "include": ["src/**/*"]
}
JSON

cat > src/extension.ts <<'TS'
import * as extensionApi from '@podman-desktop/api';

/**
 * ACC Podman Desktop extension — Phase 1.
 *
 * Phase 1 scope: register two commands and a configuration block.
 *   - acc.openMarketplace — fetches the catalog index and opens
 *     a webview listing @acc/* packs.
 *   - acc.installPack — invokes `acc-pkg install <name>@<version>`
 *     against the host (operator must have acc-pkg on PATH).
 *
 * Phase 2 (not in bootstrap): inline tracing pane parity with
 * acc-webgui, surfaced as a Podman Desktop view.
 */

interface CatalogEntry {
  name: string;
  version: string;
  tarball_url: string;
  tarball_sha256: string;
}

interface CatalogIndex {
  schema_version: number;
  packages: CatalogEntry[];
}

async function fetchCatalog(url: string): Promise<CatalogIndex> {
  const r = await fetch(`${url.replace(/\/$/, '')}/index.json`);
  if (!r.ok) throw new Error(`catalog fetch failed: ${r.status}`);
  return (await r.json()) as CatalogIndex;
}

export async function activate(ctx: extensionApi.ExtensionContext): Promise<void> {
  const open = extensionApi.commands.registerCommand('acc.openMarketplace', async () => {
    const cfg = extensionApi.configuration.getConfiguration('acc');
    const url = cfg.get<string>('catalog.url')!;
    const index = await fetchCatalog(url);
    const names = index.packages.map(p => `${p.name}@${p.version}`).join('\n');
    await extensionApi.window.showInformationMessage(`ACC catalog (${url}):\n${names}`);
  });
  const install = extensionApi.commands.registerCommand('acc.installPack', async () => {
    const pick = await extensionApi.window.showInputBox({
      prompt: 'Package to install (e.g. @acc/research-roles@1.0.0)',
    });
    if (!pick) return;
    const proc = extensionApi.process.exec('acc-pkg', ['install', pick]);
    await proc;
    await extensionApi.window.showInformationMessage(`Installed ${pick}`);
  });
  ctx.subscriptions.push(open, install);
}

export async function deactivate(): Promise<void> {
  // nothing to teardown — registrations are auto-disposed by ctx
}
TS

cat > README.md <<'MD'
# acc-podman-desktop

Podman Desktop extension for the [ACC role
ecosystem](https://github.com/flg77/acc).  Browse and infuse
`@acc/*` family packs from inside Podman Desktop.

## Status

Bootstrap (v0.1.0). Two commands:

* **ACC: Open Role Marketplace** — fetches the canonical catalog
  index and lists available packs.
* **ACC: Install Family Pack** — invokes `acc-pkg install` against
  the host (operator must have `acc-pkg` on PATH).

## Catalog URL

Configurable via `acc.catalog.url`. Defaults to
`https://flg77.github.io/acc-ecosystem`.

## License

Apache 2.0.
MD

git add .
git -c user.email=flg@nomiras.com -c user.name="flg77" commit -m "bootstrap v0.1.0: Podman Desktop extension scaffold"
git push -u origin bootstrap-v0.1.0

gh pr create --repo "$REMOTE" --base main --head bootstrap-v0.1.0 \
  --title "Bootstrap v0.1.0 — Podman Desktop extension scaffold" \
  --body "Phase 1: openMarketplace + installPack commands wired against the canonical catalog. Phase 2 (tracing pane) follows."

echo ""
echo "==> bootstrap PR opened against $REMOTE"
