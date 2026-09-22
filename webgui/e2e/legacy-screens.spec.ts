// A click-through of the screens still on the .legacy stylesheet (not yet
// rebuilt on PatternFly — see #476's "not covered" list): they load under the
// new shell, without a console error, in both environments. Not a rewrite —
// that is Phase 4 — just proof the shell did not break them.

import { expect, test, type ConsoleMessage, type Page } from "@playwright/test";
import { gotoSection } from "./fixtures";

// The heading is each screen's first <Card title="…"> in screens.tsx (rendered
// as an <h3>) — there is no page-level <h1> on these yet, that is Phase 4.
const SCREENS: { path: string; heading: string }[] = [
  { path: "agentset/roles", heading: "Roles in use" },
  { path: "agentset/infuse", heading: "Infuse a role" },
  { path: "agentset/role-editor", heading: "Roles" },
  { path: "models/configuration", heading: "Configuration" },
  { path: "governance/compliance", heading: "Compliance health" },
  { path: "settings/diagnostics", heading: "Golden prompts" },
  { path: "settings/help", heading: "Help" },
];

function trackConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  page.on("pageerror", (err) => errors.push(String(err)));
  return errors;
}

for (const { path, heading } of SCREENS) {
  test(`${path}: renders under the new shell with no console error`, async ({ page }) => {
    const errors = trackConsoleErrors(page);
    await gotoSection(page, path);
    await expect(page.locator(".legacy")).toBeVisible();
    await expect(page.getByRole("heading", { name: heading }).first()).toBeVisible({ timeout: 10_000 });
    expect(errors, errors.join("\n")).toEqual([]);
  });
}

test("the environment bar and navigation persist across a legacy screen", async ({ page }) => {
  await gotoSection(page, "governance/compliance");
  await expect(page.getByRole("region", { name: "Environment" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Agentset" })).toBeVisible();
});
