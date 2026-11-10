import { defineConfig, devices } from "@playwright/test";

// Installed Playwright supports suppressing automatic DOM snapshots on failures.
process.env.PLAYWRIGHT_NO_COPY_PROMPT = "1";

// Only the guarded launcher owns servers. Never discover these tests in the mock suite.
if (!/^[0-9a-f]{32}$/.test(process.env.CC_LIVE_RUN ?? "") ||
    !process.env.CC_LIVE_PYTHON || !process.env.CC_LIVE_BACKEND) {
  throw new Error("Use the guarded live launcher; direct live execution is forbidden.");
}

export default defineConfig({
  testDir: "./live-tests",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  timeout: 180_000,
  expect: { timeout: 20_000 },
  reporter: "line",
  preserveOutput: "never",
  // No webServer: the launcher rejects occupied ports and owns both server trees.
  use: {
    baseURL: "http://127.0.0.1:3010",
    trace: "off",
    screenshot: "off",
    video: "off",
    storageState: undefined,
  },
  projects: [{ name: "live-chromium", use: { ...devices["Desktop Chrome"] } }],
});
