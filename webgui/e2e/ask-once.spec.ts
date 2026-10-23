// 058 "ask once" — the WebGUI half of the verification-matrix HowTo.
//
// Runs only with the seed (ACC_E2E_058=1, see e2e/mock_webgui.py): a mission
// card and a destructive confirm waiting, an active and a closed mission, a
// prompt reply with the heading. Each step asserts what the operator sees and,
// with ACC_SHOTS_DIR set, saves the screenshot the HowTo embeds.
//
//   ACC_E2E_058=1 ACC_SHOTS_DIR=../shots npx playwright test e2e/ask-once.spec.ts --project edge

import { expect, test, type Page } from "@playwright/test";
import { gotoSection } from "./fixtures";

const SHOTS = process.env.ACC_SHOTS_DIR || "";

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

test.describe("058 ask once", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(!process.env.ACC_E2E_058, "needs the 058 seed (ACC_E2E_058=1)");
    test.skip(testInfo.project.name !== "edge", "edge only");
  });

  test("Prompt: Ask once is the default, the reply says where the work is heading", async ({ page }) => {
    await gotoSection(page, "work/prompt");
    await expect(page.locator("#prompt-mode")).toHaveValue("ASK_ONCE");
    await expect(page.locator("#prompt-mode option:checked")).toHaveText("Ask once (ASK_ONCE)");
    await shot(page, "w01_prompt_ask_once_default");

    await page.getByLabel("Message").fill("In the workspace, fix the failing test in calc/ and run it again.");
    await page.getByLabel("Message").press("Enter");
    const heading = page.getByTestId("heading");
    await expect(heading).toContainText("mission: m-3f9a2c71b0de");
    await expect(heading).toContainText("next: add a test for adding zero.");
    await shot(page, "w02_prompt_reply_with_heading");
  });

  test("Decisions: the mission card is answered here, missions are settled here", async ({ page }) => {
    await gotoSection(page, "work/decisions");
    const cards = page.getByTestId("decision-card");
    await expect(cards).toHaveCount(2);
    await expect(cards.first()).toContainText("Ask once: let coding_agent get this done?");
    await expect(cards.first().getByRole("button", { name: "allow the mission" })).toBeVisible();
    await expect(cards.nth(1)).toContainText("will delete or overwrite data");
    await shot(page, "w03_decisions_mission_card");

    await cards.first().getByRole("button", { name: "allow the mission" }).click();
    await expect(page.getByText(/option 1 — sent for ov-mission-0/)).toBeVisible();
    await shot(page, "w04_decisions_mission_allowed");

    const missions = page.getByTestId("mission-card");
    await expect(missions).toHaveCount(2);
    await expect(missions.first()).toContainText("ACTIVE");
    await expect(missions.nth(1)).toContainText("2 deletion(s) backed up");
    await expect(missions.nth(1).getByRole("button", { name: "Purge (final allowance)" })).toBeVisible();
    await missions.nth(1).scrollIntoViewIfNeeded();
    await shot(page, "w05_decisions_missions_and_final_allowance");
  });

  test("Settings → Evals: promptfoo is the operator's switch, the gate is posture", async ({ page }) => {
    await gotoSection(page, "settings/evals");
    await expect(page.getByText("Posture — changed only through oversight")).toBeVisible();
    await expect(page.getByText("evals.garak_gate")).toBeVisible();
    // The values come from /api/config: wait for them before the shot.
    await expect(page.getByText("\"promptinject\"", { exact: false })).toBeVisible();
    await expect(page.getByLabel("Run promptfoo")).toBeEnabled();
    await shot(page, "w06_settings_evals");
    // A controlled switch: it shows the state the server confirms, not the click.
    await page.getByLabel("Run promptfoo").click({ force: true });
    await expect(page.getByText(/promptfoo enabled/)).toBeVisible();
    await expect(page.getByLabel("Run promptfoo")).toBeChecked();
    await shot(page, "w07_settings_evals_promptfoo_on");
  });
});
