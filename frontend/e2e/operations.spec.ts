import { expect, test, type Page } from "@playwright/test";

const TEST_KEY = "fixture-only-browser-key";
const EXECUTION_ID = "a".repeat(32);
const ACTION_DIGEST = "f".repeat(64);
const SOURCE_SHA = "a".repeat(40);

function execution({
  executionId = EXECUTION_ID,
  incidentId = "incident-current",
  state = "running",
  phase = "remediation",
  record = {},
}: {
  executionId?: string;
  incidentId?: string;
  state?: string;
  phase?: string;
  record?: Record<string, unknown>;
} = {}) {
  return {
    incident_id: incidentId,
    execution_id: executionId,
    phase,
    state,
    updated_at: 1_800_000_000,
    record: {
      evidence_class: "OPERATIONAL_UNCERTIFIED",
      resolved: false,
      env_resolved: null,
      verification: { verification_status: "unknown", env_resolved: null },
      triage: { final: { severity: "P1", title: "Checkout latency" }, trajectory: ["alert received"] },
      diagnosis: { final: { root_cause: "fixture only" }, trajectory: [] },
      remediation: { final: { status: "prepared" }, trajectory: [] },
      comms: { final: { status: "deferred" }, trajectory: [] },
      replay_allowed: false,
      ...record,
    },
    integrity: { ok: true, events: 2, last_hash: "c".repeat(64) },
  };
}

function pendingApproval() {
  return {
    incident_id: "incident-current",
    severity: "P1",
    summary: "A prepared workload action requires an operator decision.",
    token: "apr-browser-fixture",
    created_at: 1_800_000_000,
    decision: "pending",
    approved_by: "",
    reason: "",
    action: {
      tool: "chaos_stop_experiment",
      arguments: { name: "checkout-fault", kind: "PodChaos", namespace: "chaos-mesh" },
      target: {
        server: "https://cluster.fixture.invalid",
        context: "kind-atlasops-local",
        api_version: "chaos-mesh.org/v1alpha1",
        kind: "PodChaos",
        namespace: "chaos-mesh",
        name: "checkout-fault",
        uid: "uid-chaos-browser-fixture",
        resource_version: "17",
      },
      verification_target: {
        server: "https://cluster.fixture.invalid",
        context: "kind-atlasops-local",
        api_version: "apps/v1",
        kind: "Deployment",
        namespace: "default",
        name: "checkoutservice",
        uid: "uid-browser-verification",
        resource_version: "23",
      },
      patch: {
        apiVersion: "v1",
        kind: "DeleteOptions",
        preconditions: { uid: "uid-chaos-browser-fixture", resourceVersion: "17" },
      },
    },
    action_digest: ACTION_DIGEST,
    action_summary: "chaos_stop_experiment arguments={...} target={...}",
    expires_at: Math.floor(Date.now() / 1000) + 600,
  };
}

function status(executions = [execution()]) {
  return {
    enabled: true,
    source_sha: SOURCE_SHA,
    certification_status: "NOT_CERTIFIED",
    operator_id: "operator-browser-fixture",
    approval_policy: "exact-action-all-mutations",
    executions,
    pending: [pendingApproval()],
    circuit_breaker: {
      tripped: false,
      active_incidents: 1,
      consecutive_failures: 0,
    },
  };
}

function verifiedExecution() {
  return execution({
    state: "completed",
    phase: "finished",
    record: {
      resolved: true,
      env_resolved: true,
      verification: { verification_status: "passed", env_resolved: true },
    },
  });
}

function evidence() {
  return {
    ...execution(),
    source_sha: SOURCE_SHA,
    certification_status: "NOT_CERTIFIED",
    events: [
      {
        execution_id: EXECUTION_ID,
        event: "admitted",
        phase: "queued",
        state: "running",
        record: { evidence_class: "OPERATIONAL_UNCERTIFIED" },
        ts: 1_800_000_000,
        audit_entry_hash: "d".repeat(64),
      },
      {
        execution_id: EXECUTION_ID,
        event: "verification_completed",
        phase: "verification",
        state: "running",
        record: {
          verification: { verification_status: "inconclusive", env_resolved: false },
          resolved: false,
        },
        ts: 1_800_000_002,
        audit_entry_hash: "e".repeat(64),
      },
    ],
    audit_integrity: { ok: true, entries: 2 },
    audit_scope: "last_1000_global_records",
    audit: [{ action_type: "execution_progress" }],
  };
}

