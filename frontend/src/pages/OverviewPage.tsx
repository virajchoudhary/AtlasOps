import { ArrowUpRight, CheckCircle2, GitBranch, ShieldCheck, UsersRound, Wrench } from "lucide-react";
import { motion } from "motion/react";
import { ArchitectureDiagram } from "../components/ArchitectureDiagram";
import { MetricTile } from "../components/MetricTile";
import { PageHeading } from "../components/SectionHeading";
import { Badge } from "../components/ui/badge";
import type { CatalogPageProps } from "./types";
import { displayNumber, displayText } from "../lib/format";

export function OverviewPage({ catalog }: CatalogPageProps) {
  const product = catalog?.product;

  return (
    <div className="page-stack overview-page">
      <section className="overview-hero">
        <PageHeading
          eyebrow="Governed incident response"
          title={displayText(product?.name ?? "AtlasOps")}
          description={displayText(product?.description)}
          actions={
            <div className="hero-status">
              <Badge tone="mint"><span className="badge-dot" /> Read-only research demo</Badge>
              <div className="hero-certification">
                <span>Scientific certification</span>
                <strong>{displayText(product?.certification)}</strong>
              </div>
            </div>
          }
        />
        <div className="hero-meta">
          <div className="hero-subtitle">
            <span className="hero-subtitle-mark" aria-hidden="true"><GitBranch size={16} /></span>
            <span>{displayText(product?.subtitle)}</span>
          </div>
          <span className="hero-source">{displayText(catalog?.source)}</span>
        </div>
      </section>

      <section className="overview-metrics" aria-label="System inventory">
        <motion.div className="metric-grid" initial="hidden" animate="show" variants={{
          hidden: {},
          show: { transition: { staggerChildren: 0.045 } },
        }}>
          <MetricTile
            label="Specialized agents"
            value={displayNumber(product?.agent_count)}
            note="Coordinated incident roles"
            icon={<UsersRound size={16} />}
            tone="mint"
          />
          <MetricTile
            label="Registered tool wrappers"
            value={displayNumber(product?.tool_count)}
            note={`Role-exposed: ${displayNumber(product?.agent_exposed_tool_count)}`}
            icon={<Wrench size={16} />}
            tone="graphite"
          />
          <MetricTile
            label="Frozen scenarios"
            value={displayNumber(product?.scenario_count)}
            note="Catalog inventory"
            icon={<CheckCircle2 size={16} />}
            tone="amber"
          />
        </motion.div>
      </section>

      <ArchitectureDiagram />

      <section className="capability-strip" aria-label="Project capabilities">
        <div className="capability-intro">
          <p className="eyebrow">Built around the boundary</p>
          <h2>Reasoning is not authority.</h2>
        </div>
        <div className="capability-items">
          <Capability icon={<UsersRound size={18} />} label="Multi-agent reasoning" />
          <Capability icon={<ShieldCheck size={18} />} label="Explicit approval gate" />
          <Capability icon={<CheckCircle2 size={18} />} label="Objective verification" />
          <Capability icon={<ArrowUpRight size={18} />} label="Traceable evidence" />
        </div>
      </section>

      <div className="overview-footer-note">
        <span className="footer-note-icon"><ShieldCheck size={15} aria-hidden="true" /></span>
        <span>Presentation package: <strong>{displayText(product?.presentation_status)}</strong></span>
        <span className="footer-note-divider" aria-hidden="true">·</span>
        <span>Research status is distinct from scientific certification.</span>
      </div>
    </div>
  );
}

function Capability({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="capability-item">
      <span className="capability-icon" aria-hidden="true">{icon}</span>
      <strong>{label}</strong>
    </div>
  );
}
