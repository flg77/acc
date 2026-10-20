import { expect, test } from "@playwright/test";
import { gotoSection } from "./fixtures";

// The Knowledge section (20260928-rdf-provenance-and-memory-views, RDF-07) over the
// fixture backend: one HIGH note, promoted to the hub by two operators, and one lesson.

test("memory: the note appears in its tier and in the hub, with ceiling and dissent", async ({ page }) => {
  await gotoSection(page, "knowledge/memory");
  await expect(page.getByRole("heading", { name: /^Private/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /^Enterprise hub/ })).toBeVisible();
  await expect(page.getByText("DTI above 43% needs a compensating factor.").first()).toBeVisible();
  await expect(page.getByText("disputed: one region applies 45%").first()).toBeVisible();
});

test("memory → lineage: the graph draws the note, the hub copy and both approvals", async ({ page }) => {
  await gotoSection(page, "knowledge/memory");
  await page.getByRole("link", { name: "Lineage" }).first().click();
  const table = page.getByRole("grid", { name: "Lineage, as a table" });
  await expect(table).toContainText("Published copy");
  await expect(table).toContainText("-> hub:hub-e2e");
  await expect(table.getByRole("cell", { name: "Approval", exact: true })).toHaveCount(2);
});

test("lineage: an id nobody can see reads as no note", async ({ page }) => {
  await gotoSection(page, "knowledge/lineage?note=does-not-exist");
  await expect(page.getByText('No note "does-not-exist" that you can see.')).toBeVisible();
});

test("learnings: the lesson with its use count", async ({ page }) => {
  await gotoSection(page, "knowledge/learnings");
  const table = page.getByRole("grid", { name: "Lessons" });
  await expect(table).toContainText("credit-policy");
  await expect(table).toContainText("3 task(s)");
});

test("contributions: the four counts render", async ({ page }) => {
  await gotoSection(page, "knowledge/contributions");
  await expect(page.getByText("Instances holding a copy")).toBeVisible();
  await expect(page.getByText("Tasks that used them")).toBeVisible();
});

test("knowledge map: graphs, the domain tree, and a neighbourhood", async ({ page }) => {
  await gotoSection(page, "knowledge/map");
  const graphs = page.getByRole("grid", { name: "Readable graphs" });
  await expect(graphs).toContainText("taxonomy");
  await expect(graphs).toContainText("shared-medium");
  await page.getByRole("link", { name: "Mortgage" }).click();
  await expect(page.getByRole("grid", { name: "Neighbourhood, as a table" })).toContainText("dti-cap");
});

test("conflicts: two regions disagree, both claims shown", async ({ page }) => {
  await gotoSection(page, "knowledge/conflicts");
  const table = page.getByRole("grid", { name: "Conflicts" });
  await expect(table).toContainText("dtiLimit");
  await expect(table).toContainText("43%");
  await expect(table).toContainText("45%");
});