async function installOperationsApi(
  page: Page,
  apiStatus = status(),
  evidenceResponse: { status: number; payload: unknown } = { status: 200, payload: evidence() },
) {
  const requests: Array<{ url: string; method: string; key: string | null }> = [];
  let decisionBody: string | null = null;
  await page.route("**/api/operations**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    requests.push({
      url: url.pathname,
      method: request.method(),
      key: request.headers()["x-atlasops-key"] ?? null,
    });
    if (url.pathname === "/api/operations" && request.method() === "GET") {
      await route.fulfill({ json: apiStatus });
      return;
    }
    if (url.pathname === "/api/operations/decision" && request.method() === "POST") {
      decisionBody = request.postData();
      await route.fulfill({ json: { ok: true, decision: "rejected" } });
      return;
    }
    if (url.pathname === `/api/operations/${EXECUTION_ID}/evidence` && request.method() === "GET") {
      await route.fulfill({ status: evidenceResponse.status, json: evidenceResponse.payload });
      return;
    }
    await route.fulfill({ status: 404, body: "Not found" });
  });
  return {
    requests,
    decisionBody: () => decisionBody,
  };
}

test("Operations navigation shows the exact action and downloads authenticated evidence", async ({ page }, testInfo) => {
  const api = await installOperationsApi(page);
  await page.goto("/");
  await page.getByRole("link", { name: "Operations" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Operations" })).toBeVisible();

  await page.getByLabel("Operator API key").fill(TEST_KEY);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Pending approvals" })).toBeVisible();
  await expect(page.getByText("NOT_CERTIFIED", { exact: true })).toBeVisible();
  await expect(page.getByText(ACTION_DIGEST)).toBeVisible();
  await expect(page.getByText(/resource_version/)).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "Verification target identity" })).toBeVisible();
  await expect(page.getByText(/uid-browser-verification/)).toBeVisible();
  await expect(page.getByText(/"DeleteOptions"/)).toBeVisible();

  await page.getByLabel("Rejection reason").fill("Observed target is outside the incident scope.");
  await page.getByRole("button", { name: "Reject action for incident-current" }).click();
  await expect(page.getByText("Rejection submitted.")).toBeVisible();
  expect(api.decisionBody()).toBe(JSON.stringify({
    token: "apr-browser-fixture",
    action_digest: ACTION_DIGEST,
    decision: "rejected",
    reason: "Observed target is outside the incident scope.",
  }));

  await page.getByRole("button", { name: /^incident-current/ }).click();
  await page.getByRole("button", { name: /Load full event history/ }).click();
  await expect(page.getByText("verification_completed")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("operations-desktop.png"), fullPage: true });
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download evidence JSON for incident-current" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe(`atlasops-${EXECUTION_ID}.json`);
  expect(api.requests.length).toBeGreaterThanOrEqual(3);
  expect(api.requests.every((request) => request.key === TEST_KEY)).toBe(true);
  expect(api.requests.every((request) => !request.url.includes(TEST_KEY))).toBe(true);
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
  expect(await page.evaluate(() => sessionStorage.length)).toBe(0);
});

test("Operations withdraws a verified claim when full evidence is missing after an unchanged refresh", async ({ page }, testInfo) => {
  const api = await installOperationsApi(
    page,
    status([verifiedExecution()]),
    { status: 404, payload: { detail: "Evidence record not found" } },
  );
  await page.goto("/#/operations");
  await page.getByLabel("Operator API key").fill(TEST_KEY);
  await page.getByRole("button", { name: "Connect", exact: true }).click();

  await page.getByRole("button", { name: /^incident-current/ }).click();
  const detail = page.getByRole("region", { name: "incident-current" });
  await expect(detail.getByText("Resolved and verified")).toBeVisible();
  await detail.getByRole("button", { name: /Load full event history/ }).click();
  await expect(detail.getByRole("alert")).toHaveText(/HTTP 404/);
  await expect(detail.getByText("Evidence unavailable; verification unconfirmed")).toBeVisible();
  await expect(page.getByText("Resolved and verified")).toHaveCount(0);

  await page.getByRole("button", { name: "Refresh operations" }).click();
  await expect.poll(() => api.requests.filter(
    (request) => request.url === "/api/operations" && request.method === "GET",
  ).length).toBeGreaterThanOrEqual(2);
  await expect(detail.getByText("Evidence unavailable; verification unconfirmed")).toBeVisible();
  await expect(page.getByText("Resolved and verified")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("operations-missing-evidence.png"), fullPage: true });
});

