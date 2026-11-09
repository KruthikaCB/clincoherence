import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration for ClinCoherence frontend end-to-end tests.
 * See https://playwright.dev/docs/test-configuration
 */
const baseURL = process.env.FRONTEND_URL || "http://localhost:3000";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "html",
  use: {
    baseURL,
    trace: "off",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: `npm run dev -- --port ${new URL(baseURL).port || "3000"}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120 * 1000,
  },
});

