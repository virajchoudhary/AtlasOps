import { expect, test } from "@playwright/test";

const KEY = "test-placeholder-operations-browser-key";

test("Operations uses the real mounted authenticated handlers without a live executor", async ({
  page,
  request,
}, testInfo) => {
  const unauthorized = await request.get("/api/operations");
  expect(unauthorized.status()).toBe(401);

  await page.goto("/#/operations");
  await page.getByLabel("Operator API key").fill(KEY);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Pending approvals" })).toBeVisible();

  const connectionState = page.locator(".operations-connection-state");
  await expect(connectionState).toContainText("Connected");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(connectionState).toContainText("Connected");
  await expect(page.getByRole("heading", { name: "Pending approvals" })).toBeVisible();

  await page.route("**/api/operations", (route) =>
    route.fulfill({ status: 503, json: { detail: "Disposable API outage" } }),
  );
  await page.getByRole("button", { name: "Refresh operations" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.unroute("**/api/operations");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(connectionState).toContainText("Connected");
  await expect(page.getByRole("heading", { name: "Pending approvals" })).toBeVisible();

  const status = await request.get("/api/operations", {
    headers: { "X-AtlasOps-Key": KEY },
  });
  expect(status.status()).toBe(200);
  const snapshot = await status.json();
  expect(snapshot.source_sha).toMatch(/^[0-9a-f]{40}$/);
  expect(snapshot.certification_status).toBe("NOT_CERTIFIED");
  expect(snapshot.pending).toHaveLength(1);
  const pending = snapshot.pending[0];

  const stale = await request.post("/api/operations/decision", {
    headers: { "X-AtlasOps-Key": KEY },
    data: {
      token: pending.token,
      action_digest: "0".repeat(64),
      decision: "approved",
    },
  });
  expect(stale.status()).toBe(400);
  expect((await stale.json()).error).toBe("action_digest_mismatch");

  const hostile = await request.post("/api/operations/decision", {
    headers: { "X-AtlasOps-Key": KEY, Origin: "https://hostile.invalid" },
    data: {
      token: pending.token,
      action_digest: pending.action_digest,
      decision: "approved",
    },
  });
  expect(hostile.status()).toBe(403);

  const card = page.locator(".operations-approval").filter({ hasText: "fixture-pending" });
  await expect(card).toContainText("argocd_rollback");
  await expect(card).toContainText("browser-fixture-application");
  await expect(card).toContainText("browser-fixture-frontend");
  const decisionRequest = page.waitForRequest((candidate) =>
    candidate.url().endsWith("/api/operations/decision") && candidate.method() === "POST",
  );
  const decisionResponse = page.waitForResponse((candidate) =>
    candidate.url().endsWith("/api/operations/decision")
      && candidate.request().method() === "POST",
  );
  await card.getByRole("button", { name: /Approve exact action/ }).click();
  const [sent, response] = await Promise.all([decisionRequest, decisionResponse]);
  expect(response.status()).toBe(200);
  expect(sent.postDataJSON()).toEqual({
    token: pending.token,
    action_digest: pending.action_digest,
    decision: "approved",
  });
  expect(sent.headers()["x-atlasops-key"]).toBe(KEY);
  await expect(page.getByText("No pending operational approvals.")).toBeVisible();

  await page.getByRole("button", { name: /^fixture-rejected/ }).click();
  const rejected = page.getByRole("region", { name: "fixture-rejected" });
  await expect(rejected).toContainText(/rejected/i);
  await expect(page.getByText("Resolved and verified", { exact: true })).toHaveCount(0);
  await rejected.getByRole("button", { name: /Load full event history/ }).click();
  await expect(rejected.getByText("approval_rejected", { exact: true })).toBeVisible();
  const downloadEvent = page.waitForEvent("download");
  await rejected.getByRole("button", {
    name: "Download evidence JSON for fixture-rejected",
  }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe(`atlasops-${"b".repeat(32)}.json`);

  const evidence = await request.get(`/api/operations/${"b".repeat(32)}/evidence`, {
    headers: { "X-AtlasOps-Key": KEY },
  });
  expect(evidence.status()).toBe(200);
  const exported = await evidence.text();
  expect(exported).not.toContain(KEY);
  expect(exported).not.toContain(pending.token);
  const payload = JSON.parse(exported);
  expect(payload.record.resolved).toBe(false);
  expect(payload.integrity.ok).toBe(true);
  expect(payload.audit_integrity.ok).toBe(true);
  expect(payload.audit_integrity.entries).toBeGreaterThan(0);
  expect(payload.source_sha).toBe(snapshot.source_sha);

  const pauseResponse = page.waitForResponse((candidate) =>
    candidate.url().endsWith("/api/operations/pause"),
  );
  await page.getByRole("button", { name: "Pause future tool execution" }).click();
  const paused = await pauseResponse;
  expect(paused.status()).toBe(200);
  expect((await paused.json()).tripped).toBe(true);
  await expect(page.getByText("Future tool execution is paused.", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  await page.screenshot({ path: testInfo.outputPath("operations-real-api-desktop.png"), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() =>
    document.documentElement.scrollWidth <= window.innerWidth + 1,
  )).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("operations-real-api-mobile.png"), fullPage: true });
});
