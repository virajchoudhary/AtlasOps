import { useEffect, useState } from "react";
import { Check, Play, RefreshCw, ShieldCheck, X } from "lucide-react";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";

interface Proposal {
  token: string;
  action_digest: string;
  action: { tool: string; arguments: Record<string, unknown> };
  incident_id: string;
  severity: string;
  operator_scope: { kube_context: string; scenario_id: string };
}
interface OperatorStatus {
  enabled: boolean;
  csrf_token: string;
  experiment_id: string;
  source_sha: string;
  protocol_fingerprint: string;
  kube_context: string;
  scenario_id: string;
  readiness: {
    can_start: boolean; blockers: string[]; attempts_used: number | null;
    attempt_limit: number; runtime_qualified: boolean;
  };
  pending: Proposal[];
  channel_error: string | null;
  events?: { role: string; phase: string; tool: string | null }[] | null;
  capture: {
    status: string; process_running: boolean | null; env_resolved: boolean | null;
    cleanup_verified_zero: boolean | null; gate_g4_pass: boolean | null;
    phases: { id: string; label: string; observed: boolean }[];
    policy_block: string | null;
    comms_status: string | null;
  } | null;
}
const fact = (value: boolean | null) => value === true ? "Yes" : value === false ? "No" : "Unavailable";

export function OperatorPanel({ onMode }: { onMode: (enabled: boolean) => void }) {
  const [status, setStatus] = useState<OperatorStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    fetch("/api/operator", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Operator status unavailable");
        const value = await response.json() as OperatorStatus;
        if (!controller.signal.aborted) {
          setStatus(value); setError(""); onMode(value.enabled);
          if (value.enabled) timer = window.setTimeout(() => setRefresh((n) => n + 1), 3000);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setStatus(null); setError("Operator status unavailable. No action can be submitted.");
          timer = window.setTimeout(() => setRefresh((n) => n + 1), 3000);
        }
      });
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [refresh, onMode]);

  async function command(path: string, body: object) {
    if (!status) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(`/api/operator/${path}`, {
        method: "POST", headers: {
          "Content-Type": "application/json", "X-AtlasOps-Operator": status.csrf_token,
        }, body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error((await response.json()).detail ?? "Operation blocked");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Operation unavailable");
    } finally {
      setBusy(false); setStatus(null); setRefresh((n) => n + 1);
    }
  }
  if (!status?.enabled && !error) return null;
  return <section className="operator-panel" aria-label="Live incident controls">
    <div className="demo-toolbar">
      <h2><ShieldCheck size={18} aria-hidden="true" /> Live incident controls</h2>
      <Button size="icon" variant="quiet" aria-label="Refresh operator status" title="Refresh operator status"
        disabled={busy} onClick={() => { setError(""); setRefresh((n) => n + 1); }}><RefreshCw size={16} /></Button>
    </div>
    {error && <p role="alert" className="monitor-failure">{error}</p>}
    {status?.enabled && <>
      <div className="demo-actions">
        <Badge tone="amber">GOVERNED / NOT A SIMULATION</Badge>
        <code>{status.experiment_id}</code>
        <Badge tone="outline">{status.capture?.status ?? "NOT STARTED"}</Badge>
      </div>
      <dl className="monitor-facts">
        <div><dt>Cluster context</dt><dd>{status.kube_context}</dd></div>
        <div><dt>Scenario</dt><dd>{status.scenario_id}</dd></div>
        <div><dt>Protocol slots used</dt><dd>{status.readiness.attempts_used ?? "Unavailable"} / {status.readiness.attempt_limit}</dd></div>
      </dl>
      <p className="demo-provenance">Source SHA: <code>{status.source_sha}</code></p>
      <p className="demo-provenance">Protocol SHA-256: <code>{status.protocol_fingerprint}</code></p>
      {status.readiness.blockers.length > 0 && <ul className="operator-blockers" aria-label="Launch blockers">
        {status.readiness.blockers.map((blocker) => <li key={blocker}>{blocker.replaceAll("_", " ")}</li>)}
      </ul>}
      {status.capture?.process_running && status.channel_error && <p role="alert" className="monitor-failure">
        Approval channel unavailable. Pending actions cannot be read or decided.
      </p>}
      {status.capture?.process_running && status.events === null && <p role="status">
        Agent activity unavailable.
      </p>}
      {!status.capture && <Button variant="primary" disabled={busy || !status.readiness.can_start}
        onClick={() => command("start", {})}><Play size={16} /> Start governed SF002 run</Button>}
      {status.pending.map((proposal) => <div key={proposal.token} className="demo-approval"
        role="region" aria-label="Exact live action approval">
        <div>
          <Badge tone="amber">{proposal.severity} APPROVAL PENDING</Badge>
          <p><code>{proposal.incident_id}</code></p>
          <pre>{JSON.stringify(proposal.action, null, 2)}</pre>
          <p className="demo-provenance">Action SHA-256: <code>{proposal.action_digest}</code></p>
        </div>
        <div className="demo-actions">
          <Button variant="primary" disabled={busy} onClick={() => command("decision", {
            token: proposal.token, action_digest: proposal.action_digest, decision: "approved",
          })}><Check size={16} /> Approve exact action</Button>
          <Button variant="outline" disabled={busy} onClick={() => command("decision", {
            token: proposal.token, action_digest: proposal.action_digest, decision: "rejected",
          })}><X size={16} /> Reject exact action</Button>
        </div>
      </div>)}
      {!!status.events?.length && <div className="operator-activity" role="region" aria-label="Live agent activity">
        <h3>Agent activity</h3>
        <ol>{status.events.map((event, index) => <li key={index}>
          <strong>{event.role}</strong><span>{event.phase.replaceAll("_", " ")}</span>
          {event.tool && <code>{event.tool}</code>}
        </li>)}</ol>
      </div>}
      {status.capture && <>
        {status.capture.policy_block && <p className="monitor-failure">Policy block: {status.capture.policy_block}</p>}
        <dl className="monitor-facts">
          <div><dt>Runner active</dt><dd>{fact(status.capture.process_running)}</dd></div>
          <div><dt>Environment resolved</dt><dd>{fact(status.capture.env_resolved)}</dd></div>
          <div><dt>G4 pass</dt><dd>{fact(status.capture.gate_g4_pass)}</dd></div>
          <div><dt>Cleanup verified zero Chaos</dt><dd>{fact(status.capture.cleanup_verified_zero)}</dd></div>
          <div><dt>Recorded communications disposition</dt><dd>{status.capture.comms_status ?? "Unavailable"}</dd></div>
        </dl>
        <ol className="monitor-phases">{status.capture.phases.map((phase) => <li key={phase.id}>
          <strong>{phase.label}</strong><span>{phase.observed ? "Recorded" : "Not recorded"}</span>
        </li>)}</ol>
      </>}
    </>}
  </section>;
}
