import { expect, test } from "@playwright/test";
import { gotoSection } from "./fixtures";

test.describe("cluster", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "cluster", "cluster-only");
  });

  test("reads the AgentCollective, shows drift and the undeclared agent", async ({ page }) => {
    await gotoSection(page, "agentset/agents");
    // "AgentCollective" also appears in the gated-action reason below — the
    // "Declared in" line is the one that matters here.
    await expect(page.locator("p.acc-source")).toContainText("AgentCollective");
    const table = page.getByRole("grid", { name: "Agentset: declared beside running" });
    await expect(table.locator("tbody tr")).toHaveCount(2);
    // mortgage_underwriter: declared gpt-oss-120b == running gpt-oss-120b -> Converged
    await expect(table.getByRole("row", { name: /mortgage_underwriter/ })).toContainText("Converged");
    // undeclared: the arbiter runs but is declared nowhere
    await expect(page.getByRole("heading", { name: /declared nowhere/ })).toBeVisible();
    await expect(page.getByRole("grid", { name: "Running without a declaration" })).toContainText("arbiter");
  });

  test("the write action is gated with the environment's own reason", async ({ page }) => {
    await gotoSection(page, "agentset/agents");
    const addAgent = page.getByRole("button", { name: "Add agent" });
    await expect(addAgent).toBeDisabled();
    await expect(page.locator(".acc-gated__reason")).toContainText(/AgentCollective|not available/i);
  });

  test("packages show declared vs installed", async ({ page }) => {
    await gotoSection(page, "agentset/agents");
    await expect(page.getByRole("heading", { name: "Packages" })).toBeVisible();
    await expect(page.getByText("@acc/mortgage-roles")).toBeVisible();
    await expect(page.getByText("Installed")).toBeVisible();
  });
});

test.describe("edge", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "edge", "edge-only");
  });

  test("reads collective.yaml and shows the model drift", async ({ page }) => {
    await gotoSection(page, "agentset/agents");
    await expect(page.locator("p.acc-source")).toContainText("collective.yaml");
    const table = page.getByRole("grid", { name: "Agentset: declared beside running" });
    await expect(table.locator("tbody tr")).toHaveCount(2);
    // analyst: declared 2 replicas but only 1 heartbeat seen, running the wrong
    // model — drift outranks "converging" (acc.deployment.compare): a running
    // instance on the wrong model is worse than one that just needs a minute.
    const analystRow = table.getByRole("row", { name: /analyst/ });
    await expect(analystRow).toContainText("Drift");
    await expect(analystRow).toContainText("qwen3-14b");
    await expect(analystRow).toContainText("openai/qwen3-14b-maas");
    await expect(analystRow).toContainText("1 of 2 replicas");
  });

  test("the write action names the edge remedy", async ({ page }) => {
    await gotoSection(page, "agentset/agents");
    await expect(page.getByRole("button", { name: "Add agent" })).toBeDisabled();
    await expect(page.locator(".acc-gated__reason")).toContainText(/acc-deploy\.sh apply|KW-15/);
  });
});
