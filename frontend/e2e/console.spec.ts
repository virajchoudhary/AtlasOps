import { expect, test } from "@playwright/test";

const routes = [
  ["Overview", "/", "AtlasOps"],
  ["Rehearsal", "/demo", "Incident workflow"],
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
  await expect(page.getByText("Governed Multi-Agent SRE Intelligence")).toHaveCount(0);
  await expect(page.getByText("Read-only research demo")).toBeVisible();
  await expect(page.getByText(/GAI\s*\+\s*RL/i)).toHaveCount(0);

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
    await expect(page.getByText(/GAI\s*\+\s*RL/i)).toHaveCount(0);
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
  await expect(page.getByText("Time to resolve: Unavailable", { exact: true })).toHaveCount(3);
  await expect(page.getByText("Time to resolve: Unavailable s", { exact: true })).toHaveCount(0);
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

test("all mobile routes scroll above the fixed navigation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  for (const [, path, heading] of routes) {
    await page.goto(`/#${path}`);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expect(page.getByText("Snapshot available", { exact: true })).toBeVisible();
    const main = page.locator(".main-content");
    await main.evaluate((element) => { element.scrollTop = element.scrollHeight; });
    const bounds = await page.evaluate(() => {
      const main = document.querySelector(".main-content")!;
      const last = document.querySelector(".page-stack")!.lastElementChild!;
      const nav = document.querySelector(".mobile-nav")!;
      return {
        mainBottom: main.getBoundingClientRect().bottom,
        lastBottom: last.getBoundingClientRect().bottom,
        navTop: nav.getBoundingClientRect().top,
        navPosition: getComputedStyle(nav).position,
      };
    });
    expect(bounds.navPosition).toBe("fixed");
    expect(bounds.mainBottom, path).toBeLessThanOrEqual(bounds.navTop + 1);
    expect(bounds.lastBottom, path).toBeLessThanOrEqual(bounds.navTop - 12);
  }
});

