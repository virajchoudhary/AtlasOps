"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { projectLiveIncident, recommendationQuery } = require("./live-incident.js");
assert.equal(globalThis.AtlasOpsLiveIncident.projectLiveIncident, projectLiveIncident);

const incident = { alert_names: ["HighCpuUsage"], services: ["paymentservice"] };
const lifecycle = Array.from({ length: 8 }, (_, index) => ["", "", `Reference ${index}`]);
const event = (ts, agent_role, action_type, policy_check = "allowed", extra = {}) =>
  ({ ts, agent_role, action_type, policy_check, ...extra });

test("unavailable audit is not presented as inactivity or verification", () => {
  const steps = projectLiveIncident(incident, [], false, lifecycle);
  assert.equal(steps[0].status, "Observed");
  assert.equal(steps[1].status, "Audit unavailable");
  assert.equal(steps[4].status, "Audit unavailable");
  assert.equal(steps[6].status, "Audit unavailable");
  assert.equal(steps[7].status, "Audit unavailable");
});

test("audited roles show activity, never phase completion", () => {
  const steps = projectLiveIncident(incident, [
    event(1, "triage", "tool_result", "allowed", { tool_name: "kubectl_get" }),
    event(2, "diagnosis", "tool_call", "allowed", { tool_name: "promql_query" }),
    event(3, "remediation", "tool_call", "allowed", { tool_name: "kubectl_rollout" }),
    event(4, "verifier", "tool_result")
  ], true, lifecycle);
  assert.equal(steps[1].status, "Activity logged");
  assert.equal(steps[2].status, "Activity logged");
  assert.equal(steps[5].status, "Tool activity");
  assert.equal(steps[6].status, "Activity logged");
  assert.match(steps[6].note, /does not provide an authoritative/);
  assert.equal(steps[7].status, "Audit entries");
  assert.ok(steps.every(step => !["Resolved", "Complete", "Verified"].includes(step.status)));
});

test("approval outcomes and policy blocks remain distinct", () => {
  const base = [
    event(1, "remediation", "approval_requested", "requires_approval"),
    event(3, "remediation", "tool_result", "blocked_by_policy", { tool_name: "kubectl_rollout" })
  ];
  for (const reason of ["timeout", "rejected"]) {
    const steps = projectLiveIncident(incident, [
      ...base, event(2, "remediation", "approval_decision", "approval_denied", { result_summary: reason })
    ], true, lifecycle);
    assert.equal(steps[4].status, reason);
    assert.equal(steps[4].tone, "blocked");
    assert.equal(steps[5].status, "Policy blocked");
  }
  const requested = projectLiveIncident(incident, base.slice(0, 1), true, lifecycle);
  assert.equal(requested[4].status, "Requested");
  assert.equal(requested[5].status, "Not reported");
});

test("advisory query uses only one observed alert and service", () => {
  assert.deepEqual(recommendationQuery(incident), {
    alert_name: "HighCpuUsage", service: "paymentservice", top_k: 3
  });
  assert.equal(recommendationQuery({ alert_names: [], services: ["paymentservice"] }), null);
  assert.equal(recommendationQuery({ alert_names: ["HighCpuUsage"], services: [] }), null);
  assert.equal(recommendationQuery({
    alert_names: ["HighCpuUsage", "PodCrashLooping"], services: ["paymentservice"]
  }), null);
  assert.equal(recommendationQuery({
    alert_names: ["HighCpuUsage"], services: ["paymentservice", "adservice"]
  }), null);
  assert.equal(recommendationQuery({
    alert_names: ["HighCpuUsage"], services: [" "]
  }), null);
  assert.equal(recommendationQuery({
    alert_names: ["A".repeat(101)], services: ["paymentservice"]
  }), null);
});
