import { describe, expect, it } from "vitest";
import { initialWorkflow, outcome, transcript, workflowReducer, type RehearsalScenario } from "./workflow";

const scenario: RehearsalScenario = {
  id: "fixture", title: "Fixture", service: "paymentservice", alert: "HighCpuUsage",
  severity: "P1", approval_mode: "approve", diagnosis: "CPU stress",
  unhealthy_observation: "CPU high", healthy_observation: "CPU normal",
  proposal: { tool: "chaos_stop_experiment", arguments: { name: "fixture" } },
  source: "fixture", sha256: "fixture", checkout_sha256: "fixture",
};
function pending(verifierHealthy = true) {
  let state = workflowReducer(initialWorkflow, { type: "start", scenario, verifierHealthy });
  for (let i = 0; i < 3; i++) state = workflowReducer(state, { type: "next" });
  return state;
}

describe("presentation state machine", () => {
  it("cannot advance past P1 without a decision", () => {
    const state = pending();
    expect(workflowReducer(state, { type: "next" })).toEqual(state);
    expect(transcript(state)).toHaveLength(4);
  });
  it.each(["rejected", "timeout"] as const)("blocks remediation after %s", (decision) => {
    const state = workflowReducer(pending(), { type: "decide", decision });
    expect(outcome(state)).toBe("blocked");
    expect(transcript(state).map((entry) => entry.stage)).not.toContain("Remediation");
    expect(transcript(state).map((entry) => entry.stage)).not.toContain("Objective verifier");
    expect(workflowReducer(state, { type: "decide", decision: "approved" })).toEqual(state);
  });
  it.each([true, false])("verifier, not tool success, determines outcome (%s)", (healthy) => {
    let state = workflowReducer(pending(healthy), { type: "decide", decision: "approved" });
    expect(outcome(state)).toBeNull();
    state = workflowReducer(state, { type: "next" });
    expect(outcome(state)).toBeNull();
    state = workflowReducer(state, { type: "next" });
    expect(outcome(state)).toBe(healthy ? "resolved" : "unresolved");
    expect(transcript(state)).toHaveLength(7);
  });
  it("ignores out-of-order decisions and resets all run state", () => {
    const state = workflowReducer(initialWorkflow, { type: "start", scenario, verifierHealthy: false });
    expect(workflowReducer(state, { type: "decide", decision: "approved" })).toEqual(state);
    expect(workflowReducer(state, { type: "reset" })).toEqual(initialWorkflow);
  });
});
