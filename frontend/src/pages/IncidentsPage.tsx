import { useState } from "react";
import { AlertTriangle, ArrowDown, Clock3, FileSearch, ShieldCheck } from "lucide-react";
import { fetchAttempt } from "../api/client";
import type { AttemptDetail, AttemptSummary, FinalAttempt } from "../api/types";
import { PageHeading, DataUnavailable } from "../components/SectionHeading";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "../components/ui/accordion";
import { Badge } from "../components/ui/badge";
import { Input } from "../components/ui/input";
import type { CatalogPageProps } from "./types";
import { displayBoolean, displayNumber, displayNumberWithUnit, displayText, shortDigest } from "../lib/format";

type AttemptLoad =
  | { status: "loading" }
  | { status: "ready"; detail: AttemptDetail }
  | { status: "unavailable" };

export function IncidentsPage({ catalog, loading }: CatalogPageProps) {
  const finalAttempts = catalog?.current_results?.g4_attempts ?? [];
  const historicalAttempts = catalog?.attempts ?? [];
  const g4 = catalog?.gates?.find((gate) => gate.gate === "G4");
  const [search, setSearch] = useState("");
  const [selectedAttempt, setSelectedAttempt] = useState("");
  const [attemptDetails, setAttemptDetails] = useState<Record<string, AttemptLoad>>({});
  const filteredAttempts = historicalAttempts.filter((attempt) =>
    `${attempt.id} ${attempt.name} ${attempt.scenario} ${attempt.state}`.toLowerCase().includes(search.trim().toLowerCase())
  );

  function selectAttempt(name: string) {
    setSelectedAttempt(name);
    if (!name || attemptDetails[name]) return;
    setAttemptDetails((previous) => ({ ...previous, [name]: { status: "loading" } }));
    fetchAttempt(name)
      .then((detail) => setAttemptDetails((previous) => ({
        ...previous,
        [name]: { status: "ready", detail },
      })))
      .catch(() => setAttemptDetails((previous) => ({
        ...previous,
        [name]: { status: "unavailable" },
      })));
  }

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="Incident evidence"
        title="Incident chronology"
        description="Final G4 dispositions remain ordered and distinct. Historical records are secondary and opened only on request."
        actions={
          <div className="gate-status">
            <span>Current gate</span>
            <Badge tone={g4?.status === "PASS" ? "mint" : "amber"}>{displayText(g4?.status)}</Badge>
          </div>
        }
      />

      {finalAttempts.length ? (
        <section className="incident-timeline" aria-label="Final G4 chronology">
          <div className="timeline-heading">
            <div>
              <p className="eyebrow">Final chronology</p>
              <h2>{displayText(g4?.gate)} · {displayText(g4?.name)}</h2>
            </div>
            <span className="timeline-classification">Canonical dispositions</span>
          </div>
          <ol className="timeline-list">
            {finalAttempts.map((attempt, index) => (
              <FinalAttemptRow key={attempt.attempt} attempt={attempt} last={index === finalAttempts.length - 1} />
            ))}
          </ol>
        </section>
      ) : (
        <DataUnavailable loading={loading} message="Final G4 chronology unavailable from the read API." />
      )}

      <section className="historical-attempts">
        <div className="historical-heading">
          <div>
            <p className="eyebrow">Secondary browser</p>
            <h2>Historical attempts</h2>
          </div>
        </div>
        <Accordion type="single" collapsible className="historical-browser">
          <AccordionItem value="historical-attempts">
            <AccordionTrigger>
              Browse preserved records <span className="inline-count">{historicalAttempts.length}</span>
            </AccordionTrigger>
            <AccordionContent>
              <div className="historical-browser-content">
                <p>Preserved for context; these records are not current gate evidence.</p>
                <label className="search-field historical-search">
                  <FileSearch size={16} aria-hidden="true" />
                  <span className="sr-only">Search historical attempts</span>
                  <Input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="Search attempts"
                  />
                </label>
                {historicalAttempts.length ? (
                  <Accordion
                    type="single"
                    collapsible
                    value={selectedAttempt}
                    onValueChange={selectAttempt}
                    className="attempt-list"
                  >
                    {filteredAttempts.map((attempt) => (
                      <HistoricalAttemptRow
                        key={attempt.name}
                        attempt={attempt}
                        detail={attemptDetails[attempt.name]}
                      />
                    ))}
                  </Accordion>
                ) : (
                  <p className="empty-state">No historical attempt records are available in this checkout.</p>
                )}
                {historicalAttempts.length > 0 && filteredAttempts.length === 0 && (
                  <p className="empty-state">No attempts match this search.</p>
                )}
              </div>
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </section>
    </div>
  );
}

