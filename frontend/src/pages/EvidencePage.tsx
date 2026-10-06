import { PageHeading } from "../components/SectionHeading";
import { EvidenceBrowser } from "../components/EvidenceBrowser";
import type { CatalogPageProps } from "./types";

export function EvidencePage({ catalog, loading }: CatalogPageProps) {
  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="Evidence"
        title="Provenance before presentation."
        description="Browse current repository references separately from historical or non-empirical material."
      />
      {catalog?.evidence_browser ? (
        <EvidenceBrowser
          evidence={catalog.evidence_browser}
        />
      ) : catalog ? (
        <div className="evidence-placeholder" role="status">
          Evidence classification is unavailable from the read API.
        </div>
      ) : (
        <div className="evidence-placeholder" role="status">
          {loading ? "Reading evidence index…" : "Evidence index unavailable from the read API."}
        </div>
      )}
    </div>
  );
}
