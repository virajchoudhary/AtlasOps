import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "ring-canvas.e2e.ts",
  fullyParallel: false,
  reporter: "list",
  outputDir: "./results",
  use: {
    channel: "chrome",
    baseURL: "http://127.0.0.1:7866",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
  webServer: {
    command: "node node_modules/vite/bin/vite.js --config src/reference/tests/vite.config.ts --host 127.0.0.1 --port 7866 --strictPort",
    cwd: "../../..",
    url: "http://127.0.0.1:7866/fixture.html",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
