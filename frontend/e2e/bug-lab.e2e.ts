import { expect, test } from "@playwright/test";

const scenarios = [
  ["JavaScript crash", "app.js"],
  ["Broken stylesheet link", "index.html"],
  ["Truncated product data", "products.json"],
  ["Prices show NaN", "products.json"],
  ["Broken image paths", "products.json"],
  ["Add to cart does nothing", "app.js"],
  ["Layout overflows the screen", "styles.css"],
  ["Slow product API", "config.json"],
] as const;

test.beforeEach(async ({ page, request }) => {
  const reset = await request.post("/lab/api/reset", { headers: { "X-Bug-Lab": "1" } });
  expect(reset.ok()).toBe(true);
  await request.post("/lab/api/settings", {
    headers: { "X-Bug-Lab": "1" }, data: { require_approval_all: true },
  });
  await page.goto("/");
  await expect(page.locator("#catalog .bug")).toHaveCount(8);
});

for (const [name, file] of scenarios) {
  test(`${name} is detected by the real monitor and verified after exact approval`, async ({ page, request }) => {
    await page.locator("#catalog .bug").filter({ has: page.getByText(name, { exact: true }) })
      .getByRole("button", { name: "Inject", exact: true }).click();
    await expect(page.getByRole("button", { name: "Approve and apply" })).toBeVisible({ timeout: 25_000 });
    await expect(page.locator("#incident .diff").last()).toContainText(file);
    const before = await request.get("/lab/api/state").then(response => response.json());
    expect(before.site.changed_files).toContain(file);
    expect(before.incident.approval.digest).toMatch(/^[a-f0-9]{64}$/);
    const wrong = await request.post("/lab/api/approval", {
      headers: { "X-Bug-Lab": "1" },
      data: { incident_id: before.incident.id, digest: "0".repeat(64), decision: "approve" },
    });
    expect(wrong.status()).toBe(409);
    await page.getByRole("button", { name: "Approve and apply" }).click();
    await expect(page.locator("#incident .who")).toHaveText("Resolved and verified", { timeout: 20_000 });
    const after = await request.get("/lab/api/state").then(response => response.json());
    expect(after.site.changed_files).toEqual([]);
    expect(after.incident.resolved_by).toBe("Rule engine (no model)");
    expect(after.incident.approval.decision).toBe("approved");
    expect(after.checks.browser_current).toBe(true);
    expect(after.incident.attempts.at(-1).verdict).toBe("verified");
    expect(after.incident.events.map((event: { stage: string }) => event.stage))
      .toEqual(expect.arrayContaining(["monitor", "triage", "diagnosis", "propose", "approval", "apply", "verification", "comms"]));
    const replay = await request.post("/lab/api/approval", {
      headers: { "X-Bug-Lab": "1" },
      data: { incident_id: before.incident.id, digest: before.incident.approval.digest, decision: "approve" },
    });
    expect(replay.status()).toBe(409);
    const exported = await request.get("/lab/api/evidence");
    expect(exported.headers()["content-disposition"]).toContain("attachment");
    const records = (await exported.text()).trim().split("\n")
      .map(line => JSON.parse(line));
    expect(records.at(-1).id).toBe(after.incident.id);
    expect(records.at(-1).attempts.at(-1).verification.verdict).toBe("verified");
    expect(records.at(-1).evidence_classification).toBe("LOCAL_DEMONSTRATION_NOT_SCIENTIFIC");
  });
}

for (const decision of ["reject", "timeout"]) {
  test(`${decision} leaves the exact broken store unchanged`, async ({ page, request }) => {
    await page.locator("#catalog .bug").filter({ has: page.getByText("Prices show NaN", { exact: true }) })
      .getByRole("button", { name: "Inject", exact: true }).click();
    await expect(page.getByRole("button", { name: "Reject", exact: true })).toBeVisible();
    const before = await request.get("/shop/products.json").then(response => response.text());
    if (decision === "reject") await page.getByRole("button", { name: "Reject", exact: true }).click();
    await expect(page.locator("#incident .who")).toHaveText("Escalated to you", { timeout: 10_000 });
    expect(await request.get("/shop/products.json").then(response => response.text())).toBe(before);
    const state = await request.get("/lab/api/state").then(response => response.json());
    expect(state.incident.approval.decision).toBe(decision === "reject" ? "rejected" : "timeout");
    expect(state.site.monitoring_paused).toBe(true);
    expect(state.incident.events.some((event: { stage: string }) => event.stage === "apply")).toBe(false);
    const late = await request.post("/lab/api/approval", {
      headers: { "X-Bug-Lab": "1" },
      data: { incident_id: state.incident.id, digest: state.incident.approval.digest, decision: "approve" },
    });
    expect(late.status()).toBe(409);
  });
}

test("Control Room and Store render at desktop and mobile sizes", async ({ page }, testInfo) => {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await expect(page.getByRole("heading", { name: "AtlasOps Bug Lab" })).toBeVisible();
    await expect.poll(() => page.evaluate(() =>
      document.documentElement.scrollWidth <= document.documentElement.clientWidth + 2)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`control-${viewport.width}.png`), fullPage: true });
    const store = page.frameLocator("#preview");
    await expect(store.locator("#product-grid .card")).toHaveCount(8);
    await expect(store.locator("#atlasops-monitor-badge")).toHaveText("AtlasOps monitor: all checks pass");
  }
});

test("Control Room identifies stale point-in-time telemetry", async ({ page }) => {
  await page.route("**/lab/api/state", async route => {
    const response = await route.fetch();
    const state = await response.json();
    state.checks.browser_age_s = 61;
    state.checks.browser_stale = true;
    await route.fulfill({ response, json: state });
  });
  await expect(page.locator("#browser-age")).toContainText("Sampled 61 s ago.");
  await expect(page.locator("#browser-age")).toContainText("Stale telemetry");
});
