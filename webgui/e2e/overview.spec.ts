import { expect, test } from "@playwright/test";
import { expectEnvironmentKind, gotoSection } from "./fixtures";

test("shows the environment and one row per agent", async ({ page }, testInfo) => {
  await gotoSection(page, "overview");
  await expectEnvironmentKind(page, testInfo.project.name === "cluster" ? "CLUSTER" : "STANDALONE");

  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
  // components/Table.tsx renders role="grid" (matches the Agentset table),
  // not the native <table> "table" role.
  await expect(page.getByRole("grid", { name: "Agents" })).toBeVisible();
  const rows = page.getByRole("grid", { name: "Agents" }).locator("tbody tr");
  await expect(rows).toHaveCount(3);
  // The arbiter is declared in neither backend's agentset (checked in agentset.spec.ts)
  // but it does heartbeat, so it must still show up here.
  await expect(rows.filter({ hasText: "arbiter" })).toHaveCount(1);
});

test("the collective figure is not left blank", async ({ page }) => {
  await gotoSection(page, "overview");
  // "Agents" also labels the table's <h2> below; scope to this stat's own
  // description-list group so "3" is read from the Agents figure specifically
  // (Patterns is 3 too in the fixture — a loose match would pass by accident).
  const agentsGroup = page.locator(".pf-v6-c-description-list__group").filter({ hasText: "Agents" });
  await expect(agentsGroup.locator(".pf-v6-c-description-list__description")).toHaveText("3");
});