function FinalAttemptRow({ attempt, last }: { attempt: FinalAttempt; last: boolean }) {
  const negative = attempt.state.includes("NEGATIVE");
  const inconclusive = /INCONCLUSIVE|UNSCORED|NON-RESULT|ABORT/i.test(attempt.state);
  const tone = negative ? "rose" : inconclusive ? "amber" : "outline";
  return (
    <li className={`timeline-item ${negative ? "timeline-item--negative" : ""}`}>
      <div className="timeline-spine">
        <span className={`timeline-marker ${negative ? "is-negative" : ""}`}>
          {negative ? <AlertTriangle size={15} aria-hidden="true" /> : <Clock3 size={15} aria-hidden="true" />}
        </span>
        {!last && <span className="timeline-connector" aria-hidden="true"><ArrowDown size={13} /></span>}
      </div>
      <article className="timeline-entry">
        <div className="timeline-entry-top">
          <span className="attempt-number">{attempt.attempt}</span>
          <Badge tone={tone}>{displayText(attempt.state)}</Badge>
          {negative && <span className="timeline-last-completed">Last completed attempt</span>}
        </div>
        <p className="timeline-entry-class">{displayText(attempt.classification)}</p>
        <div className="timeline-entry-foot">
          <span><ShieldCheck size={14} aria-hidden="true" /> Resolution: {displayBoolean(attempt.resolution)}</span>
          <span>Reward: {displayNumber(attempt.reward)}</span>
          <span>Time to resolve: {displayNumberWithUnit(attempt.time_to_resolve_s, "s")}</span>
        </div>
        <Accordion type="single" collapsible className="timeline-source">
          <AccordionItem value="source">
            <AccordionTrigger>Source and scoring details</AccordionTrigger>
            <AccordionContent>
              <p className="timeline-source-note">{displayText(attempt.source_note)}</p>
              <dl className="evidence-detail-grid">
                <div><dt>Source</dt><dd><code>{displayText(attempt.source?.path)}</code></dd></div>
                <div><dt>Raw record</dt><dd>{displayBoolean(attempt.raw_record_available, "Available", "Unavailable")}</dd></div>
                <div><dt>Reference SHA-256</dt><dd><code>{attempt.source?.sha256 ?? "Unavailable"}</code></dd></div>
              </dl>
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </article>
    </li>
  );
}

function HistoricalAttemptRow({
  attempt,
  detail,
}: {
  attempt: AttemptSummary;
  detail?: AttemptLoad;
}) {
  return (
    <AccordionItem value={attempt.name} className="historical-attempt">
      <AccordionTrigger>
        <span className="historical-attempt-summary">
          <span className="historical-attempt-id">{displayText(attempt.id)}</span>
          <span>{displayText(attempt.scenario)}</span>
          <Badge tone="outline">{displayText(attempt.state)}</Badge>
        </span>
      </AccordionTrigger>
      <AccordionContent>
        {!detail || detail.status === "loading" ? (
          <p className="muted-copy" role="status">Reading this historical record…</p>
        ) : detail.status === "unavailable" ? (
          <p className="muted-copy" role="status">Attempt detail unavailable. No substitute record is shown.</p>
        ) : (
          <HistoricalDetail detail={detail.detail} />
        )}
      </AccordionContent>
    </AccordionItem>
  );
}

function HistoricalDetail({ detail }: { detail: AttemptDetail }) {
  return (
    <div className="historical-detail">
      <div className="historical-detail-grid">
        <DetailBlock label="State" value={displayText(detail.state)} />
        <DetailBlock label="Timestamp" value={displayText(detail.timestamp)} />
        <DetailBlock label="Model" value={displayText(detail.model)} />
        <DetailBlock label="Scenario" value={displayText(detail.scenario)} />
        <DetailBlock label="Triage" value={displayText(detail.triage?.title)} />
        <DetailBlock label="Severity" value={displayText(detail.triage?.severity)} />
        <DetailBlock label="Root cause" value={displayText(detail.diagnosis?.specific)} />
        <DetailBlock label="Approval" value={displayText(detail.approval)} />
        <DetailBlock label="Verifier status" value={displayText(detail.verification?.status)} />
        <DetailBlock label="Environment resolved" value={displayBoolean(detail.verification?.env_resolved)} />
        <DetailBlock label="Raw SHA-256" value={detail.sha256 || "Unavailable"} />
        <DetailBlock label="Source path" value={detail.source || "Unavailable"} />
      </div>
      {detail.warnings?.length > 0 && (
        <div className="historical-warnings">
          <strong>Recorded warnings</strong>
          <ul>{detail.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </div>
      )}
      {detail.verification?.checks?.length > 0 && (
        <div className="historical-checks">
          <strong>Recorded verifier checks</strong>
          {detail.verification.checks.map((check, index) => (
            <div className="historical-check-row" key={`${check.name}:${index}`}>
              <span className={`check-dot ${check.passed ? "is-pass" : "is-negative"}`} />
              <span>{displayText(check.name)}</span>
              <span>{displayText(check.target)}</span>
              <Badge tone={check.passed ? "mint" : "rose"}>{check.passed ? "Pass" : "Not passed"}</Badge>
            </div>
          ))}
        </div>
      )}
      <p className="historical-recommendation">{displayText(detail.recommendation)}</p>
      <p className="historical-digest">Detail SHA-256 prefix: <code>{shortDigest(detail.sha256)}</code></p>
    </div>
  );
}

function DetailBlock({ label, value }: { label: string; value: string }) {
  return <div className="historical-detail-cell"><span>{label}</span><strong>{value}</strong></div>;
}
