export const stages = [
  "Incident alert", "Triage", "Diagnosis", "Safety / approval",
  "Remediation", "Objective verifier", "Comms",
] as const;

export interface RehearsalScenario {
  id: string;
  title: string;
  service: string;
  alert: string;
  severity: string;
  approval_mode: string;
  diagnosis: string;
  unhealthy_observation: string;
  healthy_observation: string;
  proposal: { tool: string; arguments: Record<string, string> };
  source: string;
  sha256: string;
  checkout_sha256: string;
}

export interface RehearsalCatalog {
  classification: string;
  agent_output: string;
  scenarios: RehearsalScenario[];
}

export type Decision = "approved" | "rejected" | "timeout";
export type Outcome = "resolved" | "unresolved" | "blocked";
export interface WorkflowState {
  scenario: RehearsalScenario | null;
  step: number;
  decision: Decision | null;
  verifierHealthy: boolean;
}
export const initialWorkflow: WorkflowState = {
  scenario: null, step: 0, decision: null, verifierHealthy: true,
};
export type WorkflowAction =
  | { type: "start"; scenario: RehearsalScenario; verifierHealthy: boolean }
  | { type: "next" }
  | { type: "decide"; decision: Decision }
  | { type: "reset" };

export function workflowReducer(state: WorkflowState, action: WorkflowAction): WorkflowState {
  if (action.type === "reset") return initialWorkflow;
  if (action.type === "start") {
    return { scenario: action.scenario, step: 0, decision: null, verifierHealthy: action.verifierHealthy };
  }
  if (!state.scenario || state.step === 6) return state;
  if (action.type === "decide") {
    if (state.step !== 3 || state.decision) return state;
    return { ...state, decision: action.decision, step: action.decision === "approved" ? 4 : 6 };
  }
  if (state.step === 3) return state;
  if (state.step >= 4 && state.decision !== "approved") return state;
  return { ...state, step: state.step + 1 };
}

export function outcome(state: WorkflowState): Outcome | null {
  if (!state.scenario || state.step < 6) return null;
  if (state.decision !== "approved") return "blocked";
  return state.verifierHealthy ? "resolved" : "unresolved";
}

export function transcript(state: WorkflowState) {
  const scenario = state.scenario;
  if (!scenario) return [];
  const entries: { stage: string; summary: string; data: unknown }[] = [
    { stage: stages[0], summary: `${scenario.alert} on ${scenario.service}`,
      data: { alert: scenario.alert, service: scenario.service, observation: scenario.unhealthy_observation } },
    { stage: stages[1], summary: `${scenario.severity} incident scoped to ${scenario.service}`,
      data: { severity: scenario.severity, affected_services: [scenario.service], tool: "promql_query" } },
    { stage: stages[2], summary: scenario.diagnosis,
      data: { root_cause: scenario.diagnosis, observation_tools: ["chaos_list_experiments", "kubectl_describe"],
        proposed_action: scenario.proposal } },
    { stage: stages[3], summary: state.decision ? `Approval ${state.decision}` : "Explicit P1 approval pending",
      data: { decision: state.decision ?? "pending", action: scenario.proposal,
        authority: "Presentation choice only; no operational permit issued" } },
  ];
  if (state.decision === "approved") {
    entries.push(
      { stage: stages[4], summary: "Fixture action accepted; recovery is not yet verified",
        data: { ...scenario.proposal, simulated_tool_success: true, real_tool_executed: false } },
      { stage: stages[5], summary: state.verifierHealthy ? scenario.healthy_observation : scenario.unhealthy_observation,
        data: { simulated_env_resolved: state.verifierHealthy,
          observation: state.verifierHealthy ? scenario.healthy_observation : scenario.unhealthy_observation } },
    );
  }
  const result = outcome(state);
  entries.push({
    stage: stages[6],
    summary: result === "blocked"
      ? `Remediation blocked: approval ${state.decision}. No recovery claim.`
      : result === "unresolved"
        ? "Incident remains unresolved despite the simulated tool success. Escalation required."
        : "Simulated environment recovered. This is not a real incident-resolution result.",
    data: { simulated_outcome: result, real_tool_executed: false, model_inference: false },
  });
  if (state.step < 4) return entries.slice(0, state.step + 1);
  if (state.step === 6) return entries;
  return entries.slice(0, state.step + 1);
}
