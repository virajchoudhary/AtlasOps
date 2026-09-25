/* AtlasOps operator console. All content is read-only; no execution routes are called. */
(() => {
  "use strict";

  const PAGE_TITLES = {
    overview: "Overview", incidents: "Incidents", agents: "Agents", models: "Models",
    evaluations: "Evaluations", runbooks: "Runbooks", evidence: "Evidence", settings: "Settings"
  };
  const main = document.getElementById("main");
  const state = {
    data: {}, loading: true, updated: null, details: {}, liveRecommendations: {}, incidentSearch: "",
    incidentSort: "newest", evaluationFilter: "all", runbookSearch: "",
    scenarioSearch: "", scenarioTier: "all",
    quickResults: [], quickIndex: 0, pendingFocus: null
  };
  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const safe = value => escapeHtml(value === null || value === undefined || value === "" ? "Unavailable" : value);
  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const val = key => state.data[key]?.value ?? null;
  const error = key => state.data[key]?.error || null;
  const fmtTime = value => {
    if (!value) return "Unavailable";
    const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
    return Number.isNaN(date.getTime()) ? safe(value) : escapeHtml(date.toLocaleString());
  };
  const short = (value, length = 17) => {
    const text = String(value || "");
    return `<span class="mono truncate" title="${escapeHtml(text)}">${escapeHtml(text.length > length ? `${text.slice(0, length)}...` : text || "Unavailable")}</span>`;
  };
  const badge = (label, tone = "neutral") => `<span class="badge ${tone}">${safe(label)}</span>`;
  const gateTone = status => status === "PASS" ? "good" :
    status === "NOT_PASSED" || status === "REOPENED" ? "bad" :
    status?.startsWith("IMPLEMENTED") ? "info" : "warn";
  const attemptTone = status => status === "Not passed" ? "bad" :
    status === "Interrupted" ? "warn" : "neutral";
  const empty = (title, description, glyph = "inbox") =>
    `<div class="surface empty-state">${icon(glyph)}<h3>${safe(title)}</h3><p>${safe(description)}</p></div>`;
  const head = (eyebrow, title, subtitle, action = "") =>
    `<header class="page-header"><div><span class="eyebrow">${safe(eyebrow)}</span><h1>${safe(title)}</h1><p class="subtitle">${safe(subtitle)}</p></div>${action}</header>`;
  const section = (title, caption, body, action = "") =>
    `<section class="section"><div class="section-head"><div><h2>${safe(title)}</h2><p class="section-caption">${safe(caption)}</p></div>${action}</div>${body}</section>`;
  const row = (label, value) => `<div class="readiness-row"><span>${safe(label)}</span>${value}</div>`;
  const kv = (label, value) => `<div class="kv"><dt>${safe(label)}</dt><dd>${value}</dd></div>`;
  const nav = (page, text = "View all") => `<a class="text-link" href="#/${page}">${safe(text)} ${icon("arrow-up-right")}</a>`;
  const lifecycle = [
    ["Alert", "radio", "Capture the incoming signal and incident context."],
    ["Triage", "scan-search", "Identify affected services, severity, and impact."],
    ["Diagnosis", "scan-eye", "Investigate observations and distinguish findings from guesses."],
    ["Recommendation", "list-ordered", "Rank advisory runbooks against the incident."],
    ["Approval", "shield-check", "Require the applicable human and policy authorization."],
    ["Action", "wrench", "Record the exact bounded action and its tool result."],
    ["Verification", "activity", "Check the environment independently of an agent claim."],
    ["Evidence", "file-check-2", "Preserve outcomes, provenance, and failures."]
  ];
  function processView(steps, title, caption, label) {
    const selected = steps[0];
    return `<section class="process-view" aria-label="${safe(label)}">
      <div class="section-head"><div><span class="eyebrow">INCIDENT LIFECYCLE</span>
      <h2>${safe(title)}</h2><p class="section-caption">${safe(caption)}</p></div>
      ${badge(label, "info")}</div>
      <ol class="process-track">${steps.map((step, index) => `<li>
        <button type="button" class="process-node ${step.tone} ${index === 0 ? "selected" : ""}"
          data-process-step data-title="${escapeHtml(step.title)}" data-status="${escapeHtml(step.status)}"
          data-note="${escapeHtml(step.note)}" aria-pressed="${index === 0}"
          aria-label="${escapeHtml(step.title)}: ${escapeHtml(step.status)}">
          <span class="process-symbol">${icon(step.icon)}</span>
          <span class="process-copy"><strong>${safe(step.title)}</strong><small>${safe(step.status)}</small></span>
        </button></li>`).join("")}</ol>
      <div class="process-inspector" aria-live="polite">
        <div><span class="process-inspector-status">${safe(selected.status)}</span>
        <h3 class="process-inspector-title">${safe(selected.title)}</h3></div>
        <p class="process-inspector-note">${safe(selected.note)}</p>
      </div></section>`;
  }
  const referenceSteps = lifecycle.map(([title, glyph, note]) => ({
    title, icon: glyph, note, status: "Reference stage", tone: "reference"
  }));

  function route() {
    let path;
    try { path = decodeURIComponent((location.hash || "#/overview").replace(/^#\/?/, "")); }
    catch { path = "overview"; }
    const parts = path.split("/");
    const page = Object.hasOwn(PAGE_TITLES, parts[0]) ? parts[0] : "overview";
    return { page, kind: parts[1] || "", id: parts[2] || "" };
  }

  async function request(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(path, { ...options, signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } finally {
      clearTimeout(timeout);
    }
  }

  let refreshRun = 0;
  async function refresh() {
    const run = ++refreshRun;
    state.liveRecommendations = {};
    if (!Object.keys(state.data).length) {
      state.loading = true;
      render();
    }
    const endpoints = {
      catalog: "/ui/catalog", incidents: "/incidents/active", health: "/health",
      cluster: "/cluster/health", metrics: "/metrics", audit: "/audit/log?limit=50",
      integrity: "/audit/verify", breaker: "/circuit-breaker/status", config: "/config",
      scenarios: "/api/scenarios", featured: "/ui/attempts/EXP-STAGE4-SF002-010.json"
    };
    const jobs = Object.fromEntries(Object.entries(endpoints).map(([key, path]) => [key, (async () => {
      try {
        const value = await request(path);
        if (run === refreshRun) state.data[key] = { value };
      } catch (err) {
        if (run === refreshRun) state.data[key] = { error: err.message };
      }
    })()]));
    await Promise.all(["catalog", "incidents", "health", "featured"].map(key => jobs[key]));
    if (run !== refreshRun) return;
    const featured = val("featured");
    if (featured?.name) state.details[featured.name] = { value: featured };
    state.loading = false;
    state.updated = new Date();
    render();
    await Promise.all(Object.values(jobs));
    if (run !== refreshRun) return;
    state.updated = new Date();
    const focused = document.activeElement;
    if (focused?.matches("input, select, textarea") ||
        focused?.closest("form, .palette, .drawer") ||
        !document.getElementById("palette").hidden) {
      statusBar();
      if (document.getElementById("scenario-list") && val("scenarios")) {
        document.getElementById("scenario-list").innerHTML = scenarioList();
      }
    } else {
      render();
    }
  }

  function statusBar() {
    const cluster = val("cluster");
    const process = val("health");
    let tone = "neutral", text = "Runtime unverified";
    if (cluster?.ok && cluster.total > 0) {
      tone = cluster.healthy === cluster.total ? "good" : "warn";
      text = `${cluster.healthy}/${cluster.total} services ready`;
    } else if (process?.status === "ok") {
      text = "API reachable; cluster unverified";
    }
    document.getElementById("global-status").innerHTML =
      `<span class="state-dot ${tone}"></span>${escapeHtml(text)}`;
    document.getElementById("rail-state").textContent = text;
    document.querySelector(".rail-dot").className = `rail-dot ${tone}`;
    document.getElementById("last-updated").textContent = state.updated
      ? `Refreshed ${state.updated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
      : "Not refreshed";
    const sha = val("catalog")?.source_sha;
    document.getElementById("rail-source").textContent = sha
      ? `Source ${sha.slice(0, 12)}` : "Source revision unavailable";
    document.getElementById("rail-source").title = sha || "";
  }

  function activeRows(items, compact = false) {
    return `<div class="surface table-scroll"><table><thead><tr>
      <th>Incident</th><th>Service</th><th>Signal</th><th>Phase</th><th>Verification</th><th>Started</th>
      </tr></thead><tbody>${items.map(item => `<tr>
      <td><button class="row-button mono" data-live="${escapeHtml(item.incident_id)}">${short(item.incident_id, 22)}</button></td>
      <td>${safe((item.services || []).join(", ") || "Unavailable")}</td>
      <td>${safe((item.alert_names || []).join(", ") || "Unavailable")}</td>
      <td>${badge(item.active_processing ? "Processing" : "Observed", "info")}</td>
      <td>${badge("Not reported", "neutral")}</td>
      <td>${fmtTime(item.created_at)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function recordedRows(items) {
    return `<div class="surface table-scroll"><table><thead><tr>
      <th>Attempt</th><th>Scenario</th><th>Recorded state</th><th>Evidence class</th><th>Recorded at</th>
      </tr></thead><tbody>${items.map(item => `<tr>
      <td><button class="row-button mono" data-attempt="${escapeHtml(item.name)}">${short(item.id, 29)}</button></td>
      <td>${safe(item.scenario)}</td><td>${badge(item.state, attemptTone(item.state))}</td>
      <td>${badge(item.classification, "info")}</td><td>${fmtTime(item.timestamp)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function auditActivity() {
    const audit = val("audit");
    if (!audit) return empty("Audit history unavailable",
      "The configured audit log could not be read in this runtime.", "file-warning");
    const entries = (audit.entries || []).slice().reverse().slice(0, 7);
    if (!entries.length) return empty("No audit entries",
      "No incident activity is recorded in the configured audit log.", "list");
    return `<div class="surface table-scroll"><table><thead><tr>
      <th>Time</th><th>Incident</th><th>Event</th><th>Result</th></tr></thead><tbody>
      ${entries.map(entry => `<tr><td>${fmtTime(entry.ts)}</td><td>${short(entry.incident_id)}</td>
      <td>${safe(entry.action_type)}</td><td>${safe(entry.result_summary)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function overview() {
    const incidents = val("incidents")?.incidents;
    const catalog = val("catalog");
    const featured = val("featured");
    const cluster = val("cluster");
    const metrics = val("metrics");
    const health = val("health");
    const breaker = val("breaker");
    const recent = Array.isArray(incidents) ? incidents.slice(0, 6) : null;
    const latest = catalog?.attempts?.find(a => a.name === "EXP-STAGE4-SF002-010.json");
    const stats = [
      ["Active incidents", recent ? String(incidents.length) : "Unavailable",
        recent ? "Current process window" : "Incident API unavailable"],
      ["Verified outcomes", "Unavailable", "No verified outcome aggregate exposed"],
      ["Blocked actions", "Unavailable", "No authoritative count exposed"],
      ["Cluster readiness", cluster?.ok && cluster.total > 0
        ? `${cluster.healthy}/${cluster.total}` : "Unverified",
      cluster?.ok ? "Observed pod readiness" : "No current cluster observation"]
    ];
    const investigation = featured ? `<section class="case-spotlight">
      <div class="case-summary"><div><span class="eyebrow">PRESERVED INVESTIGATION</span>
      <h2>${safe(featured.triage.title)}</h2>
      <p>${safe(featured.id)} / ${safe(featured.scenario)} / ${fmtTime(featured.timestamp)}</p></div>
      <div class="case-actions">${badge(featured.state, attemptTone(featured.state))}
      <button class="button" data-attempt="${escapeHtml(featured.name)}">${icon("arrow-up-right")} Open workspace</button></div></div>
      <div class="case-metadata"><span>Frozen target <strong>${safe(featured.triage.frozen_targets.join(", "))}</strong></span>
      <span>Approval <strong>${safe(featured.approval)}</strong></span>
      <span>Environment <strong>${featured.verification.env_resolved === false ? "Unresolved" : "Not certified"}</strong></span></div>
    </section>` +
      processView(recordedSteps(featured), "Incident lifecycle",
        "Historical attempt. Recorded stages are not proof of successful resolution.", "Historical evidence")
      : processView(referenceSteps, "From signal to evidence",
        "Reference workflow. This diagram does not represent a running or completed incident.",
        "Reference workflow");
    return head("OPERATIONS", "Operations", "Current observations and incident evidence, without inferred outcomes.") +
      (error("catalog") ? `<div class="notice bad">Repository status unavailable: ${safe(error("catalog"))}.</div>` : "") +
      `<div class="stat-band">${stats.map(([label, value, detail]) =>
        `<div class="stat"><span class="stat-label">${safe(label)}</span><strong class="stat-value ${value.length > 12 ? "word" : ""}">${safe(value)}</strong><span class="stat-detail">${safe(detail)}</span></div>`).join("")}</div>` +
      investigation +
      `<div class="content-grid"><div>` +
      section("Current incidents", "Live correlator window; not a historical incident ledger",
        recent ? recent.length ? activeRows(recent, true) : empty("No active incidents",
          "The current process has no active correlated incidents. This does not report historical resolution.")
          : empty("Incident feed unavailable", "The live incident endpoint could not be read.", "triangle-alert"),
        nav("incidents")) +
      section("Recent activity", "Entries from the configured append-only audit log", auditActivity()) +
      `</div><div>` +
      section("System readiness", "Observed now or explicitly unavailable",
        `<div class="surface surface-pad">
        ${row("API process", badge(health?.status === "ok" ? "Reachable" : "Unavailable",
          health?.status === "ok" ? "good" : "neutral"))}
        ${row("Telemetry", badge(metrics && Object.values(metrics).some(v => v !== null)
          ? "Observation returned" : "Unavailable", metrics && Object.values(metrics).some(v => v !== null) ? "info" : "neutral"))}
        ${row("Kubernetes", badge(cluster?.ok ? `${cluster.healthy}/${cluster.total} ready` : "Unavailable", cluster?.ok ? "info" : "neutral"))}
        ${row("Model endpoint", badge(health?.agent_base ? "Configured; not probed" : "Not configured", health?.agent_base ? "info" : "neutral"))}
        ${row("Approval path", badge("Not observable here", "neutral"))}
        ${row("Environment verifier", badge("No current verdict", "neutral"))}
        ${row("Evidence store", badge(catalog ? "Repository snapshot" : "Unavailable", catalog ? "info" : "neutral"))}
        ${row("Circuit breaker", badge(breaker ? breaker.tripped ? "Tripped" : "Not tripped" : "Unavailable", breaker?.tripped ? "bad" : "neutral"))}
        </div>`) +
      section("Research status", "Repository governance, separate from live operations",
        `<div class="surface surface-pad"><p class="muted">G4 remains <strong>NOT_PASSED</strong>. Downstream implementation and mock tests do not certify an end-to-end incident.</p>
        ${latest ? `<div class="section-divider">${badge("Historical negative result", "bad")}<p class="section-caption">Attempt 010: approval timeout, target drift, and failed environment verification.</p>
        <button class="text-link" data-attempt="${escapeHtml(latest.name)}">Inspect attempt ${icon("arrow-up-right")}</button></div>` : ""}
        <div class="section-divider">${nav("evaluations", "Explore evaluations")}</div></div>`) +
      `</div></div>`;
  }

  function incidentsPage() {
    const live = val("incidents")?.incidents;
    const records = val("catalog")?.attempts;
    return head("OPERATIONS", "Incidents", "Live incident correlation and preserved attempts are shown separately.") +
      `<div class="notice">This console is read-only. An active alert is not a verified resolution; historical attempts are not live incidents.</div>` +
      section("Active incidents", "The correlator retains a short in-process window",
        live ? live.length ? activeRows(live) : empty("No active incidents",
          "No current incident is reported by this process. Recent history may exist only in the audit or preserved evidence.")
          : empty("Active feed unavailable", "The incident endpoint could not be read.", "triangle-alert")) +
      section("Preserved incident attempts", "G4 remains NOT_PASSED; no attempt here certifies safe resolution",
        `<div class="controls"><label class="field grow">Search recorded attempts
        <input id="incident-search" type="search" value="${escapeHtml(state.incidentSearch)}" placeholder="Attempt ID or state"></label>
        <label class="field">Order<select id="incident-sort"><option value="newest" ${state.incidentSort === "newest" ? "selected" : ""}>Newest first</option>
        <option value="oldest" ${state.incidentSort === "oldest" ? "selected" : ""}>Oldest first</option></select></label></div>
        <div id="recorded-list">${records ? renderRecordedList() :
          empty("Preserved evidence unavailable", "The repository catalog could not be read.", "file-warning")}</div>`);
  }

  function renderRecordedList() {
    const items = (val("catalog")?.attempts || []).filter(item =>
      `${item.id} ${item.state} ${item.scenario}`.toLowerCase().includes(state.incidentSearch.toLowerCase()));
    if (state.incidentSort === "oldest") items.reverse();
    return items.length ? recordedRows(items) :
      empty("No matching attempts", "Adjust the search to see preserved records.", "search");
  }

  function liveIncidentSteps(item, audit, auditAvailable) {
    return window.AtlasOpsLiveIncident.projectLiveIncident(item, audit, auditAvailable, lifecycle);
  }

  function loadLiveRecommendations(item, query) {
    const key = JSON.stringify(query);
    const id = item.incident_id;
    if (state.liveRecommendations[id]?.key === key) return;
    const pending = { key, loading: true };
    state.liveRecommendations[id] = pending;
    request("/api/recommender/recommend", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(query)
    }).then(value => {
      if (state.liveRecommendations[id] !== pending) return;
      state.liveRecommendations[id] = { key, value };
      if (route().kind === "live" && route().id === id) render();
    }).catch(err => {
      if (state.liveRecommendations[id] !== pending) return;
      state.liveRecommendations[id] = { key, error: err.message };
      if (route().kind === "live" && route().id === id) render();
    });
  }

  function liveRecommendationSection(item) {
    const caption = "Scenario-derived ranking, not a recorded agent decision or recovery probability";
    const query = window.AtlasOpsLiveIncident.recommendationQuery?.(item);
    if (!query) return section("Advisory runbooks", caption,
      empty("Ranking unavailable", "A single observed alert and service are required for this query.", "list-ordered"));
    loadLiveRecommendations(item, query);
    const result = state.liveRecommendations[item.incident_id];
    let body;
    if (result.loading) {
      body = `<div class="loading-state"><span class="spinner"></span>Ranking advisory runbooks</div>`;
    } else if (result.error) {
      body = empty("Ranking unavailable", `The runbook service could not be read: ${result.error}.`, "triangle-alert");
    } else {
      const recommendations = Array.isArray(result.value?.recommendations)
        ? result.value.recommendations.slice(0, 3) : [];
      body = recommendations.length ? `<div class="list-stack">${recommendations.map((rec, index) =>
        `<div class="evidence-row"><div>
          <button class="row-button" data-runbook="${escapeHtml(rec.runbook_id)}">${index + 1}. ${safe(rec.title)}</button>
          <div class="meta-note">${safe(rec.runbook_id)} / ${safe(rec.explanation)}</div>
        </div>${badge("Advisory", "info")}</div>`).join("")}</div>` :
        empty("No ranked suggestions", "The runbook service returned no suggestions for this context.", "list-ordered");
    }
    return section("Advisory runbooks", caption,
      `<p class="meta-note">Observed query: ${safe(query.alert_name)} / ${safe(query.service)}. This does not authorize action.</p>${body}`);
  }

  function liveDetail(id) {
    const item = val("incidents")?.incidents?.find(row => row.incident_id === id);
    if (!item) return head("INCIDENT", "Incident unavailable", "This in-process record may have expired.") +
      `<a class="button" href="#/incidents">Back to incidents</a>`;
    if (!window.AtlasOpsLiveIncident?.projectLiveIncident) {
      return head("INCIDENT", "Incident view unavailable", "The read-only phase projection could not be loaded.") +
        `<div class="notice bad">No incident outcome can be inferred from an unavailable projection.</div>`;
    }
    const auditEntries = val("audit")?.entries;
    const audit = auditEntries?.filter(row => row.incident_id === id) || [];
    const steps = liveIncidentSteps(item, audit, Array.isArray(auditEntries));
    return head("ACTIVE INCIDENT", (item.alert_names || []).join(", ") || "Alert observed",
      `${(item.services || []).join(", ") || "Service unavailable"} / ${item.namespace || "Namespace unavailable"}`,
      badge(item.active_processing ? "Processing" : "Observed", "info")) +
      `<div class="notice warn">Only correlation and audit fields are exposed for this live record. No verification verdict or final outcome is available here.</div>` +
      `<div class="surface surface-pad"><dl>${kv("Incident ID", short(id, 60))}
      ${kv("Created", fmtTime(item.created_at))}${kv("Last alert", fmtTime(item.last_alert_at))}
      ${kv("Alert count", safe(item.alert_count))}${kv("Severity", badge("Unavailable"))}
      ${kv("Verification", badge("Not reported"))}</dl></div>` +
      processView(steps, "Observed lifecycle",
        "Audited activity is not phase completion. Only objective verification can establish resolution.",
        "Live observation") +
      liveRecommendationSection(item) +
      section("Audit activity", "A log entry is not an environment-verification verdict",
        audit.length ? `<div class="surface table-scroll"><table><thead><tr><th>Time</th><th>Role</th><th>Event</th><th>Policy</th><th>Result</th></tr></thead><tbody>
        ${audit.slice().reverse().map(entry => `<tr><td>${fmtTime(entry.ts)}</td><td>${safe(entry.agent_role)}</td>
        <td>${safe(entry.action_type)}</td><td>${badge(entry.policy_check || "Unavailable",
          entry.policy_check?.startsWith("blocked") || entry.policy_check === "approval_denied" ? "warn" : "neutral")}</td>
        <td>${safe(entry.result_summary)}</td></tr>`).join("")}</tbody></table></div>`
          : empty(auditEntries ? "No audit entries" : "Audit unavailable",
            auditEntries ? "No entries for this incident are in the current audit window."
              : "The configured audit log could not be read."));
  }

  function recordedSteps(detail) {
    const v = detail.verification;
    const approval = detail.approval.toLowerCase();
    const recordedStates = [
      ["Historical record", "info", `Preserved scenario ${detail.scenario}; this is not a live alert.`],
      [detail.triage.title === "No triage result recorded" ? "Unavailable" : "Recorded", "info",
        `Triage reported ${detail.triage.services.join(", ") || "no service"}; frozen target: ${detail.triage.frozen_targets.join(", ") || "unavailable"}.`],
      [detail.diagnosis.specific ? "Recorded" : "Unavailable", "info",
        detail.diagnosis.specific || "No diagnosis finding was preserved."],
      ["Unavailable", "unavailable", detail.recommendation],
      [detail.approval, approval === "timeout" || approval === "rejected" ? "blocked" : "info",
        approval === "timeout" || approval === "rejected"
          ? "No approval to execute was established by this decision." : "Recorded decision; inspect source provenance."],
      [detail.actions.length ? `${detail.actions.length} attempt(s)` : "Not recorded",
        detail.actions.some(action => !action.success) ? "failed" : "unavailable",
        detail.actions.length ? "Tool attempts are recorded, but success of an action is not environment resolution."
          : "No mutating action was recorded."],
      [v.env_resolved === false ? "Unresolved" : v.env_resolved === true
        ? "Raw verdict; not certified" : "No completed verdict",
      v.env_resolved === false ? "failed" : v.env_resolved === true ? "blocked" : "unavailable",
      v.env_resolved === false ? "Objective checks did not establish resolution."
        : "A historical raw verdict cannot certify the current G4 gate."],
      ["Preserved", "info", `Selected fields from ${detail.source}; SHA-256 ${detail.sha256}.`]
    ];
    return lifecycle.map(([title, glyph], index) => ({
      title, icon: glyph, status: recordedStates[index][0], tone: recordedStates[index][1],
      note: recordedStates[index][2]
    }));
  }

  function recordedDetail(detail) {
    const v = detail.verification;
    const approval = detail.approval.toLowerCase();
    const steps = recordedSteps(detail);
    const verdictText = v.env_resolved === false ? "Environment unresolved" :
      v.env_resolved === true ? "Historical raw resolution; not certified" : "No completed verifier verdict";
    const verdictNote = v.env_resolved === false
      ? "The environment remained unresolved in this attempt. Model and tool claims cannot override the checks below."
      : "No current G4 certification follows from this preserved record.";
    return head(detail.id, detail.triage.title, `${detail.scenario} / Preserved G4 attempt`,
      badge(detail.state, attemptTone(detail.state))) +
      `<div class="notice bad"><strong>G4 NOT_PASSED.</strong> This is historical evidence, not a live or certified resolution.
      ${detail.warnings.length ? `<br>${detail.warnings.map(safe).join(" ")}` : ""}</div>` +
      `<div class="surface surface-pad"><dl>${kv("Attempt", short(detail.id, 90))}
      ${kv("Affected service", safe(detail.triage.services.join(", ") || "Unavailable"))}
      ${kv("Frozen target", safe(detail.triage.frozen_targets.join(", ") || "Unavailable"))}
      ${kv("Severity", badge(detail.triage.severity, "warn"))}
      ${kv("Recorded at", fmtTime(detail.timestamp))}
      ${kv("Evidence class", badge("Historical evidence", "info"))}</dl></div>` +
      processView(steps, "Recorded lifecycle",
        "A recorded stage is not a successful stage. The verifier remains authoritative.",
        "Historical evidence") +
      `<section class="verifier-spotlight ${v.env_resolved === false ? "failed" : "unavailable"}">
        <div><span class="eyebrow">OBJECTIVE VERIFICATION</span><h2>${safe(verdictText)}</h2>
        <p>${safe(verdictNote)}</p></div>
        <div class="verifier-checks">${v.checks.length ? v.checks.map(check =>
          `<div class="verifier-check"><span class="state-dot ${check.passed ? "good" : "bad"}"></span>
          <div><strong>${safe(check.name)}</strong><small>${safe(check.details)}</small></div>
          ${badge(check.passed ? "Passed" : "Failed", check.passed ? "good" : "bad")}</div>`).join("") :
          `<p>No completed objective checks are recorded.</p>`}</div></section>` +
      `<div class="section-head"><h2>Incident record</h2><div class="record-actions">
      <a class="text-link" href="#/runbooks">Runbook library ${icon("arrow-up-right")}</a>
      <button class="text-link" data-gate="G4">G4 status ${icon("arrow-up-right")}</button>
      <button class="button" data-attempt-evidence="${escapeHtml(detail.name)}">
      ${icon("file-text")} Source & provenance</button></div></div>` +
      `<div class="detail-grid">
      <section class="detail-section"><h2>Triage</h2><p>${safe(detail.triage.title)}</p><p>Reported: ${safe(detail.triage.services.join(", ") || "Unavailable")}</p></section>
      <section class="detail-section"><h2>Diagnosis</h2><p>${safe(detail.diagnosis.specific || "No diagnosis recorded.")}</p>
      <p class="meta-note">Category: ${safe(detail.diagnosis.category)}</p></section>
      <section class="detail-section"><h2>Recommendation</h2><p>${safe(detail.recommendation)}</p>
      <p class="section-caption">The runbook library is advisory and does not retroactively create a recommendation for this attempt.</p></section>
      <section class="detail-section"><h2>Approval</h2>${badge(detail.approval, approval === "timeout" || approval === "rejected" ? "warn" : "neutral")}
      <p>Timeout and rejection do not authorize remediation.</p></section>
      <section class="detail-section"><h2>Action</h2>${detail.actions.length
        ? `<div class="check-list">${detail.actions.map(action => `<div class="check ${action.success ? "good" : "bad"}">
        <strong>${safe(action.tool)}</strong> / ${safe(action.target)}<span>${action.success ? "Tool reported success" : "Tool did not report success"}</span></div>`).join("")}</div>`
        : `<p>No mutating action recorded in this attempt.</p>`}</section>
      </div><div class="section-divider">${nav("incidents", "Back to incidents")}</div>`;
  }

  async function loadAttempt(name) {
    if (!/^EXP-STAGE4-SF002-\d{3}(?:\.interruption)?\.json$/.test(name) || state.details[name]) return;
    state.details[name] = { loading: true };
    try { state.details[name] = { value: await request(`/ui/attempts/${encodeURIComponent(name)}`) }; }
    catch (err) { state.details[name] = { error: err.message }; }
    if (route().kind === "recorded" && route().id === name) render();
  }

  function attemptPage(name) {
    if (!/^EXP-STAGE4-SF002-\d{3}(?:\.interruption)?\.json$/.test(name)) {
      return head("INCIDENT", "Record unavailable", "The requested evidence identifier is invalid.");
    }
    const entry = state.details[name];
    if (!entry) { loadAttempt(name); return `<div class="loading-state"><span class="spinner"></span>Loading preserved attempt</div>`; }
    if (entry.loading) return `<div class="loading-state"><span class="spinner"></span>Loading preserved attempt</div>`;
    if (entry.error) return head("INCIDENT", "Record unavailable", "The preserved record could not be read.") +
      `<div class="notice bad">${safe(entry.error)}</div>`;
    return recordedDetail(entry.value);
  }

  const roles = [
    ["Triage", "Classifies incoming alerts and affected services.", "Agent", "triage"],
    ["Diagnosis", "Investigates root cause from tools and observations.", "Agent", "diagnosis"],
    ["Recommender", "Ranks scenario-derived runbooks as advisory options.", "Advisory", "recommender"],
    ["Remediation policy", "Checks target, tool, and approval constraints.", "Safety control", "remediation"],
    ["Environment verifier", "Checks observed state after action; verdict governs resolution.", "Authority", "verifier"],
    ["Comms", "Records incident updates after verification.", "Agent", "comms"]
  ];
  function agentsPage() {
    const entries = val("audit")?.entries;
    return head("AUTOMATION", "Agents", "Roles in the incident chain; this view does not assert that any agent is online.") +
      `<div class="notice">Generative agents propose. Safety controls authorize. Environment verification decides success.</div>` +
      section("Execution roles", "Runtime activity and model identity are not proven by a role definition",
        `<div class="list-stack">${roles.map(([name, description, type, key]) => {
          const latest = entries?.filter(entry => entry.agent_role === key)
            .reduce((a, b) => !a || b.ts > a.ts ? b : a, null);
          return `<div class="list-item">
        <div class="list-item-main"><h3>${safe(name)}</h3><p>${safe(description)}</p></div>
        <div>${badge(type, type === "Authority" ? "good" : type === "Safety control" ? "warn" : "neutral")}
        <div class="meta-note">${latest ? `Last audited ${fmtTime(latest.ts)}` :
          entries ? "No recent audited activity" : "Activity unavailable"}</div></div></div>`;
        }).join("")}</div>`);
  }

  function modelsPage() {
    const health = val("health");
    const configured = Boolean(health?.agent_base);
    return head("RUNTIME", "Models", "Configuration is separate from checkpoint provenance and empirical evaluation.") +
      section("Model registry", "A declared route, a usable checkpoint, and an empirical result are different states",
        `<div class="surface table-scroll"><table><thead><tr><th>Stage</th><th>Identity</th><th>Runtime / checkpoint</th><th>Readiness</th><th>Evaluation</th></tr></thead><tbody>
        <tr><td class="strong">Base model</td><td>${safe(health?.model)}<br><span class="meta-note">API-reported label</span></td>
        <td>${configured ? "Endpoint configured" : "Endpoint not configured"}</td>
        <td>${badge(configured ? "Configured; unverified" : "Not configured", configured ? "info" : "neutral")}</td>
        <td><button class="text-link" data-gate="G6">G6 ${icon("arrow-up-right")}</button></td></tr>
        <tr><td class="strong">SFT</td><td>Unavailable</td><td>No verified usable checkpoint in this view</td>
        <td>${badge("Not yet evaluated", "warn")}</td>
        <td><button class="text-link" data-gate="G7">G7 ${icon("arrow-up-right")}</button>
        <button class="text-link" data-gate="G8">G8 ${icon("arrow-up-right")}</button></td></tr>
        <tr><td class="strong">GRPO</td><td>Unavailable</td><td>No verified usable checkpoint in this view</td>
        <td>${badge("Not yet evaluated", "warn")}</td>
        <td><button class="text-link" data-gate="G9">G9 ${icon("arrow-up-right")}</button></td></tr>
        </tbody></table></div>`) +
      `<div class="notice warn">Historical Stage 6/8/9 mock outputs are not deployment evidence or measured model improvement. See Evaluations and Evidence for the recorded limitations.</div>`;
  }

  const evaluationGroups = [
    ["Foundation & environment", 0, 5, "Provenance, local environment, and the golden incident"],
    ["Model development", 6, 9, "Zero-shot, SFT, and GRPO"],
    ["Runbook recommendation", 10, 11, "Dataset and bounded ranker evaluation"],
    ["Integration & delivery", 12, 15, "Pipeline, ablation, demo, and submission"]
  ];
  const gateNotes = {
    G4: "Attempt 010 is the latest completed negative result among 009-014. The other recent attempts were interrupted.",
    G6: "Historical Stage 6 outputs are mock evidence, not a genuine zero-shot baseline.",
    G7: "A corpus and configuration exist, but usable checkpoint provenance is unverified.",
    G8: "The preserved evaluation is deterministic mock output, not demonstrated SFT improvement.",
    G9: "Direct-action software is locally tested. Real training, checkpoint, and evaluation remain missing.",
    G10: "The interactions are scenario-derived and do not constitute historical operator feedback.",
    G11: "PASS applies to bounded ranking on a small scenario-derived dataset, not broad recovery performance.",
    G12: "Local integration tests do not establish a real checkpoint and environment run.",
    G13: "The preserved matrix contains predetermined profiles, not measured ablation results.",
    G14: "The demo is read-only; deployment safety is not certified by code alone.",
    G15: "The package inventory is NOT_CERTIFIED while empirical gates remain open."
  };
  function scenarioList() {
    const items = (val("scenarios")?.scenarios || []).filter(item =>
      (state.scenarioTier === "all" || item.tier === state.scenarioTier) &&
      `${item.scenario_id} ${item.expected_alert} ${item.description}`.toLowerCase()
        .includes(state.scenarioSearch.toLowerCase()));
    return items.length ? `<div class="surface table-scroll"><table><thead><tr>
      <th>Scenario</th><th>Tier</th><th>Expected signal</th><th>Frozen target</th></tr></thead><tbody>
      ${items.map(item => `<tr><td><button class="row-button mono" data-scenario="${escapeHtml(item.scenario_id)}">
      ${safe(item.scenario_id)}</button></td><td>${badge(item.tier.replaceAll("_", " "), "neutral")}</td>
      <td>${safe(item.expected_alert)}</td><td>${safe((item.target_services || []).join(", ") || "Unavailable")}</td></tr>`).join("")}
      </tbody></table></div>` : empty("No matching scenarios", "Adjust the catalogue filters.", "search");
  }
  function evaluationsPage() {
    const gates = val("catalog")?.gates;
    const selected = gates?.filter(gate => state.evaluationFilter === "all" ||
      (state.evaluationFilter === "open" ? gate.status !== "PASS" : gate.status === "PASS"));
    const groups = selected && evaluationGroups.map(([title, first, last, caption]) => {
      const rows = selected.filter(gate => {
        const index = Number(gate.gate.slice(1));
        return index >= first && index <= last;
      });
      if (!rows.length) return "";
      return section(title, caption, `<div class="list-stack">${rows.map(gate => `<div class="list-item">
        <div class="list-item-main"><span class="eyebrow">${safe(gate.gate)} / ${safe(gate.stage)}</span>
        <h3>${safe(gate.name)}</h3><p>${safe(gateNotes[gate.gate] || gate.deliverable)}</p></div>
        <div>${badge(gate.status, gateTone(gate.status))}
        <button class="text-link" data-gate="${safe(gate.gate)}" aria-label="Inspect ${safe(gate.gate)} status">
        ${icon("arrow-up-right")}</button></div></div>`).join("")}</div>`);
    }).join("");
    return head("RESEARCH STATUS", "Evaluations", "G0-G15 governance and preserved evidence, separate from operator health.") +
      `<div class="notice warn">G4 is NOT_PASSED. G9 and G13 are REOPENED. Software implementation and CI do not close empirical gates.</div>` +
      `<div class="controls"><div class="segmented" aria-label="Evaluation filter">
      ${[["all", "All gates"], ["open", "Open"], ["passed", "Recorded PASS"]].map(([id, title]) =>
        `<button data-eval-filter="${id}" class="${state.evaluationFilter === id ? "active" : ""}" aria-pressed="${state.evaluationFilter === id}">${title}</button>`).join("")}</div></div>` +
      (groups || empty("Status unavailable", "The checked-in governance table could not be read.", "file-warning")) +
      section("Scenario catalogue", "Frozen evaluation definitions; not an operational runbook or replay control",
        `<div class="controls"><label class="field grow">Search scenarios
        <input id="scenario-search" type="search" value="${escapeHtml(state.scenarioSearch)}" placeholder="ID or signal"></label>
        <label class="field">Tier<select id="scenario-tier">
        ${[["all", "All tiers"], ["single_fault", "Single fault"], ["cascade", "Cascade"],
          ["multi_fault", "Multi-fault"], ["named_replays", "Named replay"]].map(([id, label]) =>
            `<option value="${id}" ${state.scenarioTier === id ? "selected" : ""}>${label}</option>`).join("")}
        </select></label></div>
        <div id="scenario-list">${val("scenarios") ? scenarioList() :
          empty(state.data.scenarios ? "Scenario catalogue unavailable" : "Loading scenario catalogue",
            "No scenario definitions are available in this view.", "file-warning")}</div>`) +
      `<div class="section-divider">${nav("evidence", "Browse evidence")}</div>`;
  }

  function runbookList() {
    const items = (val("catalog")?.runbooks || []).filter(rb =>
      `${rb.id} ${rb.title} ${rb.category}`.toLowerCase().includes(state.runbookSearch.toLowerCase()));
    return items.length ? `<div class="list-stack">${items.map((rb, index) => `<div class="list-item">
      <div class="list-item-main"><h3><button class="row-button" data-runbook="${escapeHtml(rb.id)}">${safe(rb.title)}</button></h3>
      <p><span class="mono">${safe(rb.id)}</span> / ${safe(rb.category)}</p></div>
      <button class="text-link" data-runbook="${escapeHtml(rb.id)}" aria-label="Inspect ${escapeHtml(rb.title)}">${icon("arrow-up-right")}</button>
      </div>`).join("")}</div>` : empty("No matching runbooks", "Adjust the search to see catalog entries.", "search");
  }

  function runbooksPage() {
    return head("OPERATIONS LIBRARY", "Runbooks", "Catalog entries and advisory recommendations; no action can be executed here.") +
      section("Find a runbook", "12 catalog definitions; availability and execution validation are not asserted",
        `<label class="field grow">Search catalog<input id="runbook-search" type="search" value="${escapeHtml(state.runbookSearch)}" placeholder="ID, title, category"></label>
        <div id="runbook-list" style="margin-top:14px">${val("catalog") ? runbookList() :
          empty("Catalog unavailable", "The repository catalog could not be read.", "file-warning")}</div>`) +
      section("Advisory ranking", "Scenario-derived training interactions, not historical feedback or recovery probabilities",
        `<form id="recommend-form" class="surface surface-pad">
        <div class="controls"><label class="field">Alert name<input name="alert_name" required value="KubeMemoryOvercommit"></label>
        <label class="field">Service<input name="service" required value="frontend"></label>
        <label class="field grow">Symptoms<input name="symptoms" value="OOMKilled memory limit exceeded"></label>
        <label class="field">Top K<select name="top_k"><option>1</option><option>2</option><option selected>3</option><option>4</option><option>5</option></select></label>
        <button class="button primary" type="submit">${icon("search")} Rank runbooks</button></div>
        <p class="meta-note">Advisory only. Querying this model does not approve or execute remediation.</p>
        <div id="rec-results"></div></form>`);
  }

  function evidencePage() {
    const catalog = val("catalog");
    const areas = [
      ["Model evaluations", "Archived mock outputs, not empirical performance"],
      ["Training provenance", "Corpus and configuration, not a validated checkpoint"],
      ["Recommender", "Scenario-derived interaction and ranker evidence"],
      ["Ablation", "Predetermined historical output, not a measured comparison"],
      ["Environment", "Historical local acceptance, not current cluster health"],
      ["Submission", "Asset integrity without scientific certification"]
    ];
    const grouped = catalog && areas.map(([area, caption]) => {
      const items = catalog.evidence.map((item, index) => ({ ...item, index }))
        .filter(item => item.area === area);
      return section(area, caption, items.length ? `<div class="list-stack">${items.map(item => `<div class="list-item">
        <div class="list-item-main"><h3>${safe(item.title)}</h3><p>${badge(item.classification,
          item.classification.includes("Mock") || item.classification.includes("Predetermined") ||
          item.classification.includes("NOT_CERTIFIED") ? "warn" : "info")}
        <span class="mono" title="${escapeHtml(item.path)}">${safe(item.path)}</span></p></div>
        <button class="text-link" data-evidence="${item.index}" aria-label="Inspect ${escapeHtml(item.title)}">
        ${icon("arrow-up-right")}</button></div>`).join("")}</div>` :
        empty("No indexed artifact", "No curated source file is available for this area.", "file-warning"));
    }).join("");
    return head("PROVENANCE", "Evidence", "File presence and hashes are shown without promoting artifacts to live proof.") +
      `<div class="notice">Classifications matter: test, mock, scenario-derived, predetermined, and historical evidence are not interchangeable with empirical incident resolution.</div>` +
      section("Preserved incident evidence", "G4 negative and interrupted attempts",
        catalog?.attempts?.length ? recordedRows(catalog.attempts.slice(0, 8)) :
          empty("No preserved attempts", "No allowlisted Stage 4 attempts are available."), nav("incidents", "All attempts")) +
      (grouped || empty("Evidence index unavailable", "The repository catalog could not be read.", "file-warning"));
  }

  function settingsPage() {
    const health = val("health");
    const config = val("config");
    const cluster = val("cluster");
    const integrity = val("integrity");
    const source = val("catalog")?.source_sha;
    return head("WORKSPACE", "Settings", "Read-only runtime and build information. Sensitive values are never displayed.") +
      section("Runtime observations", "This view does not perform a health certification",
        `<div class="surface surface-pad"><dl>
        ${kv("API process", badge(health?.status === "ok" ? "Reachable" : "Unavailable", health?.status === "ok" ? "good" : "neutral"))}
        ${kv("Cluster observation", badge(cluster?.ok ? "Read returned" : "Unavailable", cluster?.ok ? "info" : "neutral"))}
        ${kv("Model endpoint", badge(health?.agent_base ? "Configured; reachability unknown" : "Not configured", health?.agent_base ? "info" : "neutral"))}
        ${kv("Telemetry endpoint", badge(val("metrics") && Object.values(val("metrics")).some(v => v !== null) ? "Observation returned" : "Unavailable", "neutral"))}
        ${kv("Audit integrity", badge(integrity?.ok === true ? `Verified chain (${integrity.entries} entries)` :
          integrity?.ok === false ? "Failed" : "Unavailable", integrity?.ok === true ? "good" : integrity?.ok === false ? "bad" : "neutral"))}
        ${kv("Source revision", source ? `<span class="hash">${safe(source)}</span>` : "Unavailable")}
        </dl></div>`) +
      section("Configured integrations", "Configuration only; no connectivity or authorization is inferred",
        `<div class="surface surface-pad"><dl>
        ${kv("Coordinator", badge(config?.coordinator_url ? "Override configured" : "Same-origin default", "neutral"))}
        ${kv("Grafana", badge(config?.grafana_url ? "Configured" : "Not configured", "neutral"))}
        ${kv("Argo CD", badge(config?.argocd_url ? "Configured" : "Not configured", "neutral"))}
        ${kv("Boutique", badge(config?.boutique_url ? "Configured" : "Not configured", "neutral"))}
        ${kv("Kubernetes context", badge("Not exposed", "neutral"))}
        </dl></div>`) +
      `<div class="notice warn">Approval and remediation controls are intentionally absent. Real experiments use the governed harness and independent environment verification.</div>`;
  }

  function render() {
    statusBar();
    const current = route();
    document.querySelectorAll(".nav a, .sidebar-settings").forEach(link => {
      const active = link.dataset.page === current.page;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    document.getElementById("top-title").textContent = current.page === "incidents" && current.kind
      ? "Incident workspace" : PAGE_TITLES[current.page];
    document.title = `${document.getElementById("top-title").textContent} | AtlasOps`;
    if (state.loading) {
      main.innerHTML = `<div class="loading-skeleton" aria-label="Loading workspace"><span></span><span></span><span></span></div>`;
      return;
    }
    const views = {
      overview: overview, incidents: incidentsPage, agents: agentsPage, models: modelsPage,
      evaluations: evaluationsPage, runbooks: runbooksPage, evidence: evidencePage, settings: settingsPage
    };
    main.innerHTML = current.page === "incidents" && current.kind === "recorded" ? attemptPage(current.id)
      : current.page === "incidents" && current.kind === "live" ? liveDetail(current.id)
        : views[current.page]();
    if (window.lucide?.createIcons) window.lucide.createIcons();
  }

  let lastFocus = null;
  function showDrawer(content) {
    lastFocus = document.activeElement;
    document.getElementById("drawer-content").innerHTML = content;
    document.getElementById("drawer").hidden = false;
    document.getElementById("drawer-backdrop").hidden = false;
    document.getElementById("drawer-close").focus();
    if (window.lucide?.createIcons) window.lucide.createIcons();
  }
  function closeDrawer() {
    if (document.getElementById("drawer").hidden) return;
    document.getElementById("drawer").hidden = true;
    document.getElementById("drawer-backdrop").hidden = true;
    lastFocus?.focus();
  }
  const quickIcons = {
    overview: "layout-dashboard", incidents: "siren", agents: "workflow", models: "blocks",
    evaluations: "flask-conical", runbooks: "book-open", evidence: "file-check-2", settings: "settings-2"
  };
  let paletteFocus = null;
  function quickItems() {
    const catalog = val("catalog");
    return [
      ...Object.entries(PAGE_TITLES).map(([page, title]) =>
        ({ title, detail: "Workspace", glyph: quickIcons[page], hash: `#/${page}` })),
      ...(catalog?.attempts || []).map(item => ({
        title: item.id, detail: `${item.state} / historical evidence`, glyph: "file-warning",
        hash: `#/incidents/recorded/${encodeURIComponent(item.name)}`
      })),
      ...(catalog?.gates || []).map(gate => ({
        title: `${gate.gate} / ${gate.name}`, detail: gate.status, glyph: "flask-conical",
        hash: "#/evaluations", focus: { kind: "gate", id: gate.gate }
      })),
      ...(catalog?.runbooks || []).map(rb => ({
        title: `${rb.id} / ${rb.title}`, detail: rb.category, glyph: "book-open",
        hash: "#/runbooks", focus: { kind: "runbook", id: rb.id }
      }))
    ];
  }
  function renderPalette() {
    const query = document.getElementById("palette-input").value.trim().toLowerCase();
    state.quickResults = quickItems().filter(item =>
      `${item.title} ${item.detail}`.toLowerCase().includes(query)).slice(0, 9);
    state.quickIndex = 0;
    document.getElementById("palette-results").innerHTML = state.quickResults.length
      ? state.quickResults.map((item, index) => `<button type="button" class="palette-option ${index === 0 ? "active" : ""}"
        role="option" id="quick-option-${index}" aria-selected="${index === 0}" data-quick-index="${index}">
        ${icon(item.glyph)}<span><strong>${safe(item.title)}</strong><small>${safe(item.detail)}</small></span>
        ${icon("arrow-up-right")}</button>`).join("")
      : `<div class="palette-empty">No matching workspace item.</div>`;
    if (state.quickResults.length) document.getElementById("palette-input").setAttribute("aria-activedescendant", "quick-option-0");
    else document.getElementById("palette-input").removeAttribute("aria-activedescendant");
    if (window.lucide?.createIcons) window.lucide.createIcons();
  }
  function openPalette() {
    if (!document.getElementById("drawer").hidden) closeDrawer();
    document.getElementById("sidebar").classList.remove("open");
    document.getElementById("sidebar-backdrop").hidden = true;
    document.getElementById("menu-toggle").setAttribute("aria-expanded", "false");
    paletteFocus = document.activeElement;
    document.getElementById("palette").hidden = false;
    document.getElementById("palette-backdrop").hidden = false;
    document.getElementById("palette-input").value = "";
    renderPalette();
    document.getElementById("palette-input").focus();
  }
  function closePalette() {
    if (document.getElementById("palette").hidden) return;
    document.getElementById("palette").hidden = true;
    document.getElementById("palette-backdrop").hidden = true;
    paletteFocus?.focus();
  }
  function revealPendingFocus() {
    const pending = state.pendingFocus;
    state.pendingFocus = null;
    if (!pending) return;
    const selector = pending.kind === "gate" ? "[data-gate]" : "[data-runbook]";
    [...main.querySelectorAll(selector)].find(button =>
      (pending.kind === "gate" ? button.dataset.gate : button.dataset.runbook) === pending.id)?.click();
  }
  function activateQuick(index) {
    const item = state.quickResults[index];
    if (!item) return;
    closePalette();
    state.pendingFocus = item.focus || null;
    if (location.hash === item.hash) {
      render();
      revealPendingFocus();
    } else {
      location.hash = item.hash;
    }
  }
  document.getElementById("quick-open").addEventListener("click", openPalette);
  document.getElementById("palette-backdrop").addEventListener("click", closePalette);
  document.getElementById("palette-input").addEventListener("input", renderPalette);
  document.getElementById("palette-results").addEventListener("click", event => {
    const option = event.target.closest("[data-quick-index]");
    if (option) activateQuick(Number(option.dataset.quickIndex));
  });
  document.getElementById("drawer-close").addEventListener("click", closeDrawer);
  document.getElementById("drawer-backdrop").addEventListener("click", closeDrawer);
  document.getElementById("refresh").addEventListener("click", refresh);
  document.getElementById("sidebar-backdrop").addEventListener("click", () => {
    document.getElementById("sidebar").classList.remove("open");
    document.getElementById("sidebar-backdrop").hidden = true;
    document.getElementById("menu-toggle").setAttribute("aria-expanded", "false");
  });
  document.getElementById("menu-toggle").addEventListener("click", () => {
    const open = document.getElementById("sidebar").classList.toggle("open");
    document.getElementById("sidebar-backdrop").hidden = !open;
    document.getElementById("menu-toggle").setAttribute("aria-expanded", String(open));
  });
  window.addEventListener("hashchange", () => {
    closeDrawer();
    closePalette();
    document.getElementById("sidebar").classList.remove("open");
    document.getElementById("sidebar-backdrop").hidden = true;
    document.getElementById("menu-toggle").setAttribute("aria-expanded", "false");
    render();
    main.focus({ preventScroll: true });
    window.scrollTo(0, 0);
    revealPendingFocus();
  });
  document.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (document.getElementById("palette").hidden) openPalette();
      else closePalette();
      return;
    }
    const palette = document.getElementById("palette");
    if (!palette.hidden) {
      if (event.key === "Escape") { closePalette(); return; }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const delta = event.key === "ArrowDown" ? 1 : -1;
        state.quickIndex = Math.max(0, Math.min(state.quickResults.length - 1, state.quickIndex + delta));
        palette.querySelectorAll("[data-quick-index]").forEach((option, index) => {
          const selected = index === state.quickIndex;
          option.classList.toggle("active", selected);
          option.setAttribute("aria-selected", String(selected));
          if (selected) option.scrollIntoView({ block: "nearest" });
        });
        if (state.quickResults.length) document.getElementById("palette-input").setAttribute(
          "aria-activedescendant", `quick-option-${state.quickIndex}`);
        return;
      }
      if (event.key === "Enter" && document.activeElement.id === "palette-input") {
        event.preventDefault(); activateQuick(state.quickIndex); return;
      }
      if (event.key === "Tab") {
        const input = document.getElementById("palette-input");
        const last = palette.querySelector("[data-quick-index]:last-of-type") || input;
        if (event.shiftKey && document.activeElement === input) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); input.focus();
        }
      }
      return;
    }
    const drawer = document.getElementById("drawer");
    if (drawer.hidden) return;
    if (event.key === "Escape") closeDrawer();
    if (event.key === "Tab") {
      const focusable = [...drawer.querySelectorAll("button, a[href], input, select")];
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    }
  });
  document.addEventListener("click", event => {
    const target = event.target.closest("[data-process-step], [data-attempt], [data-live], [data-evidence], [data-runbook], [data-attempt-evidence], [data-eval-filter], [data-gate], [data-scenario]");
    if (!target) return;
    if (target.hasAttribute("data-process-step")) {
      const view = target.closest(".process-view");
      view.querySelectorAll("[data-process-step]").forEach(step => {
        const selected = step === target;
        step.classList.toggle("selected", selected);
        step.setAttribute("aria-pressed", String(selected));
      });
      view.querySelector(".process-inspector-status").textContent = target.dataset.status;
      view.querySelector(".process-inspector-title").textContent = target.dataset.title;
      view.querySelector(".process-inspector-note").textContent = target.dataset.note;
      return;
    }
    if (target.dataset.attempt) location.hash = `#/incidents/recorded/${encodeURIComponent(target.dataset.attempt)}`;
    if (target.dataset.live) location.hash = `#/incidents/live/${encodeURIComponent(target.dataset.live)}`;
    if (target.dataset.evalFilter) { state.evaluationFilter = target.dataset.evalFilter; render(); }
    if (target.dataset.gate) {
      const gate = val("catalog")?.gates?.find(item => item.gate === target.dataset.gate);
      if (gate) showDrawer(`<h2>${safe(gate.gate)} / ${safe(gate.name)}</h2>
        <p>${badge(gate.status, gateTone(gate.status))}</p><dl>
        ${kv("Stage", safe(gate.stage))}
        ${kv("Recorded context", safe(gateNotes[gate.gate] || "See the checked-in status source for the scoped evidence."))}
        ${kv("Deliverable", safe(gate.deliverable))}
        ${kv("Source", `<span class="hash">docs/project/MASTER_PIPELINE_STATUS.md</span>`)}
        </dl><p class="meta-note">Repository governance snapshot; not a live environment check.</p>
        <a class="text-link" href="#/evidence">Browse evidence ${icon("arrow-up-right")}</a>`);
    }
    if (target.dataset.scenario) {
      const scenario = val("scenarios")?.scenarios?.find(item =>
        item.scenario_id === target.dataset.scenario);
      if (scenario) showDrawer(`<h2>${safe(scenario.scenario_id)}</h2>
        <p>${badge("Frozen evaluation definition", "info")}</p><p>${safe(scenario.description)}</p><dl>
        ${kv("Tier", safe(scenario.tier.replaceAll("_", " ")))}
        ${kv("Expected alert", safe(scenario.expected_alert))}
        ${kv("Frozen target", safe((scenario.target_services || []).join(", ") || "Unavailable"))}
        </dl><p class="meta-note">Evaluation-only truth. Inspecting this record does not inject a fault or run an incident.</p>`);
    }
    if (target.dataset.evidence) {
      const item = val("catalog")?.evidence?.[Number(target.dataset.evidence)];
      if (item) showDrawer(`<h2>${safe(item.title)}</h2><p>${badge(item.classification, "info")}</p><dl>
        ${kv("Source", `<span class="hash">${safe(item.path)}</span>`)}
        ${kv("SHA-256", `<span class="hash">${safe(item.sha256)}</span>`)}
        ${kv("Meaning", "Preserved file presence and integrity identifier; not a live evaluation verdict.")}</dl>`);
    }
    if (target.dataset.attemptEvidence) {
      const detail = state.details[target.dataset.attemptEvidence]?.value;
      if (detail) showDrawer(`<h2>${safe(detail.id)}</h2><p>${badge("Historical evidence", "info")}</p><dl>
        ${kv("Governance", badge(detail.governance, "bad"))}
        ${kv("Source", `<span class="hash">${safe(detail.source)}</span>`)}
        ${kv("SHA-256", `<span class="hash">${safe(detail.sha256)}</span>`)}
        ${kv("Model recorded", safe(detail.model))}
        ${kv("Recorded at", fmtTime(detail.timestamp))}
        </dl><p class="meta-note">Selected fields only. The hash identifies the preserved source record.</p>`);
    }
    if (target.dataset.runbook) {
      const rb = val("catalog")?.runbooks?.find(item => item.id === target.dataset.runbook);
      if (rb) showDrawer(`<h2>${safe(rb.title)}</h2><p>${badge("Catalog definition", "info")}</p>
        <p>${safe(rb.description)}</p><dl>${kv("ID", `<span class="mono">${safe(rb.id)}</span>`)}
        ${kv("Category", safe(rb.category))}${kv("Suggested tools", safe(rb.tools.join(", ")))}
        ${kv("Validation", badge("Not established here", "neutral"))}</dl>
        <p class="meta-note">Catalog text is advisory; no remediation is dispatched from this console.</p>`);
    }
  });
  document.addEventListener("input", event => {
    if (event.target.id === "incident-search") {
      state.incidentSearch = event.target.value;
      document.getElementById("recorded-list").innerHTML = renderRecordedList();
    }
    if (event.target.id === "runbook-search") {
      state.runbookSearch = event.target.value;
      document.getElementById("runbook-list").innerHTML = runbookList();
      if (window.lucide?.createIcons) window.lucide.createIcons();
    }
    if (event.target.id === "scenario-search") {
      state.scenarioSearch = event.target.value;
      document.getElementById("scenario-list").innerHTML = scenarioList();
    }
  });
  document.addEventListener("change", event => {
    if (event.target.id === "incident-sort") {
      state.incidentSort = event.target.value;
      document.getElementById("recorded-list").innerHTML = renderRecordedList();
    }
    if (event.target.id === "scenario-tier") {
      state.scenarioTier = event.target.value;
      document.getElementById("scenario-list").innerHTML = scenarioList();
    }
  });
  document.addEventListener("submit", async event => {
    if (event.target.id !== "recommend-form") return;
    event.preventDefault();
    const form = event.target;
    const output = document.getElementById("rec-results");
    const button = form.querySelector("button[type=submit]");
    const fields = new FormData(form);
    button.disabled = true;
    output.innerHTML = `<div class="loading-state"><span class="spinner"></span>Ranking catalog runbooks</div>`;
    try {
      const data = await request("/api/recommender/recommend", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          alert_name: String(fields.get("alert_name")).slice(0, 100),
          service: String(fields.get("service")).slice(0, 100),
          symptoms: String(fields.get("symptoms")).slice(0, 500),
          top_k: Number(fields.get("top_k"))
        })
      });
      output.innerHTML = `<div class="section-divider"><h3>Ranked suggestions</h3>
        <p class="section-caption">Scores are ranking signals, not recovery probabilities.</p>
        ${(data.recommendations || []).map((rec, index) => `<div class="evidence-row">
        <div><button class="row-button" data-runbook="${escapeHtml(rec.runbook_id)}">${index + 1}. ${safe(rec.title)}</button>
        <div class="meta-note">${safe(rec.runbook_id)} / ${safe(rec.explanation)}</div></div>
        ${badge(`Score ${rec.score}`, "info")}</div>`).join("") || `<p class="muted">No recommendations returned.</p>`}</div>`;
    } catch (err) {
      output.innerHTML = `<div class="notice bad">Ranking unavailable: ${safe(err.message)}.</div>`;
    } finally { button.disabled = false; }
  });
  if (!location.hash) location.hash = "#/overview";
  main.dataset.phaseProjection = window.AtlasOpsLiveIncident?.projectLiveIncident ? "ready" : "unavailable";
  refresh();
})();
