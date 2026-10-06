import {
  BadgeCheck,
  CircleHelp,
  ClipboardCheck,
  FileSearch,
  LockKeyhole,
  ShieldCheck,
} from "lucide-react";
import type { ProductControl } from "../api/types";
import { PageHeading, DataUnavailable } from "../components/SectionHeading";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "../components/ui/accordion";
import { Badge } from "../components/ui/badge";
import type { CatalogPageProps } from "./types";
import { displayNumber, displayText } from "../lib/format";

const controlIcons: Record<string, typeof ShieldCheck> = {
  acl: LockKeyhole,
  approval: ShieldCheck,
  verifier: BadgeCheck,
  audit: FileSearch,
};

export function AgentsPage({ catalog, loading }: CatalogPageProps) {
  const agents = catalog?.product?.agents ?? [];
  const controls = catalog?.product?.controls ?? [];

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="Agent system"
        title="Four roles. Clear authority boundaries."
        description="Agents produce scoped outputs; policy and the environment retain the authority to approve and verify."
      />
      {agents.length ? (
        <section className="agent-grid" aria-label="Specialized agents">
          {agents.map((agent, index) => (
            <article className={`agent-card agent-card--${agent.id}`} key={agent.id}>
              <div className="agent-card-top">
                <span className="agent-index">{String(index + 1).padStart(2, "0")}</span>
                <Badge tone="mint">Agent</Badge>
              </div>
              <h2>{displayText(agent.name)}</h2>
              <p className="agent-role">{displayText(agent.role)}</p>
              <p className="agent-purpose">{displayText(agent.purpose)}</p>

              <dl className="agent-io">
                <div><dt>Input</dt><dd>{displayText(agent.input)}</dd></div>
                <div><dt>Output</dt><dd>{displayText(agent.output)}</dd></div>
              </dl>

              <div className="agent-tools">
                <div className="agent-tools-heading">
                  <span>Tool categories</span>
                  <strong>{displayNumber(agent.tool_count)}</strong>
                </div>
                <div className="tag-list">
                  {agent.tool_categories?.length
                    ? agent.tool_categories.map((category) => <Badge key={category} tone="outline">{category}</Badge>)
                    : <span className="muted-copy">{displayText(null)}</span>}
                </div>
              </div>

              <div className="agent-boundary">
                <span className="boundary-shield" aria-hidden="true"><ShieldCheck size={15} /></span>
                <div><span>Governance boundary</span><p>{displayText(agent.governance_boundary)}</p></div>
              </div>

              <Accordion type="single" collapsible className="agent-accordion">
                <AccordionItem value="tools">
                  <AccordionTrigger>Allowed tools <span className="inline-count">{agent.allowed_tools?.length ?? 0}</span></AccordionTrigger>
                  <AccordionContent>
                    {agent.allowed_tools?.length ? (
                      <ul className="tool-name-list">
                        {agent.allowed_tools.map((tool) => <li key={tool}><code>{tool}</code></li>)}
                      </ul>
                    ) : (
                      <p className="muted-copy">{displayText(null)}</p>
                    )}
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </article>
          ))}
        </section>
      ) : (
        <DataUnavailable loading={loading} message="Agent catalog unavailable from the read API." />
      )}

      <section className="controls-section">
        <div className="controls-heading">
          <div>
            <p className="eyebrow">Not agents</p>
            <h2>Control and verification layer</h2>
          </div>
          <p>These controls constrain the workflow and establish what counts as an outcome.</p>
        </div>
        {controls.length ? (
          <div className="control-grid">
            {controls.map((control) => <ControlCard key={control.id} control={control} />)}
          </div>
        ) : (
          <DataUnavailable loading={loading} message="Control catalog unavailable from the read API." />
        )}
      </section>
    </div>
  );
}

function ControlCard({ control }: { control: ProductControl }) {
  const Icon = controlIcons[control.id] ?? CircleHelp;
  const tone = control.id === "approval" ? "amber"
    : control.id === "verifier" ? "mint"
      : control.id === "audit" ? "graphite" : "neutral";
  return (
    <article className={`control-card control-card--${tone}`}>
      <span className="control-icon" aria-hidden="true"><Icon size={18} /></span>
      <div>
        <p className="eyebrow">Control</p>
        <h3>{displayText(control.name)}</h3>
        <p>{displayText(control.purpose)}</p>
      </div>
    </article>
  );
}
