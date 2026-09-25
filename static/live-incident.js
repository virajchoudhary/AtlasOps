/* Pure, read-only projection of audit activity for the incident lifecycle. */
(function (root) {
  "use strict";

  function projectLiveIncident(item, audit, auditAvailable, lifecycle) {
    const unknown = auditAvailable ? "Not reported" : "Audit unavailable";
    const latest = predicate => audit.filter(predicate).sort((a, b) => b.ts - a.ts)[0];
    const activity = (role, title, glyph, note) => {
      const entry = latest(row => row.agent_role === role);
      const blocked = entry?.policy_check?.startsWith("blocked");
      return {
        title, icon: glyph, status: entry ? blocked ? "Policy blocked" : "Activity logged" : unknown,
        tone: entry ? blocked ? "blocked" : "info" : "unavailable",
        note: entry
          ? `Last audited ${entry.action_type}${entry.tool_name ? ` / ${entry.tool_name}` : ""}. This does not establish phase completion.`
          : `${note} No phase evidence is available from the current audit window.`
      };
    };
    const approval = latest(row => row.action_type === "approval_decision" ||
      row.action_type === "approval_requested");
    const denied = approval?.policy_check === "approval_denied";
    const denialReason = ["timeout", "rejected"].includes(approval?.result_summary)
      ? approval.result_summary : "denied";
    const verifier = latest(row => row.agent_role === "verifier" ||
      row.tool_name === "environment_verify");
    const action = latest(row => row.agent_role === "remediation" &&
      (row.action_type === "tool_call" || row.action_type === "tool_result"));
    const evidence = auditAvailable && audit.length;
    return [
      { title: "Alert", icon: "radio", status: "Observed", tone: "info",
        note: `Correlated ${(item.alert_names || []).join(", ") || "alert"} for ${(item.services || []).join(", ") || "an unavailable service"}.` },
      activity("triage", "Triage", "scan-search", lifecycle[1][2]),
      activity("diagnosis", "Diagnosis", "scan-eye", lifecycle[2][2]),
      activity("recommender", "Recommendation", "list-ordered", lifecycle[3][2]),
      { title: "Approval", icon: "shield-check",
        status: approval ? denied ? denialReason :
          approval.action_type === "approval_requested" ? "Requested" :
            approval.policy_check === "approval_granted" ? "Approved (audit)" : "Decision logged" : unknown,
        tone: approval ? denied ? "blocked" : "info" : "unavailable",
        note: approval ? `Audit policy: ${approval.policy_check}. A decision entry is not an environment verdict.`
          : "No approval decision is available from the current audit window." },
      { title: "Action", icon: "wrench",
        status: action ? action.policy_check?.startsWith("blocked") ? "Policy blocked" :
          "Tool activity" : unknown,
        tone: action ? action.policy_check?.startsWith("blocked") ? "blocked" : "info" : "unavailable",
        note: action ? `Audited ${action.action_type}${action.tool_name ? ` / ${action.tool_name}` : ""}. Tool activity is not proof of execution success or resolution.`
          : "No action result is available from the current audit window." },
      { title: "Verification", icon: "activity",
        status: verifier ? "Activity logged" : auditAvailable ? "No verdict exposed" : "Audit unavailable",
        tone: verifier ? "info" : "unavailable",
        note: auditAvailable
          ? "The audit feed does not provide an authoritative environment-verification result."
          : "The audit feed is unavailable; no verifier state can be inferred." },
      { title: "Evidence", icon: "file-check-2",
        status: evidence ? "Audit entries" : unknown, tone: evidence ? "info" : "unavailable",
        note: evidence ? `${audit.length} audit entries are visible for this incident. This is not a complete trajectory or empirical gate result.`
          : "No incident evidence is available from the current audit window." }
    ];
  }

  function recommendationQuery(item) {
    const alerts = item?.alert_names;
    const services = item?.services;
    if (!Array.isArray(alerts) || alerts.length !== 1 ||
        !Array.isArray(services) || services.length !== 1) return null;
    const [alert] = alerts, [service] = services;
    if (typeof alert !== "string" || typeof service !== "string" ||
        !alert.trim() || !service.trim() ||
        alert.length > 100 || service.length > 100) return null;
    return { alert_name: alert.trim(), service: service.trim(), top_k: 3 };
  }

  const api = Object.freeze({ projectLiveIncident, recommendationQuery });
  root.AtlasOpsLiveIncident = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);
