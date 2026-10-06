import { expect, test } from "@playwright/test";

const routes = [
  ["Overview", "/", "AtlasOps"],
  ["Agents", "/agents", "Four roles. Clear authority boundaries."],
  ["Models", "/models", "Model lineage"],
  ["Evaluations", "/evaluations", "Evaluations"],
  ["Incidents", "/incidents", "Incident chronology"],
  ["Evidence", "/evidence", "Provenance before presentation."],
  ["Runbooks", "/runbooks", "Runbook catalog"],
  ["System", "/system", "A local, read-only presentation surface."],
] as const;

test("overview shows API inventory and stays distinct from certification", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "AtlasOps" })).toBeVisible();
  await expect(page.getByText("Governed Multi-Agent SRE Intelligence")).toBeVisible();
  await expect(page.getByText("Read-only research demo")).toBeVisible();
  await expect(page.getByText("GAI + RL")).toBeVisible();

  const tools = page.locator(".metric-tile").filter({ hasText: "Registered tool wrappers" });
  await expect(tools.locator(".metric-value")).toHaveText("24");
  await expect(tools).toContainText("Role-exposed: 19");

  const scenarios = page.locator(".metric-tile").filter({ hasText: "Frozen scenarios" });
  await expect(scenarios.locator(".metric-value")).toHaveText("28");
  await expect(page.getByText("NOT_CERTIFIED")).toBeVisible();
  await expect(page.getByText("READY_FOR_REVIEW")).toBeVisible();
});

test("every primary route is reachable through the hash router", async ({ page }) => {
  for (const [label, path, heading] of routes) {
    await page.goto(`/#${path}`);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
  }
});

test("matched comparison and controlled RL findings display canonical API values", async ({ page }) => {
  await page.goto("/#/evaluations");
  await expect(page.locator(".validation-metric").filter({ hasText: "Base F1" }).locator("strong"))
    .toHaveText("0.16875");
  await expect(page.locator(".validation-metric").filter({ hasText: "SFT F1" }).locator("strong"))
    .toHaveText("0.15935");
  await expect(page.locator(".validation-metric").filter({ hasText: "Delta" }).locator("strong"))
    .toHaveText("-0.00940");
  await expect(page.getByText("No diagnostic improvement observed")).toBeVisible();
  const schemaCounts = page.locator(".schema-compare div strong");
  await expect(schemaCounts).toHaveCount(2);
  await expect(schemaCounts.nth(0)).toHaveText("6 / 6");
  await expect(schemaCounts.nth(1)).toHaveText("6 / 6");

  const rl = page.locator(".result-section--rl");
  await expect(rl.locator(".rl-outcome-number strong")).toHaveText("4");
  await expect(rl.locator(".rl-fact").filter({ hasText: "Malformed or blocked" }).locator("strong")).toHaveText("4");
  await expect(rl.locator(".rl-fact").filter({ hasText: "Reward per blocked action" }).locator("strong")).toHaveText("-1");
  await expect(rl.locator(".rl-fact").filter({ hasText: /^Reward-driven advantage groups/ }).locator("strong")).toHaveText("0");
  await expect(rl.locator(".rl-fact").filter({ hasText: "Groups with zero reward-driven advantages" }).locator("strong")).toHaveText("2");
  await expect(rl).toContainText("No acceptable SFT+GRPO checkpoint was saved.");

  const aligned = page.locator(".result-section--aligned");
  await expect(aligned.locator(".admissibility strong")).toHaveText("0/8");
  await expect(aligned.locator(".rl-fact").filter({ hasText: "Optimizer steps" }).locator("strong")).toHaveText("0");
  await expect(aligned.locator(".rl-fact").filter({ hasText: "LoRA tensors checked" }).locator("strong")).toHaveText("392");
  await expect(aligned.locator(".rl-fact").filter({ hasText: "Hashes unchanged" }).locator("strong")).toHaveText("Yes");
  await aligned.getByRole("button", { name: "Interpretation limits" }).click();
  await expect(aligned).toContainText("does not estimate population probability");
});

test("G4 shows 015 through the latest completed negative 017", async ({ page }) => {
  await page.goto("/#/incidents");
  const rows = page.locator(".timeline-item");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText("015");
  await expect(rows.nth(0)).toContainText("INCONCLUSIVE");
  await expect(rows.nth(1)).toContainText("016");
  await expect(rows.nth(1)).toContainText("PRE-FAULT ABORT / NON-RESULT");
  await expect(rows.nth(2)).toContainText("017");
  await expect(rows.nth(2)).toContainText("COMPLETED NEGATIVE");
  await expect(rows.nth(2)).toContainText("Last completed attempt");
  await expect(page.locator(".gate-status")).toContainText("NOT_PASSED");
});

test("evidence filters use server tags and external rows hide raw references", async ({ page }) => {
  await page.goto("/#/evidence");
  const rows = page.locator(".evidence-row");
  await expect(rows.first()).toBeVisible();
  await page.getByRole("button", { name: "Historical", exact: true }).click();
  await expect(rows).toHaveCount(3);
  await expect(rows.first()).toContainText("HISTORICAL / NON-EMPIRICAL");

  await page.getByRole("button", { name: "External", exact: true }).click();
  await expect(rows).toHaveCount(2);
  const externalText = (await rows.allTextContents()).join(" ");
  expect(externalText).not.toMatch(/artifacts\/evidence\/stage4|[a-f0-9]{64}/i);
  await expect(rows.first()).toContainText("external");

  await page.getByRole("button", { name: "All", exact: true }).click();
  await rows.first().getByRole("button", { name: "Source details" }).click();
  await expect(rows.first().getByText("SHA-256", { exact: true })).toBeVisible();
});

test("narrow navigation closes with Escape and outside click", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const more = page.getByRole("button", { name: "More" });
  await more.click();
  await expect(page.getByRole("region", { name: "Additional navigation" })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("region", { name: "Additional navigation" }).getByRole("link").first()).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("region", { name: "Additional navigation" })).toHaveCount(0);
  await more.click();
  await page.locator(".topbar").click();
  await expect(page.getByRole("region", { name: "Additional navigation" })).toHaveCount(0);
});

test("the layout has no horizontal overflow at supported widths", async ({ page }) => {
  for (const width of [1440, 1280, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: 960 });
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1, name: "AtlasOps" })).toBeVisible();
    const overflows = await page.evaluate(() =>
      document.documentElement.scrollWidth > window.innerWidth
      || document.body.scrollWidth > window.innerWidth
    );
    expect(overflows, `horizontal overflow at ${width}px`).toBe(false);
  }
});

test("reduced-motion preference is honored without losing page content", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "AtlasOps" })).toBeVisible();
  await expect.poll(() => page.evaluate(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  )).toBe(true);
  const transitionMs = await page.locator(".architecture-agent-cluster").evaluate((element) => {
    const value = getComputedStyle(element).transitionDuration;
    return value.endsWith("ms") ? Number.parseFloat(value) : Number.parseFloat(value) * 1000;
  });
  expect(transitionMs).toBeLessThan(0.1);
});

test("the presentation makes no non-GET API requests", async ({ page }) => {
  const apiMethods: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) apiMethods.push(request.method());
  });
  await page.goto("/");
  await page.goto("/#/incidents");
  await page.getByRole("button", { name: /Browse preserved records/ }).click();
  await page.locator(".historical-attempt .accordion-trigger").first().click();
  await expect(page.getByText("Reading this historical record…")).toHaveCount(0);
  expect(apiMethods.length).toBeGreaterThan(0);
  expect(apiMethods.every((method) => method === "GET")).toBe(true);
});
