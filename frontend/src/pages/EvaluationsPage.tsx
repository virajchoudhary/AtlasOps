import { useReducedMotion, motion } from "motion/react";
import { Activity, GitCompare, ShieldAlert } from "lucide-react";
import { PageHeading, DataUnavailable } from "../components/SectionHeading";
import { Badge } from "../components/ui/badge";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "../components/ui/accordion";
import type { CatalogPageProps } from "./types";
import {
  comparisonWidth,
  displayBoolean,
  displayNumber,
  displayRatio,
  displayText,
} from "../lib/format";

export function EvaluationsPage({ catalog, loading }: CatalogPageProps) {
  const comparison = catalog?.current_results?.base_vs_sft;
  const pilot = catalog?.current_results?.g9_pilot;
  const aligned = catalog?.current_results?.g9_aligned_diagnostic;
  const delta = comparison?.paired_delta;
  const diagnosticSummary = typeof delta !== "number"
    ? "Unavailable"
    : delta < 0 ? "No diagnostic improvement observed"
      : delta === 0 ? "No paired diagnostic change observed"
        : "Positive paired difference; diagnostic only";

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="Evaluation results"
        title="Evaluations"
        description="Matched Validation diagnostics and controlled RL findings. No incident-resolution claim is inferred from these results."
      />

      <section className="result-section result-section--validation">
        <div className="result-section-heading">
          <div>
            <p className="eyebrow">Matched Validation-only diagnostic</p>
            <h2>Base vs SFT</h2>
          </div>
          <Badge tone={comparison?.available && typeof delta === "number" && delta < 0 ? "amber" : "outline"}>
            {diagnosticSummary}
          </Badge>
        </div>
        {comparison?.available ? (
          <>
            <div className="validation-metrics" aria-label="Matched diagnostic values">
              <ValidationMetric label="Base F1" value={displayRatio(comparison.base_f1)} />
              <ValidationMetric label="SFT F1" value={displayRatio(comparison.sft_f1)} />
              <ValidationMetric
                label="Delta"
                value={displayRatio(comparison.paired_delta)}
                tone={typeof comparison.paired_delta === "number" && comparison.paired_delta < 0 ? "amber" : "neutral"}
              />
            </div>
            <div className="comparison-layout">
              <div className="comparison-chart" role="img" aria-label="Base and SFT diagnostic F1 compared on a zero to one scale">
                <div className="chart-scale" aria-hidden="true">
                  <span>0.00</span><span>0.25</span><span>0.50</span><span>0.75</span><span>1.00</span>
                </div>
                <ComparisonBar label="Base" value={comparison.base_f1} tone="graphite" />
                <ComparisonBar label="SFT" value={comparison.sft_f1} tone="mint" />
                <p className="chart-axis-note">Diagnostic F1 · scale fixed from 0 to 1</p>
              </div>
              <div className="comparison-result">
                <div className="schema-compare">
                  <span>Schema conformance</span>
                  <div><small>Base</small><strong>{ratioCount(comparison.base_schema_valid, comparison.base_schema_total)}</strong></div>
                  <div><small>SFT</small><strong>{ratioCount(comparison.sft_schema_valid, comparison.sft_schema_total)}</strong></div>
                </div>
              </div>
            </div>
            <Accordion type="single" collapsible className="validation-disclosure">
              <AccordionItem value="scope">
                <AccordionTrigger><Activity size={15} aria-hidden="true" /> Interpretation and scope</AccordionTrigger>
                <AccordionContent>
                  <div className="validation-caveat">
                    <p>{displayText(comparison.scope)}. This diagnostic does not establish incident resolution.</p>
                  </div>
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          </>
        ) : (
          <DataUnavailable loading={loading} message="Matched Base-vs-SFT diagnostic unavailable." />
        )}
      </section>

      <div className="evaluation-grid">
        <section className="result-section result-section--rl">
          <div className="result-section-heading">
            <div>
              <p className="eyebrow">Controlled RL finding</p>
              <h2>Reward / policy outcome</h2>
            </div>
            <ShieldAlert size={19} className="result-heading-icon result-heading-icon--amber" aria-hidden="true" />
          </div>
          {pilot?.available ? (
            <>
              <div className="rl-outcome-number">
                <strong>{displayNumber(pilot.completions)}</strong>
                <span>policy completions</span>
              </div>
              <div className="rl-facts">
                <Fact label="Malformed or blocked" value={displayNumber(pilot.malformed_or_blocked)} />
                <Fact label="Reward per blocked action" value={displayNumber(pilot.reward_each)} />
                <Fact label="Reward-driven advantage groups" value={displayNumber(pilot.reward_driven_advantage_groups)} />
                <Fact label="Groups with zero reward-driven advantages" value={displayNumber(pilot.zero_advantage_groups)} />
                <Fact label="Accepted checkpoint" value={displayBoolean(pilot.acceptable_checkpoint, "Yes", "No")} />
              </div>
              {pilot.acceptable_checkpoint === false && (
                <p className="result-caption">No acceptable SFT+GRPO checkpoint was saved.</p>
              )}
            </>
          ) : (
            <DataUnavailable loading={loading} message="Controlled RL finding unavailable." />
          )}
        </section>

        <section className="result-section result-section--aligned">
          <div className="result-section-heading">
            <div>
              <p className="eyebrow">Final aligned diagnostic</p>
              <h2>Admissibility and preservation</h2>
            </div>
            <GitCompare size={19} className="result-heading-icon" aria-hidden="true" />
          </div>
          {aligned?.available ? (
            <>
              <div className="admissibility">
                <strong>{displayNumber(aligned.admissible_count)}<span>/</span>{displayNumber(aligned.sample_count)}</strong>
                <span>admissible samples</span>
              </div>
              <div className="rl-facts">
                <Fact label="Optimizer steps" value={displayNumber(aligned.optimizer_steps)} />
                <Fact label="LoRA tensors checked" value={displayNumber(aligned.tensor_hash_count)} />
                <Fact label="Hashes unchanged" value={displayBoolean(aligned.tensor_hashes_unchanged)} />
                <Fact label="Model mutated" value={displayBoolean(aligned.model_mutated)} />
              </div>
              <Accordion type="single" collapsible className="validation-disclosure">
                <AccordionItem value="aligned-limits">
                  <AccordionTrigger>Interpretation limits</AccordionTrigger>
                  <AccordionContent>
                    <p className="validation-limit-copy">
                      This bounded diagnostic does not estimate population probability and does not establish interface mismatch as the only cause.
                    </p>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </>
          ) : (
            <DataUnavailable loading={loading} message="Final aligned diagnostic unavailable." />
          )}
        </section>
      </div>
    </div>
  );
}

function ValidationMetric({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "amber";
}) {
  return (
    <div className={`validation-metric validation-metric--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ComparisonBar({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | null | undefined;
  tone: "mint" | "graphite";
}) {
  const width = comparisonWidth(value);
  const reducedMotion = useReducedMotion();
  return (
    <div className="comparison-row">
      <span className="comparison-label">{label}</span>
      <div className="comparison-track">
        {width !== null && (
          <motion.div
            className={`comparison-fill comparison-fill--${tone}`}
            initial={reducedMotion ? { width: `${width}%` } : { width: 0 }}
            whileInView={{ width: `${width}%` }}
            viewport={{ once: true }}
            transition={{ duration: 0.55, ease: "easeOut" }}
          />
        )}
      </div>
      <strong className="comparison-value">{displayRatio(value)}</strong>
    </div>
  );
}

function ratioCount(valid: number | null | undefined, total: number | null | undefined): string {
  if (typeof valid !== "number" || typeof total !== "number") return "Unavailable";
  return `${displayNumber(valid)} / ${displayNumber(total)}`;
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div className="rl-fact"><span>{label}</span><strong>{value}</strong></div>;
}
