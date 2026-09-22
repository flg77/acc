import { expect, test } from "@playwright/test";
import { gotoSection } from "./fixtures";

test("marketplace: lists packages, flags the unreachable catalog", async ({ page }) => {
  await gotoSection(page, "packages/marketplace");
  // components/Table.tsx renders PatternFly's grid markup (role="grid"), matching
  // the pattern the Agentset table uses — not the native <table> "table" role.
  await expect(page.getByRole("grid", { name: "Packages" })).toBeVisible();
  await expect(page.getByText("@acc/mortgage-roles")).toBeVisible();
  // acc-canonical is https + a .invalid host in the fixture — always unreachable.
  await expect(page.getByText(/acc-canonical is unreachable/)).toBeVisible();
});

test("marketplace: the filter narrows the table", async ({ page }) => {
  await gotoSection(page, "packages/marketplace");
  // The backend filter is a PREFIX match on the full scoped name
  // (acc.marketplace.render_rows_and_errors: entry.name.startswith(name_filter)),
  // not a substring — "workspace" alone would match nothing.
  // PatternFly's SearchInput input has no distinct ARIA role; find it by placeholder.
  await page.getByPlaceholder("Filter by name…").fill("@acc/workspace");
  await page.keyboard.press("Enter");
  await expect(page.getByText("@acc/workspace-roles")).toBeVisible();
  await expect(page.getByText("@acc/mortgage-roles")).not.toBeVisible();
});

test.describe("cluster", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "cluster", "cluster-only");
  });

  test("marketplace: install is gated, not a button per row", async ({ page }) => {
    await gotoSection(page, "packages/marketplace");
    await expect(page.getByText(/AccPackageInstall/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Install" })).toHaveCount(0);
  });

  test("catalogs: read-only, no add form", async ({ page }) => {
    await gotoSection(page, "packages/catalogs");
    await expect(page.getByText(/AccCatalog objects/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Add a catalog" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Remove" })).toHaveCount(0);
  });
});

test.describe("edge", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "edge", "edge-only");
  });

  test("marketplace: install stages a marker and says so", async ({ page }) => {
    await gotoSection(page, "packages/marketplace");
    await page.getByRole("row", { name: /mortgage-roles/ }).getByRole("button", { name: "Install" }).click();
    await expect(page.getByText(/Staged a PROPOSE_INFUSE marker/)).toBeVisible();
    await expect(page.getByText(/nothing is installed until an operator dispatches it/)).toBeVisible();
  });

  // The mock backend is one long-lived process (playwright.config.ts webServer),
  // so a catalog written by one test is visible to the others — give each test
  // its own catalog id rather than relying on run order or shared cleanup.
  const addCatalog = async (page: import("@playwright/test").Page, id: string) => {
    await gotoSection(page, "packages/catalogs");
    await page.getByRole("button", { name: "Add a catalog" }).click();
    await page.getByLabel("Catalog id").fill(id);
    await page.getByLabel("URL").fill(`https://catalog.example/${id}`);
    await page.getByLabel("Required signer — issuer").fill("https://token.actions.githubusercontent.com");
    await page.getByRole("button", { name: "Add catalog" }).click();
    await expect(page.getByText(`Added ${id}`)).toBeVisible();
  };

  test("catalogs: add, re-prioritise on blur, then remove with confirmation", async ({ page }) => {
    const id = "team-catalog-add-remove";
    await addCatalog(page, id);
    const row = page.getByRole("row", { name: new RegExp(id) });
    await expect(row).toBeVisible();

    // priority commits on blur, not on every keystroke
    const priority = row.getByRole("spinbutton", { name: `Priority of ${id}` });
    await priority.fill("150");
    await page.getByRole("heading", { name: "Catalogs" }).click(); // move focus elsewhere
    await expect(page.getByText(`Priority of ${id} set to 150`)).toBeVisible();

    await row.getByRole("button", { name: "Remove" }).click();
    await expect(page.getByRole("heading", { name: "Remove this catalog?" })).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Remove" }).click();
    await expect(page.getByText(`Removed ${id}`)).toBeVisible();
    await expect(page.getByRole("row", { name: new RegExp(id) })).toHaveCount(0);
  });

  test("catalogs: Cancel on the remove dialog changes nothing", async ({ page }) => {
    const id = "team-catalog-cancel";
    await addCatalog(page, id);
    const row = page.getByRole("row", { name: new RegExp(id) });
    await row.getByRole("button", { name: "Remove" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    // still there — Cancel did not remove it
    await expect(row).toBeVisible();
  });
});
