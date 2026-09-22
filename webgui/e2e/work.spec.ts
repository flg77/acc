import { expect, test } from "@playwright/test";
import { gotoSection } from "./fixtures";

test("prompt: sending shows the turn, then the mock's error (no live LLM)", async ({ page }) => {
  await gotoSection(page, "work/prompt");
  await page.getByLabel("Message").fill("Which applications wait for my decision?");
  await page.getByRole("button", { name: "Send" }).click();
  const log = page.getByRole("log", { name: "Conversation" });
  await expect(log).toContainText("Which applications wait for my decision?");
  // The fixture backend has no LLM behind /api/prompt — a 500 is expected and
  // proves the error path renders instead of hanging on "busy" forever.
  await expect(log).toContainText(/Error/i, { timeout: 10_000 });
});

test("prompt: Send is disabled with nothing typed, enabled once a role is available", async ({ page }) => {
  await gotoSection(page, "work/prompt");
  await expect(page.getByRole("button", { name: "Send" })).toBeDisabled();
  await page.getByLabel("Message").fill("hello");
  await expect(page.getByRole("button", { name: "Send" })).toBeEnabled();
});

test("board: five columns, the blocked card links to Governance", async ({ page }) => {
  await gotoSection(page, "work/board");
  await expect(page.getByRole("heading", { name: "Queued" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Running" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Blocked" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Done" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Failed" })).toBeVisible();
  await expect(page.getByText("Draft the credit memo")).toBeVisible();
  const link = page.getByRole("link", { name: "Answer the gate in Governance" });
  await expect(link).toHaveAttribute("href", "#/governance/compliance");
});

test("board: cancel publishes a control and reports the actor", async ({ page }) => {
  await gotoSection(page, "work/board");
  const runningCard = page.locator(".pf-v6-c-card", { hasText: "Draft the credit memo" });
  await runningCard.getByRole("button", { name: "Cancel" }).click();
  // The note is a PatternFly Alert rendered as <p> (component="p"), not a heading.
  await expect(page.getByText(/cancel → Draft the credit memo/)).toBeVisible();
});

test("comms: the three feeds render from the snapshot", async ({ page }) => {
  await gotoSection(page, "work/comms");
  await expect(page.getByRole("heading", { name: "Knowledge feed" })).toBeVisible();
  await expect(page.getByText("credit-policy")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Signal-flow log" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Episode-nomination queue" })).toBeVisible();
  await expect(page.getByText("ep-31")).toBeVisible();
});