test("Operations blocks rejected or timed-out approval outcomes despite positive verifier fields", async ({ page }, testInfo) => {
  const rejected = execution({
    executionId: "b".repeat(32),
    incidentId: "incident-rejected",
    state: "completed",
    phase: "finished",
    record: {
      resolved: true,
      env_resolved: true,
      verification: { verification_status: "passed", env_resolved: true },
      approval: { decision: "rejected" },
    },
  });
  const timedOut = execution({
    executionId: "c".repeat(32),
    incidentId: "incident-timed-out",
    state: "completed",
    phase: "finished",
    record: {
      resolved: true,
      env_resolved: true,
      verification: { verification_status: "passed", env_resolved: true },
      remediation: { final: { status: "approval_timeout" } },
    },
  });
  await installOperationsApi(page, status([rejected, timedOut]));
  await page.goto("/#/operations");
  await page.getByLabel("Operator API key").fill(TEST_KEY);
  await page.getByRole("button", { name: "Connect", exact: true }).click();

  await page.getByRole("button", { name: /^incident-rejected/ }).click();
  const rejectedDetail = page.getByRole("region", { name: "incident-rejected" });
  await expect(rejectedDetail.getByText("Approval rejected")).toBeVisible();
  await expect(rejectedDetail.getByText("Resolved and verified")).toHaveCount(0);

  await page.getByRole("button", { name: /^incident-timed-out/ }).click();
  const timeoutDetail = page.getByRole("region", { name: "incident-timed-out" });
  await expect(timeoutDetail.getByText("Approval timed out")).toBeVisible();
  await expect(timeoutDetail.getByText("Resolved and verified")).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("operations-approval-terminal-states.png"),
    fullPage: true,
  });
});

test("Operations fits a mobile viewport without horizontal page overflow", async ({ page }, testInfo) => {
  const api = await installOperationsApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#/operations");
  await page.getByLabel("Operator API key").fill(TEST_KEY);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Operations" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Pending approvals" })).toBeVisible();

  await page.screenshot({ path: testInfo.outputPath("operations-mobile.png"), fullPage: true });
  const pauseBounds = await page.getByRole("button", { name: "Pause future tool execution" }).evaluate((button) => {
    const rect = button.getBoundingClientRect();
    return {
      clientWidth: button.clientWidth,
      scrollWidth: button.scrollWidth,
      right: rect.right,
      viewportWidth: document.documentElement.clientWidth,
    };
  });
  expect(pauseBounds.scrollWidth).toBeLessThanOrEqual(pauseBounds.clientWidth + 1);
  expect(pauseBounds.right, JSON.stringify(pauseBounds)).toBeLessThanOrEqual(pauseBounds.viewportWidth + 1);

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
  expect(api.requests.every((request) => request.key === TEST_KEY)).toBe(true);
});

test("Operations preserves permit-invalid and uncertain terminal labels", async ({ page }) => {
  const resolvedRecord = {
    resolved: true,
    env_resolved: true,
    verification: { verification_status: "passed", env_resolved: true },
  };
  const permitInvalid = execution({
    executionId: "c".repeat(32),
    incidentId: "incident-permit-invalid",
    state: "completed",
    phase: "finished",
    record: {
      ...resolvedRecord,
      remediation: { final: { status: "approval_permit_invalid" } },
    },
  });
  const uncertain = execution({
    executionId: "d".repeat(32),
    incidentId: "incident-uncertain",
    state: "uncertain",
    phase: "finished",
    record: {
      ...resolvedRecord,
      failure_category: "mutation_uncertain",
      replay_allowed: false,
    },
  });
  await installOperationsApi(page, status([permitInvalid, uncertain]));
  await page.goto("/#/operations");
  await page.getByLabel("Operator API key").fill(TEST_KEY);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Operations" })).toBeVisible();

  await page.getByRole("button", { name: /incident-permit-invalid/ }).click();
  const permitInvalidDetail = page.getByRole("region", { name: "incident-permit-invalid" });
  await expect(permitInvalidDetail.getByText("Approval permit invalid")).toBeVisible();
  await expect(permitInvalidDetail).toContainText(/approval_permit_invalid/);
  await expect(page.getByText("Resolved and verified")).toHaveCount(0);

  await page.getByRole("button", { name: /incident-uncertain/ }).click();
  const uncertainDetail = page.getByRole("region", { name: "incident-uncertain" });
  await expect(uncertainDetail.getByText("Execution outcome uncertain")).toBeVisible();
  await expect(uncertainDetail.getByText("No", { exact: true })).toBeVisible();
  await expect(uncertainDetail.getByText(/Replay is blocked/)).toBeVisible();
  await expect(page.getByText("Resolved and verified")).toHaveCount(0);
});

test("Operations shows an explicit disabled state when its API is unavailable", async ({ page }) => {
  await page.route("**/api/operations**", async (route) => {
    await route.fulfill({
      status: 503,
      json: { detail: "Operational controls are not enabled" },
    });
  });
  await page.goto("/#/operations");
  await page.getByLabel("Operator API key").fill(TEST_KEY);
  await page.getByRole("button", { name: "Connect", exact: true }).click();

  await expect(page.getByRole("alert")).toHaveText(/operational mode or operator identity is not configured/i);
  await expect(page.getByText("Resolved and verified")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Approve exact action/ })).toHaveCount(0);
});
