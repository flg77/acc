// Shared helpers for the acc-webgui Playwright suite. No custom test() —
// projects already select the environment (cluster vs edge); a spec just
// imports `expectEnvironment` etc. and reads `testInfo.project.name`.

import { expect, type Locator, type Page } from "@playwright/test";

export async function gotoSection(page: Page, path: string) {
  await page.goto(`/#/${path}`);
  // The environment bar is the first thing every page waits on.
  await expect(page.getByRole("region", { name: "Environment" })).toBeVisible();
}

export async function expectEnvironmentKind(page: Page, kind: "CLUSTER" | "STANDALONE") {
  // The DOM text is Title Case ("Cluster"/"Standalone"); CSS text-transform
  // renders it uppercase, which Playwright does not see.
  await expect(page.getByRole("region", { name: "Environment" })).toContainText(new RegExp(kind, "i"));
}

/** The reason text of a gated-action notice — one Alert above the control. */
export function gatedReason(page: Page): Locator {
  return page.locator(".pf-v6-c-alert__description, .pf-v6-c-alert__title").first();
}
