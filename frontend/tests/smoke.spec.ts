import { test, expect } from "@playwright/test";

test.describe("Frontend smoke tests", () => {
  test("landing page renders successfully", async ({ page }) => {
    await page.goto("/");
    // Verify the page loaded by checking for ClinCoherence title and main container
    await expect(page).toHaveTitle(/ClinCoherence/);
    const mainElement = page.locator(".cc-login-wrap");
    await expect(mainElement).toBeVisible();
    await expect(page.locator("h1")).toHaveText("ClinCoherence");
  });
});
