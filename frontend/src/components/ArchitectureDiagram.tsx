import {
  Activity,
  ArrowRight,
  Bell,
  CheckCircle2,
  MessageSquareText,
  Search,
  ShieldCheck,
  Wrench,
} from "lucide-react";

const stages = {
  alert: { name: "Incident alert", kind: "signal", caption: "Signal", icon: Bell },
  triage: { name: "Triage", kind: "agent", caption: "Agent", icon: Activity },
  diagnosis: { name: "Diagnosis", kind: "agent", caption: "Agent", icon: Search },
  approval: { name: "Safety / approval", kind: "control", caption: "Governance", icon: ShieldCheck },
  remediation: { name: "Remediation", kind: "agent", caption: "Agent", icon: Wrench },
  verifier: { name: "Objective verifier", kind: "verifier", caption: "Environment truth", icon: CheckCircle2 },
  comms: { name: "Comms", kind: "agent", caption: "Agent", icon: MessageSquareText },
};

export function ArchitectureDiagram() {
  return (
    <section className="architecture-panel" aria-labelledby="architecture-title">
      <div className="architecture-heading">
        <div>
          <p className="eyebrow">System map</p>
          <h2 id="architecture-title">Incident response, under governance</h2>
        </div>
        <p className="architecture-caption">Conceptual system flow</p>
      </div>
      <ol className="architecture-flow">
        <li className="architecture-stage architecture-stage--signal">
          <Stage stage={stages.alert} />
        </li>
        <li className="architecture-stage architecture-stage--cluster">
          <div className="architecture-agent-cluster" role="group" aria-label="Triage and diagnosis agents">
            <span className="architecture-agent-label">Agent reasoning layer</span>
            <div className="architecture-agent-track">
              <Stage stage={stages.triage} />
              <ArrowRight className="architecture-inner-arrow" size={14} aria-hidden="true" />
              <Stage stage={stages.diagnosis} />
            </div>
          </div>
        </li>
        <li className="architecture-stage architecture-stage--control">
          <Stage stage={stages.approval} />
        </li>
        <li className="architecture-stage architecture-stage--agent">
          <Stage stage={stages.remediation} />
        </li>
        <li className="architecture-stage architecture-stage--verifier">
          <Stage stage={stages.verifier} />
        </li>
        <li className="architecture-stage architecture-stage--agent architecture-stage--last">
          <Stage stage={stages.comms} />
        </li>
      </ol>
      <div className="architecture-legend" aria-label="Architecture roles">
        <span><i className="legend-dot legend-dot--agent" /> Agents: triage, diagnosis, remediation, comms</span>
        <span><i className="legend-dot legend-dot--control" /> Approval: policy boundary</span>
        <span><i className="legend-dot legend-dot--verifier" /> Verifier: environment decides</span>
      </div>
    </section>
  );
}

function Stage({ stage }: { stage: (typeof stages)[keyof typeof stages] }) {
  const Icon = stage.icon;
  return (
    <div className={`architecture-node architecture-node--${stage.kind}`}>
      <div className="architecture-node-top">
        <span className="architecture-icon"><Icon size={17} aria-hidden="true" /></span>
        <span className="architecture-kind">{stage.caption}</span>
      </div>
      <strong>{stage.name}</strong>
    </div>
  );
}
