import { useMemo, useState } from "react";
import { BookOpen, Search, Wrench } from "lucide-react";
import { PageHeading, DataUnavailable } from "../components/SectionHeading";
import { Badge } from "../components/ui/badge";
import { Input } from "../components/ui/input";
import type { CatalogPageProps } from "./types";
import { displayText } from "../lib/format";

export function RunbooksPage({ catalog, loading }: CatalogPageProps) {
  const [query, setQuery] = useState("");
  const runbooks = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return (catalog?.runbooks ?? []).filter((runbook) =>
      !normalized || `${runbook.id} ${runbook.title} ${runbook.category} ${runbook.description} ${runbook.tools.join(" ")}`
        .toLowerCase().includes(normalized)
    );
  }, [catalog?.runbooks, query]);

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="Historical reference"
        title="Runbook catalog"
        description="A searchable catalog only. Recommendations and ranking inference are not run here."
        actions={
          <label className="search-field runbook-search">
            <Search size={16} aria-hidden="true" />
            <span className="sr-only">Search runbooks</span>
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search catalog"
            />
          </label>
        }
      />
      <div className="runbook-notice">
        <BookOpen size={16} aria-hidden="true" />
        <span>Optional historical research · no feedback-based recommendation claim</span>
      </div>
      {catalog ? (
        runbooks.length ? (
          <section className="runbook-grid" aria-label="Runbooks">
            {runbooks.map((runbook) => (
              <article className="runbook-row" key={runbook.id}>
                <div className="runbook-mark" aria-hidden="true"><Wrench size={17} /></div>
                <div className="runbook-main">
                  <div className="runbook-titleline">
                    <h2>{displayText(runbook.title)}</h2>
                    <Badge tone="outline">{displayText(runbook.category)}</Badge>
                  </div>
                  <p>{displayText(runbook.description)}</p>
                  <div className="runbook-tools">
                    <span>Suggested tools</span>
                    {runbook.tools?.length
                      ? runbook.tools.map((tool) => <Badge key={tool} tone="neutral">{tool}</Badge>)
                      : <span className="muted-copy">{displayText(null)}</span>}
                  </div>
                </div>
              </article>
            ))}
          </section>
        ) : (
          <p className="empty-state">No runbooks match this search.</p>
        )
      ) : (
        <DataUnavailable loading={loading} message="Runbook catalog unavailable from the read API." />
      )}
    </div>
  );
}
