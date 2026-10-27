import { defineConfig } from "@playwright/test";

const browserChannel = process.env.PLAYWRIGHT_BROWSER_CHANNEL
  ?? (process.platform === "win32" ? "msedge" : undefined);

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/bug-lab.e2e.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  reporter: "list",
  outputDir: "test-results/bug-lab",
  use: {
    baseURL: "http://127.0.0.1:8793",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 1000 },
    ...(browserChannel ? { launchOptions: { channel: browserChannel } } : {}),
  },
  webServer: {
    command: "python -m tests.bug_lab_process --port 8793",
    cwd: "..",
    url: "http://127.0.0.1:8793/lab/api/state",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
