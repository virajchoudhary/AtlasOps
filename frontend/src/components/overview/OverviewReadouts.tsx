import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import type { Catalog, Gate } from "../../api/types";
import { displayBoolean, displayNumber, displayRatio, displayText, UNAVAILABLE } from "../../lib/format";
import { DataUnavailable } from "../SectionHeading";
import { Badge } from "../ui/badge";

type StatusTone = "neutral" | "mint" | "amber" | "rose";

const highlightedGateCodes = new Set(["G4", "G7", "G8", "G9", "G12", "G13", "G14", "G15"]);
const gateDestinations: Record<string, { to: string; label: string }> = {
  G4: { to: "/incidents", label: "incident chronology" },
  G7: { to: "/models", label: "model lineage" },
  G8: { to: "/evaluations", label: "evaluation results" },
  G9: { to: "/evaluations", label: "evaluation results" },
  G12: { to: "/evaluations", label: "evaluation results" },
  G13: { to: "/evaluations", label: "evaluation results" },
  G14: { to: "/system", label: "system boundaries" },
  G15: { to: "/system", label: "system boundaries" },
};

export function statusTone(value: string | null | undefined): StatusTone {
  const status = value?.trim().toUpperCase() ?? "";
  if (status.includes("NOT_PASSED") || status.includes("REOPENED")) return "rose";
  if (
    status.includes("NOT_CERTIFIED")
    || status.includes("PARTIAL")
    || status.includes("EMPIRICAL EVIDENCE MISSING")
  ) return "amber";
  if (status === "PASS" || status.startsWith("PASS ")) return "mint";
  return "neutral";
}

export function OverviewGateLedger({
  catalog,
  loading,
}: {
  catalog: Catalog | null;
  loading: boolean;
}) {
  const gates = catalog?.gates?.filter((gate) => highlightedGateCodes.has(gate.gate)) ?? [];

  return (
    <section className="overview-gate-ledger" aria-labelledby="overview-gates-title">
      <div className="overview-section-heading">
        <div>
          <p className="eyebrow">01 / Gate inventory</p>
          <h2 id="overview-gates-title">Critical gate state</h2>
        </div>
        <span className="overview-record-count">
          {catalog?.gates ? `${catalog.gates.length} tracked` : UNAVAILABLE}
        </span>
      </div>
      {gates.length > 0 ? (
        <ol className="overview-gate-list">
          {gates.map((gate) => (
            <OverviewGateRow key={gate.gate} gate={gate} />
          ))}
        </ol>
      ) : (
        <DataUnavailable
          loading={loading}
          message="Gate inventory unavailable. No substitute status is shown."
        />
      )}
    </section>
  );
}

function OverviewGateRow({ gate }: { gate: Gate }) {
  const destination = gateDestinations[gate.gate];
  const detail = gate.status_note || gate.deliverable;

  return (
    <li className="overview-gate-row">
      <span className="overview-gate-code">{gate.gate}</span>
      <div className="overview-gate-copy">
        <strong>{gate.name}</strong>
        <p>{displayText(detail)}</p>
      </div>
      <Badge tone={statusTone(gate.status)}>{displayText(gate.status)}</Badge>
      {destination && (
        <Link
          className="overview-open-link"
          to={destination.to}
          aria-label={`Open ${destination.label} for ${gate.gate}`}
          title={`Open ${destination.label}`}
        >
          <ArrowUpRight size={15} aria-hidden="true" />
        </Link>
      )}
    </li>
  );
}

export function OverviewEvidenceLedger({
  catalog,
  loading,
}: {
  catalog: Catalog | null;
  loading: boolean;
}) {
  const comparison = catalog?.current_results?.base_vs_sft;
  const aligned = catalog?.current_results?.g9_aligned_diagnostic;
  const pilot = catalog?.current_results?.g9_pilot;
  const pairedDelta = comparison?.paired_delta;
  const deltaTone = typeof pairedDelta === "number" && pairedDelta < 0
    ? "is-negative"
    : typeof pairedDelta === "number" && pairedDelta > 0 ? "is-positive" : "";
  const diagnosticNote = typeof pairedDelta !== "number"
    ? "No paired Validation diagnostic is available."
    : pairedDelta <= 0
      ? "No diagnostic improvement observed."
      : "A positive diagnostic delta is recorded.";

  return (
    <section className="overview-evidence-ledger" aria-labelledby="overview-evidence-title">
      <div className="overview-section-heading">
        <div>
          <p className="eyebrow">02 / Preserved results</p>
          <h2 id="overview-evidence-title">Recorded diagnostics</h2>
        </div>
        <Badge tone="amber">Diagnostic only</Badge>
      </div>
      {catalog ? (
        <>
          <dl className="overview-result-list">
            <OverviewResult label="Base F1" value={displayRatio(comparison?.base_f1)} />
            <OverviewResult label="SFT F1" value={displayRatio(comparison?.sft_f1)} />
            <OverviewResult
              label="Paired delta"
              value={displayRatio(pairedDelta)}
              valueClassName={deltaTone}
            />
            <OverviewResult
              label="Admissible actions"
              value={displayCountPair(aligned?.admissible_count, aligned?.sample_count)}
            />
            <OverviewResult label="Optimizer steps" value={displayNumber(aligned?.optimizer_steps)} />
            <OverviewResult
              label="Accepted SFT+GRPO checkpoint"
              value={displayBoolean(pilot?.acceptable_checkpoint, "Yes", "No")}
            />
          </dl>
          <p className="overview-diagnostic-note">{diagnosticNote}</p>
          <p className="overview-result-limit">
            Matched Validation diagnostics do not establish incident resolution or scientific certification.
          </p>
        </>
      ) : (
        <DataUnavailable
          loading={loading}
          message="Recorded result fields unavailable. No substitute metric is shown."
        />
      )}
      <Link className="overview-ledger-link" to="/evaluations">
        Open evaluation record <ArrowUpRight size={15} aria-hidden="true" />
      </Link>
    </section>
  );
}

function OverviewResult({
  label,
  value,
  valueClassName = "",
}: {
  label: string;
  value: string;
  valueClassName?: string;
}) {
  return (
    <div className="overview-result-row">
      <dt>{label}</dt>
      <dd className={valueClassName}>{value}</dd>
    </div>
  );
}

function displayCountPair(value: unknown, total: unknown) {
  const count = displayNumber(value);
  const denominator = displayNumber(total);
  return count === UNAVAILABLE || denominator === UNAVAILABLE
    ? UNAVAILABLE
    : `${count} / ${denominator}`;
}
