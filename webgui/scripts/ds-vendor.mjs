// Copy PatternFly's stylesheet and fonts next to the design-system previews.
//
// The previews link ./vendor/patternfly.min.css so they render offline and
// when uploaded by /design-sync — no CDN.  vendor/ is generated, not committed:
// the version is the one pinned in package.json.

import { cpSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const pf = join(root, "node_modules", "@patternfly", "patternfly");
const out = join(root, "design-system", "vendor");

rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, "assets"), { recursive: true });
cpSync(join(pf, "patternfly.min.css"), join(out, "patternfly.min.css"));
cpSync(join(pf, "assets", "fonts"), join(out, "assets", "fonts"), { recursive: true });
console.log(`design-system/vendor ← @patternfly/patternfly`);
