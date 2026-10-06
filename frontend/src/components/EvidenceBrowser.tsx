import { useMemo, useState } from "react";
import { FileText, Search } from "lucide-react";
import type { EvidenceBrowserEntry } from "../api/types";
import {
  matchesEvidenceFilter,
  shortDigest,
  type EvidenceFilter,
} from "../lib/format";
import { SectionHeading } from "./SectionHeading";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "./ui/accordion";
import { Badge } from "./ui/badge";
import { Input } from "./ui/input";

const filterOptions: { id: EvidenceFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "current", label: "Current" },
  { id: "empirical", label: "Empirical" },
  { id: "negative", label: "Negative" },
  { id: "historical", label: "Historical" },
  { id: "external", label: "External" },
];

export function EvidenceBrowser({
  evidence,
}: {
  evidence: EvidenceBrowserEntry[];
}) {
  const [filter, setFilter] = useState<EvidenceFilter>("all");
  const [query, setQuery] = useState("");
  const visible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return evidence.filter((item) => {
      const matchesQuery = !normalized
        || `${item.title} ${item.classification} ${item.area} ${item.path ?? ""} ${item.details}`.toLowerCase().includes(normalized);
      return matchesQuery && matchesEvidenceFilter(item, filter);
    });
  }, [evidence, filter, query]);
  const currentEvidence = visible.filter((item) => item.scope === "current");
  const historicalEvidence = visible.filter((item) => item.scope === "historical");

  return (
    <section className="evidence-browser" aria-labelledby="evidence-browser-title">
      <SectionHeading
        eyebrow="Read-only browser"
        title="Evidence index"
        description="Repository-backed references only. Missing files stay visible as unavailable; no external payloads are republished."
      />
      <div className="evidence-toolbar">
        <label className="search-field">
          <Search size={16} aria-hidden="true" />
          <span className="sr-only">Search evidence</span>
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search title, classification, or path"
          />
        </label>
        <div className="filter-tabs" role="group" aria-label="Filter evidence">
          {filterOptions.map((option) => (
            <button
              key={option.id}
              type="button"
              className={`filter-tab ${filter === option.id ? "is-active" : ""}`}
              aria-pressed={filter === option.id}
              onClick={() => setFilter(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <div className="evidence-columns">
        <div className="evidence-column">
          <div className="evidence-column-heading">
            <div>
              <p className="eyebrow">Repository snapshot</p>
              <h2 id="evidence-browser-title">Current evidence</h2>
            </div>
            <span className="evidence-count">{currentEvidence.length}</span>
          </div>
          {currentEvidence.length ? (
            <div className="evidence-list">
              {currentEvidence.map((item) => (
                <EvidenceRow key={`${item.path}:${item.title}`} item={item} />
              ))}
            </div>
          ) : (
            <p className="empty-state">No current evidence matches this filter.</p>
          )}
        </div>
        <div className="evidence-column evidence-column--historical">
          <div className="evidence-column-heading">
            <div>
              <p className="eyebrow">Preserved context</p>
              <h2>Historical / non-empirical</h2>
            </div>
            <span className="evidence-count">{historicalEvidence.length}</span>
          </div>
          {historicalEvidence.length ? (
            <div className="evidence-list">
              {historicalEvidence.map((item) => (
                <EvidenceRow key={`${item.path}:${item.title}`} item={item} />
              ))}
            </div>
          ) : (
            <p className="empty-state">No archived evidence references match this filter.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function EvidenceRow({ item }: { item: EvidenceBrowserEntry }) {
  const historical = item.scope === "historical";
  const isExternal = item.availability === "external";
  return (
    <article className={`evidence-row ${historical ? "evidence-row--historical" : ""}`}>
      <div className="evidence-row-mark" aria-hidden="true"><FileText size={16} /></div>
      <div className="evidence-row-main">
        <div className="evidence-row-title">
          <strong>{item.title}</strong>
          <Badge tone={historical ? "amber" : item.negative ? "rose" : item.available ? "mint" : "outline"}>
            {item.classification}
          </Badge>
        </div>
        <div className="evidence-row-meta">
          <span>{item.area}</span>
          <span aria-hidden="true">·</span>
          <span>{item.kind}</span>
          <span aria-hidden="true">·</span>
          <code>{item.availability}</code>
          {!isExternal && <>
            <span aria-hidden="true">·</span>
            <code>{shortDigest(item.sha256)}</code>
          </>}
        </div>
        <Accordion type="single" collapsible className="evidence-detail">
          <AccordionItem value="source">
            <AccordionTrigger>Source details</AccordionTrigger>
            <AccordionContent>
              <dl className="evidence-detail-grid">
                <div><dt>Classification</dt><dd>{item.classification || "Unavailable"}</dd></div>
                <div><dt>Scope / kind</dt><dd>{item.scope} · {item.kind}</dd></div>
                <div><dt>Disposition</dt><dd>{item.negative ? "Negative finding" : "No negative disposition tag"}</dd></div>
                <div><dt>Availability</dt><dd>{item.availability}</dd></div>
                <div><dt>Details</dt><dd>{item.details || "Unavailable"}</dd></div>
                {!isExternal && <>
                  <div><dt>SHA-256</dt><dd><code>{item.sha256 || "Unavailable"}</code></dd></div>
                  <div><dt>Path</dt><dd><code>{item.path || "Unavailable"}</code></dd></div>
                </>}
              </dl>
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </div>
    </article>
  );
}
