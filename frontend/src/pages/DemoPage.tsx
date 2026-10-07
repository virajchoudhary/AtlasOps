import { useEffect, useReducer, useState } from "react";
import { ArrowRight, Check, Download, Pause, Play, RotateCcw, ShieldCheck, X } from "lucide-react";
import { fetchRehearsal } from "../api/client";
import { PageHeading } from "../components/SectionHeading";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  initialWorkflow, outcome, stages, transcript, workflowReducer, type RehearsalCatalog,
} from "../demo/workflow";
import "../demo/demo.css";
import { LiveStatus } from "../components/LiveStatus";
import { IncidentMonitor } from "../components/IncidentMonitor";
import { OperatorPanel } from "../components/OperatorPanel";

export function DemoPage() {
  const [operatorEnabled, setOperatorEnabled] = useState(false);
  const [fixtures, setFixtures] = useState<RehearsalCatalog | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadKey, setLoadKey] = useState(0);
  const [scenarioId, setScenarioId] = useState("");
  const [verifierHealthy, setVerifierHealthy] = useState(true);
  const [state, dispatch] = useReducer(workflowReducer, initialWorkflow);
  const [playing, setPlaying] = useState(false);
  const [inspected, setInspected] = useState<number | null>(null);
  const entries = transcript(state);
  const selected = entries[Math.min(inspected ?? entries.length - 1, entries.length - 1)];
  const result = outcome(state);
  const awaitingApproval = state.scenario !== null && state.step === 3;
  const finished = state.scenario !== null && state.step === 6;
  const selectedScenario = fixtures?.scenarios.find((scenario) => scenario.id === scenarioId)
    ?? fixtures?.scenarios[0];

  useEffect(() => {
    const controller = new AbortController();
    setLoadFailed(false);
    fetchRehearsal(controller.signal)
      .then(setFixtures)
      .catch(() => { if (!controller.signal.aborted) setLoadFailed(true); });
    return () => controller.abort();
  }, [loadKey]);

  useEffect(() => {
    if (!playing || !state.scenario || awaitingApproval || finished) return;
    const timer = window.setTimeout(() => dispatch({ type: "next" }), 1500);
    return () => window.clearTimeout(timer);
  }, [playing, state, awaitingApproval, finished]);

  function start() {
    if (!selectedScenario) return;
    dispatch({ type: "start", scenario: selectedScenario, verifierHealthy });
    setInspected(null);
    setPlaying(false);
  }

  function downloadTranscript() {
    const document = {
      classification: fixtures?.classification,
      agent_output: fixtures?.agent_output,
      experimental_evidence: false,
      model_inference: false,
      real_tool_executed: false,
      scenario: state.scenario,
      simulated_outcome: result,
      entries,
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(document, null, 2)], { type: "application/json" }));
    const link = window.document.createElement("a");
    link.href = url;
    link.download = "atlasops-simulated-rehearsal.json";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  return (
    <div className="page-stack demo-page">
      <PageHeading eyebrow="Presentation console" title="Incident workflow"
        description="Live service observations, selected runner capture, and a separate scripted rehearsal."
        actions={<Badge tone="outline">{operatorEnabled ? "Governed operator mode" : "Read-only presentation"}</Badge>} />
      <LiveStatus />
      <OperatorPanel onMode={setOperatorEnabled} />
      <IncidentMonitor />
      <div className="demo-boundary" role="note">
        <ShieldCheck size={19} aria-hidden="true" />
        <span><strong>Scripted rehearsal below, not a live incident.</strong> Research evidence and gate status are unchanged. G4 remains NOT_PASSED.</span>
        <Badge tone="amber">SYNTHETIC / NON-LIVE / NON-EMPIRICAL</Badge>
      </div>

      <section className="demo-setup" aria-label="Rehearsal setup">
        <label className="demo-field">
          <span>Scenario</span>
          <select aria-label="Scenario" value={scenarioId || selectedScenario?.id || ""} onChange={(event) => setScenarioId(event.target.value)}
            disabled={!!state.scenario || !fixtures}>
            {!fixtures && <option value="">{loadFailed ? "Fixtures unavailable" : "Reading fixtures"}</option>}
            {fixtures?.scenarios.map((scenario) => <option key={scenario.id} value={scenario.id}>{scenario.title}</option>)}
          </select>
        </label>
        <label className="demo-field">
          <span>Simulated verifier observation</span>
          <select aria-label="Simulated verifier observation" value={verifierHealthy ? "healthy" : "unhealthy"}
            onChange={(event) => setVerifierHealthy(event.target.value === "healthy")} disabled={!!state.scenario}>
            <option value="healthy">Environment recovered</option>
            <option value="unhealthy">Fault still present</option>
          </select>
        </label>
        <Button variant="primary" disabled={!selectedScenario || !!state.scenario} onClick={start}>
          <Play size={16} aria-hidden="true" /> Start rehearsal
        </Button>
        {loadFailed && <div role="alert">Rehearsal fixtures unavailable.
          <Button variant="quiet" onClick={() => setLoadKey((key) => key + 1)}>Retry</Button>
        </div>}
      </section>

      <section className="demo-workflow" aria-label="Simulated incident workflow">
        <div className="demo-toolbar">
          <div>
            <p className="eyebrow">{state.scenario?.service ?? selectedScenario?.service ?? "No scenario"}</p>
            <h2>{state.scenario ? finished ? "Rehearsal complete" : awaitingApproval ? "Approval pending" : stages[state.step] : "Ready to rehearse"}</h2>
          </div>
          <div className="demo-actions">
            <Button variant="quiet" size="icon" title="Reset rehearsal" aria-label="Reset rehearsal"
              disabled={!state.scenario} onClick={() => {
                dispatch({ type: "reset" }); setPlaying(false); setInspected(null);
              }}><RotateCcw size={17} aria-hidden="true" /></Button>
            <Button variant="quiet" size="icon" title="Download simulated transcript" aria-label="Download simulated transcript"
              disabled={!finished} onClick={downloadTranscript}><Download size={17} aria-hidden="true" /></Button>
            <Button variant="secondary" disabled={!state.scenario || awaitingApproval || finished}
              onClick={() => { setInspected(null); setPlaying((value) => !value); }}>
              {playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
              {playing ? "Pause" : "Play"}
            </Button>
            <Button variant="outline" disabled={!state.scenario || awaitingApproval || finished}
              onClick={() => { setPlaying(false); setInspected(null); dispatch({ type: "next" }); }}>
              Next stage <ArrowRight size={16} aria-hidden="true" />
            </Button>
          </div>
        </div>
        <ol className="demo-stages" aria-label="Workflow stages">
          {stages.map((stage, index) => {
            const entryIndex = entries.findIndex((entry) => entry.stage === stage);
            const skipped = finished && entryIndex < 0;
            const active = selected?.stage === stage;
            return <li key={stage}>
              <button type="button" disabled={entryIndex < 0} aria-current={active ? "step" : undefined}
                className={`demo-stage ${active ? "is-current" : ""} ${skipped ? "is-skipped" : ""}`}
                onClick={() => setInspected(entryIndex)}>
                <span className="demo-stage-number">{skipped ? <X size={14} /> : index + 1}</span>
                <strong>{stage}</strong>
                <small>{skipped ? "Skipped" : entryIndex >= 0 ? "Recorded" : "Pending"}</small>
              </button>
            </li>;
          })}
        </ol>

        {awaitingApproval && <div className="demo-approval" role="region" aria-label="Simulated P1 decision">
          <div>
            <Badge tone="amber">P1 APPROVAL REQUIRED</Badge>
            <p>Proposed fixture action: <code>{state.scenario?.proposal.tool}</code></p>
            <p>Decision applies only to this rehearsal. No operational approval is issued.</p>
          </div>
          <div className="demo-actions">
            <Button variant="primary" onClick={() => {
              setInspected(null); setPlaying(false); dispatch({ type: "decide", decision: "approved" });
            }}><Check size={16} /> Approve simulation</Button>
            <Button variant="outline" onClick={() => {
              setInspected(null); setPlaying(false); dispatch({ type: "decide", decision: "rejected" });
            }}><X size={16} /> Reject</Button>
            <Button variant="quiet" onClick={() => {
              setInspected(null); setPlaying(false); dispatch({ type: "decide", decision: "timeout" });
            }}>Simulate timeout</Button>
          </div>
        </div>}

        {finished && <div className={`demo-outcome demo-outcome--${result}`} role="status">
          <strong>{result === "resolved" ? "SIMULATED RECOVERY" : result === "unresolved" ? "SIMULATED UNRESOLVED INCIDENT" : "SIMULATED REMEDIATION BLOCKED"}</strong>
          <span>{entries.at(-1)?.summary}</span>
        </div>}

        {selected ? <div className="demo-inspector" aria-label="Stage detail">
          <div>
            <p className="eyebrow">Scripted stage output</p>
            <h3>{selected.stage}</h3>
            <p>{selected.summary}</p>
            <p className="muted-copy">No live observation or model inference.</p>
          </div>
          <pre aria-label="Scripted output JSON">{JSON.stringify(selected.data, null, 2)}</pre>
        </div> : <p className="demo-empty">No rehearsal started.</p>}
      </section>

      {state.scenario && <section className="demo-log" aria-label="Simulated audit trail">
        <h2>Rehearsal transcript</h2>
        <ol>{entries.map((entry, index) => <li key={entry.stage}>
          <span>{String(index + 1).padStart(2, "0")}</span>
          <button type="button" onClick={() => setInspected(index)}>{entry.stage}</button>
          <p>{entry.summary}</p>
        </li>)}</ol>
        <p className="demo-provenance">Frozen scenario reference: <code>{state.scenario.source}</code></p>
        <p className="demo-provenance">Frozen manifest SHA-256 (LF-normalized): <code>{state.scenario.sha256}</code></p>
        <p className="demo-provenance">Checkout bytes SHA-256: <code>{state.scenario.checkout_sha256}</code></p>
      </section>}
    </div>
  );
}
