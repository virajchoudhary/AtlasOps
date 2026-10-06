import { defineConfig } from "@playwright/test";

const browserChannel = process.env.PLAYWRIGHT_BROWSER_CHANNEL
  ?? (process.platform === "win32" ? "msedge" : undefined);

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:7863",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 1000 },
    ...(browserChannel ? { launchOptions: { channel: browserChannel } } : {}),
  },
  webServer: {
    command: "python -m demo.launcher --port 7863",
    cwd: "..",
    url: "http://127.0.0.1:7863/api/catalog",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
