import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)), "frontend");
const auditRoot = resolve(frontendRoot, "..", "..", "..", ".audit", "completion-20261015", "reference-reconstruction-local");
const capturePort = Number(process.env.REFERENCE_CAPTURE_PORT ?? 5187);
const baseURL = `http://127.0.0.1:${capturePort}`;
const captureOutputDirectory = process.env.REFERENCE_CAPTURE_OUTPUT_DIR
  ? resolve(process.env.REFERENCE_CAPTURE_OUTPUT_DIR)
  : auditRoot;

export default defineConfig({
  testDir: ".",
  testMatch: "homepage.capture.e2e.ts",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  outputDir: resolve(captureOutputDirectory, "playwright-results"),
  use: {
    channel: "msedge",
    baseURL,
    reducedMotion: "reduce",
    trace: "retain-on-failure",
  },
  webServer: {
    command: `node node_modules/vite/bin/vite.js --host 127.0.0.1 --port ${capturePort} --strictPort`,
    cwd: frontendRoot,
    url: `${baseURL}/reference.html`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
