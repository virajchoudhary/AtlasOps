import type { ReactNode } from "react";
import { ArrowDown, Cpu, GitBranch, ShieldCheck, Sparkles } from "lucide-react";
import { PageHeading, DataUnavailable } from "../components/SectionHeading";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "../components/ui/accordion";
import { Badge } from "../components/ui/badge";
import type { CatalogPageProps } from "./types";
import { displayBoolean, displayNumber, displayText, shortDigest } from "../lib/format";

export function ModelsPage({ catalog, loading }: CatalogPageProps) {
  const results = catalog?.current_results;
  const sft = results?.sft_v17;
  const pilot = results?.g9_pilot;
  const checkpointOutcome = pilot?.available
    ? pilot.acceptable_checkpoint === false
      ? "No accepted GRPO checkpoint"
      : pilot.acceptable_checkpoint === true
        ? "Accepted checkpoint recorded"
        : "Unavailable"
    : "Unavailable";

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="Model lineage"
        title="Model lineage"
        description="Artifact provenance, validation, and the GRPO finding are shown as separate stages."
      />
      <section className="lineage" aria-label="Model lineage">
        <LineageNode
          step="01"
          icon={<Cpu size={18} />}
          eyebrow="Base model"
          title={displayText(sft?.model)}
          status={sft?.available ? "Source recorded" : "Unavailable"}
          tone="graphite"
        >
          <Detail label="Pinned revision" value={displayText(sft?.model_revision)} />
        </LineageNode>
        <LineageConnector />
        <LineageNode
          step="02"
          icon={<Sparkles size={18} />}
          eyebrow="Supervised fine-tuning method"
          title={displayText(catalog?.product?.model_method)}
          status={displayText(sft?.available ? sft.status : null)}
          tone={sft?.available ? "mint" : "neutral"}
        >
          <p className="lineage-context">
            Method identity is separate from the preserved adapter artifact and its reload evidence.
          </p>
        </LineageNode>
        <LineageConnector />
        <LineageNode
          step="03"
          icon={<ShieldCheck size={18} />}
          eyebrow="Adapter artifact"
          title="SFT v17 adapter"
          status={sft?.available ? "REAL ARTIFACT" : "Unavailable"}
          tone={sft?.available ? "mint" : "neutral"}
        >
          {sft?.available ? (
            <>
              <div className="lineage-stats">
                <Detail label="Corpus rows" value={displayNumber(sft.corpus_rows)} />
                <Detail label="Optimizer steps" value={displayNumber(sft.training_steps)} />
                <Detail label="Split" value={displayText(sft.corpus_split)} />
                <Detail label="Synthetic corpus" value={displayBoolean(sft.synthetic_corpus)} />
              </div>
              <div className="lineage-verification">
                <ShieldCheck size={16} aria-hidden="true" />
                <span>Independent fresh-process reload</span>
                <strong>{displayText(sft.reload_status)}</strong>
                <small>{displayNumber(sft.reload_tensor_count)} tensors checked</small>
              </div>
              <Accordion type="single" collapsible className="lineage-accordion">
                <AccordionItem value="adapter-hash">
                  <AccordionTrigger>Adapter provenance</AccordionTrigger>
                  <AccordionContent>
                    <dl className="evidence-detail-grid">
                      <div><dt>Adapter SHA-256</dt><dd><code>{sft.adapter_sha256 ?? "Unavailable"}</code></dd></div>
                      <div><dt>Run ID</dt><dd><code>{sft.run_id ?? "Unavailable"}</code></dd></div>
                    </dl>
                    <p className="lineage-digest-prefix">Digest prefix: {shortDigest(sft.adapter_sha256)}</p>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </>
          ) : (
            <DataUnavailable loading={loading} message="SFT adapter provenance is unavailable." />
          )}
        </LineageNode>
        <LineageConnector />
        <LineageNode
          step="04"
          icon={<GitBranch size={18} />}
          eyebrow="Controlled GRPO research"
          title="Controlled GRPO"
          status={pilot?.available ? displayText(pilot.status) : "Unavailable"}
          tone={pilot?.available ? "amber" : "neutral"}
        >
          {pilot?.available ? (
            <div className="lineage-stats">
              <Detail label="Policy completions" value={displayNumber(pilot.completions)} />
              <Detail label="Malformed / blocked" value={displayNumber(pilot.malformed_or_blocked)} />
              <Detail label="Reward per blocked action" value={displayNumber(pilot.reward_each)} />
              <Detail label="Reward-driven advantage groups" value={displayNumber(pilot.reward_driven_advantage_groups)} />
              <Detail label="Groups with zero reward-driven advantages" value={displayNumber(pilot.zero_advantage_groups)} />
            </div>
          ) : (
            <DataUnavailable loading={loading} message="Controlled GRPO result unavailable." />
          )}
        </LineageNode>
        <LineageConnector />
        <LineageNode
          step="05"
          icon={<ShieldCheck size={18} />}
          eyebrow="Checkpoint outcome"
          title={checkpointOutcome}
          status={pilot?.available ? "Recorded outcome" : "Unavailable"}
          tone={pilot?.available && pilot.acceptable_checkpoint === false ? "amber" : "neutral"}
        >
          {pilot?.available ? (
            <Detail
              label="Acceptable checkpoint"
              value={displayBoolean(pilot.acceptable_checkpoint, "Recorded", "None recorded")}
            />
          ) : (
            <DataUnavailable loading={loading} message="Checkpoint outcome unavailable." />
          )}
        </LineageNode>
      </section>
      {!sft && <DataUnavailable loading={loading} message="Model lineage data unavailable from the read API." />}
    </div>
  );
}

function LineageConnector() {
  return <div className="lineage-connector" aria-hidden="true"><ArrowDown size={18} /></div>;
}

function LineageNode({
  step,
  icon,
  eyebrow,
  title,
  status,
  tone,
  children,
}: {
  step: string;
  icon: ReactNode;
  eyebrow: string;
  title: string;
  status: string;
  tone: "mint" | "amber" | "graphite" | "neutral";
  children: ReactNode;
}) {
  return (
    <article className={`lineage-node lineage-node--${tone}`}>
      <div className="lineage-node-header">
        <span className="lineage-step">{step}</span>
        <span className="lineage-icon" aria-hidden="true">{icon}</span>
        <Badge tone={tone === "graphite" ? "outline" : tone}>{status}</Badge>
      </div>
      <p className="eyebrow">{eyebrow}</p>
      <h2>{title}</h2>
      <div className="lineage-node-content">{children}</div>
    </article>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="lineage-detail">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
