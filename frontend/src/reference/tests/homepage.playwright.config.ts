import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)), "frontend");
const auditRoot = resolve(frontendRoot, "..", "..", "..", ".audit", "completion-20261015", "reference-reconstruction-local");

export default defineConfig({
  testDir: ".",
  testMatch: "homepage.capture.e2e.ts",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  outputDir: resolve(auditRoot, "playwright-results"),
  use: {
    channel: "msedge",
    baseURL: "http://127.0.0.1:5187",
    reducedMotion: "reduce",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5187 --strictPort",
    cwd: frontendRoot,
    url: "http://127.0.0.1:5187/reference.html",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
