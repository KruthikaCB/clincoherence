// Test/build-only preloader. Never load project dotenv files or contact a backend.
const envPath = require.resolve("@next/env");
const original = require(envPath);
process.env.BACKEND_ORIGIN = "http://127.0.0.1:1";
process.env.NEXT_TELEMETRY_DISABLED = "1";
require.cache[envPath].exports = {
  ...original,
  loadEnvConfig: () => ({ combinedEnv: process.env, parsedEnv: {}, loadedEnvFiles: [] }),
  processEnv: () => process.env,
};
