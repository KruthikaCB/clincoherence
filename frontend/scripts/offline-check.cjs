// Fixed commands only; child processes receive no inherited application secrets.
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const root = path.resolve(__dirname, "..");
const commands = {
  types: ["node_modules/typescript/bin/tsc", "--noEmit", "--incremental", "false"],
  build: ["node_modules/next/dist/bin/next", "build"],
  test: ["node_modules/@playwright/test/cli.js", "test", "--config=playwright.demo.config.ts"],
  "test-panels": ["node_modules/@playwright/test/cli.js", "test", "--config=playwright.demo.config.ts", "medication-suitability.spec.ts", "verified-records.spec.ts"],
};
const command = commands[process.argv[2]];
if (!command || process.argv.length !== 3) throw new Error("Use: node scripts/offline-check.cjs types|build|test|test-panels");
const env = {};
for (const key of ["PATH", "Path", "SystemRoot", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "COMSPEC", "PATHEXT"]) {
  if (process.env[key] !== undefined) env[key] = process.env[key];
}
env.CI = "1";
env.NEXT_TELEMETRY_DISABLED = "1";
env.BACKEND_ORIGIN = "http://127.0.0.1:1";
env.NODE_OPTIONS = `--require="${path.join(__dirname, "offline-env.cjs").replace(/\\/g, "/")}"`;
const result = spawnSync(process.execPath, command, { cwd: root, env, stdio: "inherit", shell: false });
if (result.error) throw result.error;
process.exit(result.status ?? 1);
