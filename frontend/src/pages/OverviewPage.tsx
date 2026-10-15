import { ArrowUpRight, Play } from "lucide-react";
import { Link } from "react-router-dom";
import type { Catalog } from "../api/types";
import { ArchitectureDiagram } from "../components/ArchitectureDiagram";
import { DataUnavailable } from "../components/SectionHeading";
import { OverviewEvidenceLedger, OverviewGateLedger, statusTone } from "../components/overview/OverviewReadouts";
import { Badge } from "../components/ui/badge";
import type { CatalogPageProps } from "./types";
import { displayNumber, displayText, shortDigest, UNAVAILABLE } from "../lib/format";

export function OverviewPage({ catalog, loading }: CatalogPageProps) {
  const product = catalog?.product;
  const g4 = catalog?.gates?.find((gate) => gate.gate === "G4");
  const operatorState = product
    ? product.operator_enabled ? "GOVERNED OPERATOR" : "READ-ONLY"
    : UNAVAILABLE;

  return (
    <div className="page-stack overview-page">
      <section className="overview-intro" aria-labelledby="overview-title">
        <div className="overview-intro-main">
          <p className="eyebrow">Repository view / governed incident response</p>
          <h1 id="overview-title">{displayText(product?.name ?? "AtlasOps")}</h1>
          <p className="overview-intro-description">{displayText(product?.description)}</p>
          <p className="overview-source">{displayText(catalog?.source)}</p>
        </div>
        <div className="overview-intro-side">
          <div className="overview-source-stamp">
            <span>Source revision</span>
            <code>{shortDigest(catalog?.source_sha)}</code>
          </div>
          <Link to="/demo" className="button button--primary button--md">
            <Play size={15} aria-hidden="true" />
            Open workflow rehearsal
            <ArrowUpRight size={14} aria-hidden="true" />
          </Link>
          <Badge tone="neutral">Read-only research demo</Badge>
        </div>
      </section>

      {!catalog && (
        <DataUnavailable
          loading={loading}
          message="Repository snapshot unavailable. No status values are substituted."
        />
      )}

      <section className="overview-posture" aria-label="Current program posture">
        <div className="overview-posture-heading">
          <div>
            <p className="eyebrow">00 / Current posture</p>
            <h2>Package readiness and science are separate.</h2>
          </div>
          <span>Repository evidence, not live health</span>
        </div>
        <div className="overview-posture-grid">
          <PostureRow
            label="Presentation package"
            value={product?.presentation_status}
            note="Review packaging only."
          />
          <PostureRow
            label="Scientific certification"
            value={product?.certification}
            note="Empirical completion is not established."
          />
          <PostureRow
            label="G4 incident recovery"
            value={g4?.status}
            note={g4?.status_note || "Current G4 disposition."}
          />
          <PostureRow
            label="Operational access"
            value={operatorState}
            note={product?.operator_enabled
              ? "Governed controls are confined to the rehearsal route."
              : "Mutation controls are unavailable in this view."}
          />
        </div>
      </section>

      <ArchitectureDiagram />

      <div className="overview-readout-grid">
        <OverviewGateLedger catalog={catalog} loading={loading} />
        <OverviewEvidenceLedger catalog={catalog} loading={loading} />
      </div>

      <InventoryReadout catalog={catalog} />

      <footer className="overview-footer">
        <span>Source</span>
        <strong>{displayText(catalog?.source)}</strong>
        <span aria-hidden="true">/</span>
        <span>Certification</span>
        <strong>{displayText(product?.certification)}</strong>
      </footer>
    </div>
  );
}

function PostureRow({
  label,
  value,
  note,
}: {
  label: string;
  value: string | null | undefined;
  note: string;
}) {
  const visibleValue = displayText(value);
  return (
    <div className="overview-posture-row">
      <span className="overview-posture-label">{label}</span>
      <Badge tone={statusTone(visibleValue)}>{visibleValue}</Badge>
      <span className="overview-posture-note">{note}</span>
    </div>
  );
}

function InventoryReadout({ catalog }: { catalog: Catalog | null }) {
  const product = catalog?.product;
  const inventory = [
    ["Specialized agents", displayNumber(product?.agent_count)],
    ["Registered tool wrappers", displayNumber(product?.tool_count)],
    ["Role-exposed tools", displayNumber(product?.agent_exposed_tool_count)],
    ["Unexposed tools", displayNumber(product?.unexposed_tool_count)],
    ["Frozen scenarios", displayNumber(product?.scenario_count)],
  ] as const;

  return (
    <section className="overview-inventory" aria-labelledby="overview-inventory-title">
      <div className="overview-section-heading">
        <div>
          <p className="eyebrow">03 / Catalog inventory</p>
          <h2 id="overview-inventory-title">Registered components</h2>
        </div>
      </div>
      <dl className="overview-inventory-list">
        {inventory.map(([label, value]) => (
          <div className="overview-inventory-row" key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
