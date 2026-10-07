import { useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";

interface RunCapture {
  enabled: boolean;
  available: boolean;
  experiment_id: string;
  observed_at: string;
  status: string;
  process_running: boolean | null;
  reserved: boolean;
  attempt_state: string | null;
  failure: string | null;
  policy_block?: string | null;
  comms_status?: string | null;
  gate_g4_pass: boolean | null;
  env_resolved: boolean | null;
  cleanup_verified_zero: boolean | null;
  approval: string | null;
  severity: string | null;
  services: string[];
  phases: { id: string; label: string; observed: boolean }[];
  actions: { tool: string | null; target: string | null; namespace: string | null; success: boolean | null }[];
  proposal: { tool: string | null; target: string | null; namespace: string | null } | null;
  sources: { name: string; sha256: string }[];
}

const verdict = (value: boolean | null | undefined) => value === true ? "Yes" : value === false ? "No" : "Unavailable";

export function IncidentMonitor() {
  const [capture, setCapture] = useState<RunCapture | null>(null);
  const [failed, setFailed] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    fetch("/api/incident-monitor", { method: "GET", cache: "no-store", signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Capture unavailable");
        return response.json() as Promise<RunCapture>;
      })
      .then((value) => { if (!controller.signal.aborted) { setCapture(value); setFailed(false); } })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);
  useEffect(() => {
    if (capture?.process_running !== true) return;
    const timer = window.setTimeout(() => setRefresh((value) => value + 1), 3000);
    return () => window.clearTimeout(timer);
  }, [capture, refresh]);
  if (!capture?.enabled && !failed) return null;
  return <section className="incident-monitor" aria-label="Governed incident monitor">
    <div className="demo-toolbar">
      <div><p className="eyebrow">Recorded execution</p><h2><Activity size={18} aria-hidden="true" /> Governed incident monitor</h2></div>
      <Button variant="quiet" size="icon" aria-label="Refresh incident capture" title="Refresh incident capture"
        disabled={loading} onClick={() => setRefresh((value) => value + 1)}><RefreshCw size={16} /></Button>
    </div>
    <p>Read-only runner capture. This is separate from the scripted rehearsal and does not authorize a run.</p>
    {failed ? <p role="alert">Incident capture unavailable. Previous observations are not shown as current results.</p>
      : capture && <>
        <div className="demo-actions">
          <code>{capture.experiment_id}</code>
          <Badge tone="outline">RECORDED / NOT A SIMULATION</Badge>
          <Badge tone={capture.status.includes("FAILED") || capture.status.includes("NEGATIVE") ? "rose" : "outline"}>
            {capture.status}
          </Badge>
        </div>
        {capture.failure && <p className="monitor-failure" role="status">Recorded failure: <code>{capture.failure}</code></p>}
        {capture.policy_block && <p className="monitor-failure" role="status">Recorded policy block: <code>{capture.policy_block}</code>. No action approval or execution is implied.</p>}
        <dl className="monitor-facts">
          <div><dt>Runner currently active</dt><dd>{verdict(capture.process_running)}</dd></div>
          <div><dt>Attempt reserved</dt><dd>{verdict(capture.reserved)}</dd></div>
          <div><dt>Recorded P1 decision</dt><dd>{capture.approval ?? "Unavailable"}</dd></div>
          <div><dt>Environment resolved</dt><dd>{verdict(capture.env_resolved)}</dd></div>
          <div><dt>Recorded G4 pass</dt><dd>{verdict(capture.gate_g4_pass)}</dd></div>
          <div><dt>Cleanup verified zero Chaos</dt><dd>{verdict(capture.cleanup_verified_zero)}</dd></div>
          <div><dt>Recorded communications disposition</dt><dd>{capture.comms_status ?? "Unavailable"}</dd></div>
        </dl>
        <ol className="monitor-phases" aria-label="Recorded run phases">
          {capture.phases.map((phase) => <li key={phase.id}>
            <strong>{phase.label}</strong><span>{phase.observed ? "Observed in run log" : "Not recorded"}</span>
          </li>)}
        </ol>
        {capture.services.length > 0 && <p>Recorded triage: {capture.severity ?? "Unavailable"} / {capture.services.join(", ")}</p>}
        {capture.proposal && <p>Recorded proposal: <code>{capture.proposal.tool ?? "Unavailable"}</code> / {capture.proposal.target ?? "Unavailable"}. Proposal is not execution.</p>}
        {capture.actions.length > 0 && <div className="monitor-actions" role="region" aria-label="Recorded tool outcomes">
          {capture.actions.map((action, index) => <div key={index}>
            <code>{action.tool ?? "Unavailable"}</code>
            <span>{action.target ?? "Unavailable"} / {action.namespace ?? "Unavailable"}</span>
            <span>Tool success: {verdict(action.success)}</span>
          </div>)}
        </div>}
        {capture.sources.map((source) => <p className="demo-provenance" key={source.name}>
          {source.name} SHA-256: <code>{source.sha256}</code>
        </p>)}
        <p className="demo-provenance">{loading ? "Reading capture" : `Observed at: ${capture.observed_at}`}</p>
      </>}
  </section>;
}
