import { useMemo } from "react";
import {
  AlertOctagon,
  Check,
  CircleHelp,
  LockKeyhole,
  ShieldCheck,
} from "lucide-react";
import { MetricTile } from "../components/MetricTile";
import { PageHeading, DataUnavailable } from "../components/SectionHeading";
import { Badge } from "../components/ui/badge";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "../components/ui/accordion";
import type { CatalogPageProps } from "./types";
import { displayText } from "../lib/format";

const criticalBoundaries = new Set(["kubectl", "inference", "faults", "approval", "remediation", "cleanup", "share"]);

export function SystemPage({ catalog, loading }: CatalogPageProps) {
  const product = catalog?.product;
  const boundaries = product?.boundaries ?? [];
  const gates = catalog?.gates ?? [];
  const grouped = useMemo(() => ({
    environment: boundaries.filter((item) => ["localhost", "readonly"].includes(item.id)),
    prohibited: boundaries.filter((item) => criticalBoundaries.has(item.id)),
    other: boundaries.filter((item) => !["localhost", "readonly"].includes(item.id) && !criticalBoundaries.has(item.id)),
  }), [boundaries]);

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="System boundary"
        title="A local, read-only presentation surface."
        description="This interface reads the repository-backed catalog and historical attempt detail. It does not invoke operational tools."
      />

      <section className="system-status-grid" aria-label="Independent package and certification statuses">
        <MetricTile
          label="Scientific certification"
          value={displayText(product?.certification)}
          note="Research conclusion"
          icon={<AlertOctagon size={17} />}
          tone="amber"
        />
        <MetricTile
          label="Presentation package"
          value={displayText(product?.presentation_status)}
          note="Review package state"
          icon={<Check size={17} />}
          tone="mint"
        />
      </section>

      <section className="system-boundaries">
        <div className="system-section-heading">
          <div>
            <p className="eyebrow">Enforced demo boundaries</p>
            <h2>What this surface cannot do</h2>
          </div>
          <span className="readonly-contract"><LockKeyhole size={15} aria-hidden="true" /> GET only</span>
        </div>
        {boundaries.length ? (
          <>
            <BoundaryGroup label="Runtime" entries={grouped.environment} />
            <BoundaryGroup label="Prohibited operations" entries={grouped.prohibited} />
            {grouped.other.length > 0 && <BoundaryGroup label="Other" entries={grouped.other} />}
          </>
        ) : (
          <DataUnavailable loading={loading} message="System boundary catalog unavailable." />
        )}
      </section>

      <section className="system-read-contract">
        <div className="contract-icon" aria-hidden="true"><ShieldCheck size={19} /></div>
        <div>
          <p className="eyebrow">Data access</p>
          <h2>Read model, not runtime</h2>
          <p>Catalog and historical attempt detail are fetched as GET requests. Missing values remain “Unavailable”; this UI does not repair or infer evidence.</p>
        </div>
        <Badge tone="mint">No mutation controls</Badge>
      </section>

      <section className="gate-snapshot">
        <div className="system-section-heading">
          <div>
            <p className="eyebrow">Repository snapshot</p>
            <h2>Gate status</h2>
          </div>
          <span className="gate-snapshot-source">{displayText(catalog?.source)}</span>
        </div>
        {gates.length ? (
          <Accordion type="single" collapsible>
            <AccordionItem value="gates">
              <AccordionTrigger>G0-G15 detailed snapshot</AccordionTrigger>
              <AccordionContent>
                <div className="gate-list">
            {gates.map((gate) => (
              <article className="gate-row" key={gate.gate}>
                <span className="gate-row-id">{displayText(gate.gate)}</span>
                <div className="gate-row-name">
                  <strong>{displayText(gate.name)}</strong>
                  <span>{displayText(gate.deliverable)}</span>
                  {gate.status_note && <small>{gate.status_note}</small>}
                </div>
                <Badge tone={gate.status === "PASS" ? "mint" : gate.status.includes("NOT_PASSED") ? "amber" : "outline"}>
                  {displayText(gate.status)}
                </Badge>
              </article>
            ))}
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        ) : (
          <DataUnavailable loading={loading} message="Gate snapshot unavailable from the read API." />
        )}
      </section>
    </div>
  );
}

function BoundaryGroup({
  label,
  entries,
}: {
  label: string;
  entries: { id: string; label: string; state: string }[];
}) {
  if (!entries.length) return null;
  return (
    <div className="boundary-group">
      <div className="boundary-group-label"><span>{label}</span><i /></div>
      <div className="boundary-grid">
        {entries.map((entry) => (
          <article className={`boundary-item boundary-item--${entry.id}`} key={entry.id}>
            <span className="boundary-state-icon" aria-hidden="true">
              {entry.state === "ENFORCED" ? <Check size={16} /> : <CircleHelp size={16} />}
            </span>
            <div>
              <strong>{displayText(entry.label)}</strong>
              <span>{displayText(entry.state)}</span>
            </div>
            <Badge tone={entry.state === "ENFORCED" ? "mint" : "outline"}>{displayText(entry.state)}</Badge>
          </article>
        ))}
      </div>
    </div>
  );
}