test("meaningful metadata stays readable and mobile architecture stays compact", async ({ page }) => {
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await expect(page.getByText("Snapshot available", { exact: true })).toBeVisible();
    await expect(page.locator(".architecture-flow")).toBeVisible();
    const overview = await page.evaluate(() => ({
      architectureHeight: document.querySelector(".architecture-flow")!.getBoundingClientRect().height,
      metadataSizes: [...document.querySelectorAll(
        ".architecture-kind, .architecture-agent-label, .architecture-caption, .hero-source, .api-indicator"
      )].map((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
    }));
    expect(overview.metadataSizes.every((size) => size >= 12)).toBe(true);
    if (width === 390) expect(overview.architectureHeight).toBeLessThan(540);
    await page.goto("/#/evidence");
    await expect(page.locator(".evidence-row").first()).toBeVisible();
    const sizes = await page.locator(".evidence-row").first().locator(
      ".evidence-row-title strong, .badge, .evidence-row-meta, .evidence-row-meta code"
    ).evaluateAll((elements) => elements.map((element) => Number.parseFloat(getComputedStyle(element).fontSize)));
    expect(sizes.every((size) => size >= 12)).toBe(true);
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

async function reachApproval(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "Start rehearsal", exact: true }).click();
  for (let i = 0; i < 3; i++) await page.getByRole("button", { name: "Next stage", exact: true }).click();
  await expect(page.getByRole("region", { name: "Simulated P1 decision" })).toBeVisible();
}

test("rehearsal pauses for approval, completes, inspects prior stages and exports labels", async ({ page }) => {
  const methods: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) methods.push(request.method());
  });
  await page.goto("/#/demo");
  await expect(page.getByText("SYNTHETIC / NON-LIVE / NON-EMPIRICAL", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Start rehearsal", exact: true }).click();
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect(page.getByRole("region", { name: "Simulated P1 decision" })).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole("button", { name: "Next stage", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeDisabled();
  await expect(page.locator(".demo-log li")).toHaveCount(4);
  await page.getByRole("button", { name: "Approve simulation", exact: true }).click();
  await page.getByRole("button", { name: "Next stage", exact: true }).click();
  await page.getByRole("button", { name: "Next stage", exact: true }).click();
  await expect(page.getByText("SIMULATED RECOVERY", { exact: true })).toBeVisible();
  await expect(page.locator(".demo-log li")).toHaveCount(7);
  await page.locator(".demo-stage").filter({ hasText: "Triage" }).click();
  await expect(page.locator(".demo-inspector h3")).toHaveText("Triage");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download simulated transcript" }).click();
  const download = await downloadPromise;
  const file = await download.path();
  const fs = await import("node:fs/promises");
  const exported = JSON.parse(await fs.readFile(file!, "utf-8"));
  expect(exported.experimental_evidence).toBe(false);
  expect(exported.real_tool_executed).toBe(false);
  expect(exported.classification).toContain("NON-EMPIRICAL");
  expect(exported.entries).toHaveLength(7);
  expect(methods.every((method) => method === "GET")).toBe(true);
  expect(errors).toEqual([]);
});

test("rejected and timed-out approval never reach remediation", async ({ page }) => {
  await page.goto("/#/demo");
  for (const decision of ["Reject", "Simulate timeout"]) {
    await reachApproval(page);
    await page.getByRole("button", { name: decision, exact: true }).click();
    await expect(page.getByText("SIMULATED REMEDIATION BLOCKED", { exact: true })).toBeVisible();
    await expect(page.locator(".demo-stage.is-skipped")).toHaveCount(2);
    await expect(page.locator(".demo-log li")).toHaveCount(5);
    await expect(page.locator(".demo-log")).not.toContainText("Fixture action accepted");
    await page.getByRole("button", { name: "Reset rehearsal" }).click();
  }
});

test("tool success with failed verification stays unresolved on a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#/demo");
  await page.getByLabel("Scenario", { exact: true }).selectOption("single_fault/sf-004");
  await page.getByLabel("Simulated verifier observation").selectOption("unhealthy");
  await reachApproval(page);
  await page.getByRole("button", { name: "Approve simulation", exact: true }).click();
  await page.getByRole("button", { name: "Next stage", exact: true }).click();
  await page.getByRole("button", { name: "Next stage", exact: true }).click();
  await expect(page.getByText("SIMULATED UNRESOLVED INCIDENT", { exact: true })).toBeVisible();
  await expect(page.locator(".demo-outcome")).toContainText("despite the simulated tool success");
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.getByRole("button", { name: "Reset rehearsal" }).click();
  await expect(page.getByText("No rehearsal started.", { exact: true })).toBeVisible();
});

test("missing rehearsal fixtures do not invent a replacement run", async ({ page }) => {
  await page.route("**/api/rehearsal", (route) => route.fulfill({ status: 503, body: "{}" }));
  await page.goto("/#/demo");
  await expect(page.getByRole("alert")).toContainText("Rehearsal fixtures unavailable");
  await expect(page.getByRole("button", { name: "Start rehearsal", exact: true })).toBeDisabled();
  await expect(page.locator(".demo-log")).toHaveCount(0);
});

test("manual pause stops playback and desktop/mobile rehearsal views are captured", async ({ page }, testInfo) => {
  await page.goto("/#/demo");
  await page.getByRole("button", { name: "Start rehearsal", exact: true }).click();
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await page.waitForTimeout(1700);
  await expect(page.locator(".demo-log li")).toHaveCount(1);
  for (let i = 0; i < 3; i++) await page.getByRole("button", { name: "Next stage", exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath("desktop-approval.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("mobile-approval.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test("live observations stay separate from the simulation and refresh failure is unavailable", async ({ page }) => {
  await page.route("**/api/live-status", (route) => route.fulfill({
    json: { enabled: true, observed_at: "2026-10-07T03:00:00Z", services: [
      { id: "coordinator", label: "Coordinator process", available: true, detail: "Process health only; model execution not tested" },
      { id: "inference", label: "Cached model server", available: false, detail: "Service unavailable" },
    ] },
  }));
  await page.goto("/#/demo");
  const live = page.getByRole("region", { name: "Live service observations" });
  await expect(live).toContainText("Responding");
  await expect(live).toContainText("Unavailable");
  await expect(live).toContainText("not agent execution or incident-resolution evidence");
  await expect(page.getByText("SYNTHETIC / NON-LIVE / NON-EMPIRICAL", { exact: true })).toBeVisible();
  await page.route("**/api/live-status", (route) => route.fulfill({ status: 503 }));
  await page.getByRole("button", { name: "Refresh live observations" }).click();
  await expect(live.getByRole("alert")).toContainText("No substitute health result");
  await expect(live.getByText("Responding", { exact: true })).toHaveCount(0);
});

test("governed capture displays unreserved startup failure without a recovery claim", async ({ page }) => {
  await page.route("**/api/incident-monitor", (route) => route.fulfill({ json: {
    enabled: true, available: true, experiment_id: "EXP-STAGE4-SF002-018",
    observed_at: "2026-10-07T06:00:00Z", status: "STARTUP_FAILED / NOT_RESERVED",
    process_running: false, reserved: false, attempt_state: null,
    failure: "bridge_transport_failure", gate_g4_pass: null, env_resolved: null,
    cleanup_verified_zero: null, approval: null, severity: null, services: [],
    phases: [{ id: "agents", label: "Agent workflow", observed: false }],
    actions: [], proposal: null, sources: [{ name: "Process exit", sha256: "a".repeat(64) }],
  } }));
  await page.goto("/#/demo");
  const monitor = page.getByRole("region", { name: "Governed incident monitor" });
  await expect(monitor).toContainText("STARTUP_FAILED / NOT_RESERVED");
  await expect(monitor).toContainText("bridge_transport_failure");
  await expect(monitor).toContainText("RECORDED / NOT A SIMULATION");
  await expect(monitor.locator("dl div").filter({ hasText: "Recorded G4 pass" }).locator("dd")).toHaveText("Unavailable");
  await expect(monitor.locator("dl div").filter({ hasText: "Attempt reserved" }).locator("dd")).toHaveText("No");
  await expect(monitor).toContainText("Not recorded");
  await expect(page.getByText("SYNTHETIC / NON-LIVE / NON-EMPIRICAL", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.route("**/api/incident-monitor", (route) => route.fulfill({ status: 503 }));
  await monitor.getByRole("button", { name: "Refresh incident capture" }).click();
  await expect(monitor.getByRole("alert")).toContainText("Previous observations are not shown");
  await expect(monitor.getByText("STARTUP_FAILED / NOT_RESERVED", { exact: true })).toHaveCount(0);
});

test("governed negative result remains negative despite tool success and cleanup", async ({ page }) => {
  await page.route("**/api/incident-monitor", (route) => route.fulfill({ json: {
    enabled: true, available: true, experiment_id: "EXP-STAGE4-SF002-018",
    observed_at: "2026-10-07T06:00:00Z", status: "RECORDED_NEGATIVE",
    process_running: false, reserved: true, attempt_state: "COMPLETED",
    failure: null, gate_g4_pass: false, env_resolved: false,
    cleanup_verified_zero: true, approval: "timeout", severity: "P1", services: ["paymentservice"],
    policy_block: "invalid_action",
    phases: [{ id: "agents", label: "Agent workflow", observed: true }],
    actions: [{ tool: "chaos_stop_experiment", target: "sf-002-paymentservice-cpu", namespace: "chaos-mesh", success: true }],
    proposal: null, sources: [],
  } }));
  await page.goto("/#/demo");
  const monitor = page.getByRole("region", { name: "Governed incident monitor" });
  await expect(monitor).toContainText("RECORDED_NEGATIVE");
  await expect(monitor).toContainText("Recorded policy block: invalid_action");
  await expect(monitor.locator("dl div").filter({ hasText: "Environment resolved" }).locator("dd")).toHaveText("No");
  await expect(monitor.locator("dl div").filter({ hasText: "Cleanup verified zero Chaos" }).locator("dd")).toHaveText("Yes");
  await expect(monitor.locator("dl div").filter({ hasText: "Recorded P1 decision" }).locator("dd")).toHaveText("timeout");
  await expect(monitor.getByRole("region", { name: "Recorded tool outcomes" })).toContainText("Tool success: Yes");
});

test("operator blocks exhausted launches and submits only the displayed exact action", async ({ page }, testInfo) => {
  const proposal = {
    token: "apr-test", action_digest: "c".repeat(64), incident_id: "incident-019", severity: "P1",
    action: { tool: "chaos_stop_experiment", arguments: { name: "sf-002-paymentservice-cpu", namespace: "chaos-mesh" } },
    operator_scope: { kube_context: "kind-atlasops-local", scenario_id: "single_fault/sf-002" },
  };
  let pending: typeof proposal[] = [];
  await page.route("**/api/operator", (route) => route.fulfill({ json: {
    enabled: true, csrf_token: "test-session", source_sha: "a".repeat(40),
    protocol_fingerprint: "b".repeat(64), experiment_id: "EXP-STAGE4-SF002-019",
    kube_context: "kind-atlasops-local", scenario_id: "single_fault/sf-002",
    readiness: { can_start: false, blockers: ["protocol_attempt_budget_exhausted"],
      attempts_used: 2, attempt_limit: 2, runtime_qualified: false },
    pending, channel_error: null, capture: null,
  } }));
  const decisions: unknown[] = [];
  await page.route("**/api/operator/decision", (route) => {
    decisions.push(route.request().postDataJSON());
    expect(route.request().headers()["x-atlasops-operator"]).toBe("test-session");
    pending = [];
    return route.fulfill({ json: { decision: "approved" } });
  });
  await page.goto("/#/demo");
  const panel = page.getByRole("region", { name: "Live incident controls" });
  await expect(panel).toContainText("protocol attempt budget exhausted");
  await expect(panel.getByRole("button", { name: "Start governed SF002 run" })).toBeDisabled();
  pending = [proposal];
  await panel.getByRole("button", { name: "Refresh operator status" }).click();
  await expect(panel.getByRole("region", { name: "Exact live action approval" })).toContainText("sf-002-paymentservice-cpu");
  await page.screenshot({ path: testInfo.outputPath("operator-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("operator-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await panel.getByRole("button", { name: "Approve exact action" }).click();
  expect(decisions).toEqual([{ token: proposal.token, action_digest: proposal.action_digest, decision: "approved" }]);
  await expect(panel.getByRole("region", { name: "Exact live action approval" })).toHaveCount(0);
});

test("an active runner with an unavailable channel never looks like an empty approval queue", async ({ page }) => {
  await page.route("**/api/operator", (route) => route.fulfill({ json: {
    enabled: true, csrf_token: "test-session", source_sha: "a".repeat(40),
    protocol_fingerprint: "b".repeat(64), experiment_id: "EXP-STAGE4-SF002-019",
    kube_context: "kind-atlasops-local", scenario_id: "single_fault/sf-002",
    readiness: { can_start: false, blockers: ["server_launch_already_used"],
      attempts_used: 1, attempt_limit: 2, runtime_qualified: false },
    pending: [], channel_error: "approval_channel_unavailable", events: null,
    capture: { status: "RUNNING", process_running: true, phases: [],
      env_resolved: null, gate_g4_pass: null, cleanup_verified_zero: null,
      policy_block: null, comms_status: null },
  } }));
  await page.goto("/#/demo");
  const panel = page.getByRole("region", { name: "Live incident controls" });
  await expect(panel.getByRole("alert")).toContainText("Approval channel unavailable");
  await expect(panel).toContainText("Agent activity unavailable");
  await expect(panel.getByRole("button", { name: "Approve exact action" })).toHaveCount(0);
  await expect(panel.locator("dl div").filter({ hasText: "Environment resolved" }).locator("dd")).toHaveText("Unavailable");
});

test("operator polling clears a transient status error after a successful read", async ({ page }) => {
  let failed = true;
  await page.route("**/api/operator", (route) => route.fulfill(failed ? { status: 503 } : { json: {
    enabled: true, csrf_token: "test-session", source_sha: "a".repeat(40),
    protocol_fingerprint: "b".repeat(64), experiment_id: "EXP-STAGE4-SF002-019",
    kube_context: "kind-atlasops-local", scenario_id: "single_fault/sf-002",
    readiness: { can_start: false, blockers: ["protocol_attempt_budget_exhausted"],
      attempts_used: 2, attempt_limit: 2, runtime_qualified: false },
    pending: [], channel_error: null, events: null, capture: null,
  } }));
  await page.goto("/#/demo");
  const panel = page.getByRole("region", { name: "Live incident controls" });
  await expect(panel.getByRole("alert")).toContainText("Operator status unavailable");
  failed = false;
  await expect(panel).toContainText("protocol attempt budget exhausted", { timeout: 10000 });
  await expect(panel.getByRole("alert")).toHaveCount(0);
});
