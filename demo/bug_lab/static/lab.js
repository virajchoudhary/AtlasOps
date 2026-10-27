"use strict";

(function () {
  const POLL_MS = 1000;
  const STAGE_LABELS = { monitor: "Monitor", triage: "Triage", diagnosis: "Diagnosis", propose: "Patch",
    approval: "Approval", apply: "Apply", verification: "Verify", comms: "Comms" };
  let state = null;
  let lastKey = "";
  let lastIncidentId = null;
  let errorTimer = null;

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function fill(node, ...children) {
    node.replaceChildren(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));
  }

  function showError(message) {
    const banner = document.getElementById("error");
    banner.textContent = message;
    banner.hidden = false;
    clearTimeout(errorTimer);
    errorTimer = setTimeout(() => { banner.hidden = true; }, 6000);
  }

  async function post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Bug-Lab": "1" },
      body: JSON.stringify(body || {}),
    });
    if (!response.ok) {
      let detail = response.statusText;
      try { detail = (await response.json()).detail || detail; } catch (_) { /* keep status text */ }
      throw new Error(detail);
    }
    return response.json();
  }

  async function act(path, body) {
    try {
      await post(path, body);
      await refresh();
    } catch (error) {
      showError(error.message);
    }
  }

  function diffView(lines) {
    return el("pre", { class: "diff" }, (lines || []).map((line) => {
      const kind = line.startsWith("@@") ? "hunk"
        : line.startsWith("+") && !line.startsWith("+++") ? "add"
          : line.startsWith("-") && !line.startsWith("---") ? "del" : "";
      return el("span", { class: kind }, line + "\n");
    }));
  }

  function seconds(value) {
    return value === null || value === undefined ? "—" : value + " s";
  }

  // --- panels ---------------------------------------------------------------

  function renderBackend() {
    const pill = document.getElementById("backend");
    pill.textContent = "Agents: " + state.backend.label;
    pill.className = "pill" + (state.backend.mode === "llm" ? " model" : "");
    pill.title = state.backend.note || "";
  }

  function renderCatalog() {
    const blocked = state.active || state.injected.length > 0;
    const reason = state.active ? "Wait for the current incident to close"
      : state.injected.length ? "Waiting for the monitor to detect the last bug" : "";
    fill(document.getElementById("catalog"), ...state.catalog.map((bug) => el("div", { class: "bug" },
      el("div", {}, el("strong", { text: bug.name }), el("span", { class: "tag", text: bug.kind })),
      el("button", {
        class: "button", type: "button", disabled: blocked, title: reason || "Edits " + bug.file,
        onclick: () => act("/lab/api/inject", { bug: bug.id }),
      }, "Inject"),
      el("p", { text: bug.summary }),
    )));
    document.getElementById("approve-all").checked = state.settings.require_approval_all;
  }

  function renderSite() {
    const site = state.site;
    const text = site.changed_files.length
      ? "Changed from the release: " + site.changed_files.join(", ") + "."
      : "The store matches the release.";
    const paused = site.monitoring_paused ? " Monitoring is paused for this broken version: reset the store or inject another bug." : "";
    document.getElementById("site-status").textContent = text + paused;
  }

  function stageStates(incident) {
    const reached = new Set(incident.events.map((event) => (event.stage === "rollback" ? "propose" : event.stage)));
    const closed = incident.closed_at !== null;
    const failed = closed && incident.status !== "resolved";
    const lastStage = incident.events.length ? incident.events[incident.events.length - 1].stage : "monitor";
    return state.stages.map((stage) => {
      let status = reached.has(stage.key) ? "done" : "";
      if (!closed && incident.stage === stage.key) status = "active";
      if (failed && (lastStage === stage.key || (lastStage === "rollback" && stage.key === "propose"))) status = "blocked";
      return el("div", { class: "stage " + status, title: stage.agent }, el("b", { text: STAGE_LABELS[stage.key] || stage.key }));
    });
  }

  function nowBox(incident) {
    if (incident.closed_at !== null) {
      const label = {
        resolved: "Resolved and verified", escalated: "Escalated to you", unverified: "Applied but not verified",
        cancelled: "Cancelled",
      }[incident.status] || incident.status;
      return el("div", { class: "now closed" }, el("div", {},
        el("div", { class: "who", text: label }),
        el("div", { class: "what", text: incident.resolved_by ? "Fixed by " + incident.resolved_by : incident.outcome || "" })));
    }
    const last = incident.events[incident.events.length - 1];
    const waiting = incident.status === "awaiting_approval";
    return el("div", { class: "now" }, waiting ? null : el("div", { class: "spinner", "aria-hidden": "true" }),
      el("div", {},
        el("div", { class: "who", text: waiting ? "Waiting for your approval" : incident.active_agent + " is working" }),
        el("div", { class: "what", text: (!waiting && incident.active_backend ? "Using " + incident.active_backend + ". " : "") + (last ? last.text : "") })));
  }

  function truthCard(injected) {
    if (!injected.length) {
      return el("div", { class: "card truth" }, el("div", { class: "label", text: "What you injected" }),
        el("p", { text: "Nothing from the lab: the monitor found this on its own." }));
    }
    return el("div", { class: "card truth" }, el("div", { class: "label", text: "What you injected (hidden from the agents)" }),
      injected.map((bug) => [
        el("p", {}, el("strong", { text: bug.name }), el("span", { class: "tag", text: bug.kind })),
        el("p", { text: bug.summary + " File: " + bug.file + "." }),
        el("details", { "data-key": "truth-" + bug.id }, el("summary", { text: "Show the change" }), diffView(bug.diff)),
      ]));
  }

  function findingCard(incident) {
    const diagnosis = incident.diagnosis;
    const children = [el("div", { class: "label", text: "What the agents found" })];
    if (!diagnosis) {
      children.push(el("p", { text: incident.closed_at === null ? "Diagnosis in progress…" : "No diagnosis was produced." }));
    } else {
      children.push(el("p", { text: diagnosis.root_cause }), el("p", { class: "label", text: diagnosis.file + " · " + diagnosis.author }));
      if (incident.diagnosis_matched !== null) {
        children.push(el("p", { class: "match " + (incident.diagnosis_matched ? "yes" : "no"),
          text: incident.diagnosis_matched ? "Matches the file you broke." : "Does not match the file you broke." }));
      }
    }
    return el("div", { class: "card" }, children);
  }

  function approvalCard(incident) {
    const approval = incident.approval;
    if (!approval || !approval.required || approval.decision || incident.closed_at !== null) return null;
    const decide = (decision) => act("/lab/api/approval", { incident_id: incident.id, digest: approval.digest, decision: decision });
    return el("div", { class: "approval" },
      el("strong", { text: approval.policy + "." }),
      el("p", { text: "Review the patch below. Approving applies exactly this change; rejecting leaves the store as it is." }),
      el("div", { class: "digest", text: "change sha256 " + approval.digest }),
      el("div", { class: "actions" },
        el("button", { class: "button good", type: "button", onclick: () => decide("approve") }, "Approve and apply"),
        el("button", { class: "button bad", type: "button", onclick: () => decide("reject") }, "Reject"),
        el("span", { id: "countdown", class: "digest", "data-expires": String(approval.expires_at) })));
  }

  function patchCard(patch) {
    if (!patch) return null;
    return el("div", { class: "card" },
      el("div", { class: "label", text: (patch.kind === "rollback" ? "Rollback" : "Patch") + " by " + patch.author }),
      el("p", { text: patch.explanation }), diffView(patch.diff));
  }

  function timeline(incident) {
    return el("ol", { class: "timeline", id: "timeline" }, incident.events.map((event) => el("li", { class: event.kind },
      el("span", { class: "t", text: "+" + event.t.toFixed(1) }),
      el("span", { class: "agent", text: event.agent }),
      el("span", { class: "body" }, el("span", { class: "text", text: event.text }),
        event.detail ? el("span", { class: "detail", text: event.detail }) : null))));
  }

  function pendingCard() {
    if (!state.injected.length) return null;
    const bug = state.injected[0];
    const age = Math.max(0, Math.round(Date.now() / 1000 - bug.at));
    return el("div", { class: "card truth" },
      el("p", {}, el("strong", { text: "You injected: " + bug.name }), el("span", { class: "tag", text: bug.kind })),
      el("p", {}, "Waiting for the monitor to notice (", el("span", { id: "pending-age", "data-at": String(bug.at), text: age + " s" }),
        "). Browser-only bugs need the store open: the preview on the right counts."),
      el("details", { "data-key": "pending-" + bug.id }, el("summary", { text: "Show the change" }), diffView(bug.diff)));
  }

  function renderIncident() {
    const root = document.getElementById("incident");
    const incident = state.incident;
    const previous = document.getElementById("timeline");
    const stick = !previous || previous.scrollTop + previous.clientHeight >= previous.scrollHeight - 24;
    const pending = pendingCard();
    if (!incident) {
      fill(root, pending || el("div", { class: "empty", text: "No incident yet. Inject a bug to start." }));
      return;
    }
    fill(root,
      pending,
      pending ? el("h3", { text: "Previous incident" }) : null,
      el("div", { class: "incident-head" },
        el("h3", { text: incident.id + " · " + (incident.title || "Investigating") }),
        incident.severity ? el("span", { class: "sev " + incident.severity, text: incident.severity }) : null,
        el("span", { class: "status " + incident.status, text: incident.status.replace("_", " ") })),
      nowBox(incident),
      el("div", { class: "pipeline" }, stageStates(incident)),
      el("div", { class: "pair" }, truthCard(incident.injected), findingCard(incident)),
      approvalCard(incident),
      el("h3", { text: "Change" }),
      patchCard(incident.patch) || el("p", { class: "hint", text: "No change proposed yet." }),
      el("h3", { text: "Agent timeline" }),
      timeline(incident),
      incident.summary && incident.closed_at !== null
        ? el("div", { class: "summary" + (incident.status === "resolved" ? "" : " fail") },
          el("strong", { text: "Comms agent: " }), incident.summary)
        : null);
    const list = document.getElementById("timeline");
    if (stick || incident.id !== lastIncidentId) list.scrollTop = list.scrollHeight;
    lastIncidentId = incident.id;
  }

  function checkList(title, checks, note) {
    return [el("h3", { text: title }), note ? el("p", { class: "hint", text: note }) : null,
      el("ul", { class: "checks" }, checks.map((check) => el("li", {},
        el("span", { class: "dot " + (check.ok === true ? "ok" : check.ok === false ? "fail" : "") }),
        el("span", {}, check.label, check.ok === false && check.detail ? el("span", { class: "detail", text: check.detail }) : null))))];
  }

  function renderChecks() {
    const checks = state.checks;
    const browserNote = !checks.browser.length ? "No report yet: the store page reports after it loads."
      : checks.browser_current ? "From this version of the store." : "From an older version; waiting for the page to reload.";
    fill(document.getElementById("checks"),
      ...checkList("Server checks", checks.server, checks.server_current ? "" : "Re-checking the current version…"),
      ...checkList("Browser checks", checks.browser, browserNote));
  }

  function renderHistory() {
    const rows = state.history;
    const root = document.getElementById("history");
    if (!rows.length) {
      fill(root, el("p", { class: "hint", text: "Closed incidents appear here." }));
      return;
    }
    fill(root, el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Incident", "Bug you injected", "Severity", "Outcome", "Fixed by",
        "Diagnosis matched", "Time to detect", "Time to resolve"].map((name) => el("th", { text: name })))),
      el("tbody", {}, rows.map((row) => el("tr", {},
        el("td", { text: row.id }), el("td", { text: row.injected.join(", ") || "—" }),
        el("td", { text: row.severity || "—" }), el("td", { text: row.status }),
        el("td", { text: row.resolved_by || "—" }),
        el("td", { text: row.diagnosis_matched === null ? "—" : row.diagnosis_matched ? "Yes" : "No" }),
        el("td", { text: seconds(row.detect_s) }), el("td", { text: seconds(row.resolve_s) })))))));
  }

  function updateCountdown() {
    const node = document.getElementById("countdown");
    if (!node) return;
    const left = Math.max(0, Math.round(Number(node.dataset.expires) - Date.now() / 1000));
    node.textContent = "Expires in " + Math.floor(left / 60) + ":" + String(left % 60).padStart(2, "0") + " (then nothing is changed)";
  }

  function renderKey(snapshot) {
    return JSON.stringify(snapshot, (key, value) => (key === "browser_age_s" || key === "expires_in" ? undefined : value));
  }

  function render() {
    const key = renderKey(state);
    if (key !== lastKey) {
      lastKey = key;
      const open = new Set(Array.from(document.querySelectorAll("details[open]")).map((node) => node.dataset.key));
      renderBackend();
      renderCatalog();
      renderSite();
      renderIncident();
      renderChecks();
      renderHistory();
      document.querySelectorAll("details[data-key]").forEach((node) => { if (open.has(node.dataset.key)) node.open = true; });
    }
    const age = document.getElementById("pending-age");
    if (age) age.textContent = Math.max(0, Math.round(Date.now() / 1000 - Number(age.dataset.at))) + " s";
    updateCountdown();
  }

  async function refresh() {
    const response = await fetch("/lab/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error("The lab server returned " + response.status);
    state = await response.json();
    render();
  }

  async function poll() {
    try {
      await refresh();
    } catch (error) {
      showError("Lost contact with the lab server: " + error.message);
    } finally {
      setTimeout(poll, POLL_MS);
    }
  }

  document.getElementById("reset").addEventListener("click", () => act("/lab/api/reset"));
  document.getElementById("approve-all").addEventListener("change", (event) =>
    act("/lab/api/settings", { require_approval_all: event.target.checked }));
  poll();
})();
