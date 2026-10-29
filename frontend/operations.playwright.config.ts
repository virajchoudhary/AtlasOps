import { defineConfig } from "@playwright/test";

const browserChannel = process.env.PLAYWRIGHT_BROWSER_CHANNEL
  ?? (process.platform === "win32" ? "msedge" : undefined);

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/operations-api.e2e.ts",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  outputDir: "../.audit/operations-api-browser/test-results",
  use: {
    baseURL: "http://127.0.0.1:7873",
    trace: "off",
    viewport: { width: 1440, height: 1000 },
    ...(browserChannel ? { launchOptions: { channel: browserChannel } } : {}),
  },
  webServer: {
    command: "python -B -m tests.operations_browser_process --port 7873",
    cwd: "..",
    url: "http://127.0.0.1:7873/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
