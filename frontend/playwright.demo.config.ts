import { defineConfig, devices } from "@playwright/test";

// All selected suites mock /api/v1/**. The launcher also pins rewrites to port 1.
export default defineConfig({
  testDir: "./tests",
  testMatch: ["medication-suitability.spec.ts", "clinical-summary.spec.ts", "clinical-history.spec.ts", "clinical-corrections.spec.ts", "verified-records.spec.ts"],
  workers: 1, retries: 0, maxFailures: 1, forbidOnly: true, reporter: "list",
  use: { baseURL: "http://127.0.0.1:3107", trace: "off", serviceWorkers: "block" },
  // Use the already installed browser; no browser/package download is needed.
  projects: [{ name: "chrome", use: { ...devices["Desktop Chrome"], channel: "chrome" } }],
  webServer: {
    command: "node --require ./scripts/offline-env.cjs node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3107",
    url: "http://127.0.0.1:3107", reuseExistingServer: false, timeout: 120000,
  },
});
