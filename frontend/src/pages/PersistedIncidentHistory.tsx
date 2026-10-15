import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, FileSearch, KeyRound, RefreshCw, X } from "lucide-react";
import { fetchIncidentHistoryDetail, fetchIncidentHistoryPage, IncidentHistoryApiError } from "../api/incident-history";
import type {
  IncidentHistoryDetail,
  IncidentHistoryErrorKind,
  IncidentHistoryPage,
  IncidentHistorySection,
  IncidentHistorySummary,
  IncidentHistoryValue,
} from "../api/types";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";

const PAGE_SIZE = 25;

type HistoryConnectionState =
  | { kind: "locked" }
  | { kind: "connected"; apiKey: string };

type HistoryListState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; page: IncidentHistoryPage }
  | { kind: "empty"; page: IncidentHistoryPage }
  | { kind: "error"; error: IncidentHistoryApiError };

type HistoryDetailState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; detail: IncidentHistoryDetail }
  | { kind: "error"; error: IncidentHistoryApiError };

export function PersistedIncidentHistory() {
  const [connection, setConnection] = useState<HistoryConnectionState>({ kind: "locked" });
  const [keyDraft, setKeyDraft] = useState("");
  const [offset, setOffset] = useState(0);
  const [listRevision, setListRevision] = useState(0);
  const [detailRevision, setDetailRevision] = useState(0);
  const [listState, setListState] = useState<HistoryListState>({ kind: "idle" });
  const [detailState, setDetailState] = useState<HistoryDetailState>({ kind: "idle" });
  const [selectedRecord, setSelectedRecord] = useState<IncidentHistorySummary | null>(null);
  const listController = useRef<AbortController | null>(null);
  const detailController = useRef<AbortController | null>(null);
  const apiKey = connection.kind === "connected" ? connection.apiKey : null;

  function cancelRequests() {
    listController.current?.abort();
    detailController.current?.abort();
    listController.current = null;
    detailController.current = null;
  }

  function lockHistory(nextDraft = "") {
    cancelRequests();
    setConnection({ kind: "locked" });
    setKeyDraft(nextDraft);
    setOffset(0);
    setSelectedRecord(null);
    setListState({ kind: "idle" });
    setDetailState({ kind: "idle" });
  }

  function updateKeyDraft(nextValue: string) {
    if (connection.kind === "connected") {
      lockHistory(nextValue);
      return;
    }
    setKeyDraft(nextValue);
  }

  function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (keyDraft.length === 0) return;
    cancelRequests();
    setSelectedRecord(null);
    setOffset(0);
    setDetailState({ kind: "idle" });
    setListState({ kind: "loading" });
    setConnection({ kind: "connected", apiKey: keyDraft });
    setKeyDraft("");
  }

  function refresh() {
    if (connection.kind !== "connected") return;
    cancelRequests();
    setSelectedRecord(null);
    setDetailState({ kind: "idle" });
    setListRevision((current) => current + 1);
  }

  function changePage(nextOffset: number) {
    if (nextOffset === offset || nextOffset < 0) return;
    cancelRequests();
    setSelectedRecord(null);
    setDetailState({ kind: "idle" });
    setOffset(nextOffset);
  }

  function retryList() {
    setListRevision((current) => current + 1);
  }

  function retryDetail() {
    setDetailRevision((current) => current + 1);
  }

  function toggleRecord(record: IncidentHistorySummary) {
    setSelectedRecord((current) =>
      current !== null && historyRecordKey(current) === historyRecordKey(record)
        ? null
        : record,
    );
    setDetailState({ kind: "idle" });
  }

  useEffect(() => {
    if (apiKey === null) return;

    const controller = new AbortController();
    let current = true;
    listController.current = controller;
    setListState({ kind: "loading" });

    fetchIncidentHistoryPage({
      apiKey,
      limit: PAGE_SIZE,
      offset,
      signal: controller.signal,
    })
      .then((page) => {
        if (!current || controller.signal.aborted) return;
        setListState(
          page.total === 0
            ? { kind: "empty", page }
            : { kind: "ready", page },
        );
      })
      .catch((error: unknown) => {
        if (!current || controller.signal.aborted) return;
        setListState({ kind: "error", error: requestError(error) });
      });

    return () => {
      current = false;
      controller.abort();
      if (listController.current === controller) listController.current = null;
    };
  }, [apiKey, offset, listRevision]);

  useEffect(() => {
    if (apiKey === null || selectedRecord === null) return;

    const controller = new AbortController();
    let current = true;
    detailController.current = controller;
    setDetailState({ kind: "loading" });

    fetchIncidentHistoryDetail({
      apiKey,
      record: selectedRecord,
      signal: controller.signal,
    })
      .then((detail) => {
        if (!current || controller.signal.aborted) return;
        setDetailState({ kind: "ready", detail });
      })
      .catch((error: unknown) => {
        if (!current || controller.signal.aborted) return;
        setDetailState({ kind: "error", error: requestError(error) });
      });

    return () => {
      current = false;
      controller.abort();
      if (detailController.current === controller) detailController.current = null;
    };
  }, [apiKey, selectedRecord, detailRevision]);

  const page =
    listState.kind === "ready" || listState.kind === "empty"
      ? listState.page
      : null;
  const canGoNext = page !== null && offset + page.limit < page.total;
  const firstRow = page && page.items.length > 0 ? page.offset + 1 : 0;
  const lastRow = page ? page.offset + page.items.length : 0;
  const pageCount = page ? Math.max(1, Math.ceil(page.total / page.limit)) : 0;
  const pageNumber = page ? Math.floor(page.offset / page.limit) + 1 : 0;
  const selectedKey = selectedRecord ? historyRecordKey(selectedRecord) : null;

  return (
    <section
      className="historical-attempts incident-timeline persisted-history"
      aria-labelledby="persisted-history-title"
    >
      <header className="historical-heading persisted-history__heading">
        <div>
          <p className="eyebrow">Persisted runtime records</p>
          <h2 id="persisted-history-title">Workflow history</h2>
          <p>Persisted workflow records are separate from the research chronology above.</p>
        </div>
        <Badge tone={connection.kind === "locked" ? "amber" : "outline"}>
          {connection.kind === "locked" ? "Locked" : "Key entered"}
        </Badge>
      </header>

      <form className="evidence-toolbar persisted-history__access" onSubmit={connect}>
        <label className="persisted-history__key-field">
          <span>AtlasOps access key</span>
          <Input
            type="password"
            value={keyDraft}
            onChange={(event) => updateKeyDraft(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            aria-label="AtlasOps access key"
          />
        </label>
        <Button type="submit" variant="primary" disabled={keyDraft.length === 0}>
          <KeyRound size={15} aria-hidden="true" />
          Connect
        </Button>
        <Button
          type="button"
          variant="quiet"
          size="icon"
          aria-label="Clear key and history"
          title="Clear key and history"
          onClick={() => lockHistory()}
        >
          <X size={16} aria-hidden="true" />
        </Button>
      </form>

      <p className="muted-copy" role="status" aria-live="polite">
        {connection.kind === "locked"
          ? "Locked. No persisted history request has been made."
          : "Access key is held in this view only."}
      </p>

      {connection.kind === "connected" && (
        <>
          <div className="historical-heading persisted-history__list-heading">
            <div>
              <h3>Persisted records</h3>
              {page && (
                <p>
                  {firstRow}-{lastRow} of {page.total}
                </p>
              )}
            </div>
            <div className="persisted-history__list-tools">
              <Button
                type="button"
                variant="quiet"
                size="icon"
                aria-label="Refresh workflow history"
                title="Refresh workflow history"
                onClick={refresh}
              >
                <RefreshCw size={16} aria-hidden="true" />
              </Button>
              {page && page.total > 0 && (
                <span className="persisted-history__page-count">
                  Page {pageNumber} of {pageCount}
                </span>
              )}
            </div>
          </div>

          {listState.kind === "loading" && (
            <div className="data-unavailable is-loading" role="status">
              <span className="status-mark" aria-hidden="true" />
              <span>Reading persisted workflow history…</span>
            </div>
          )}
          {listState.kind === "empty" && (
            <p className="empty-state persisted-history__notice" role="status">
              No persisted workflow records were found.
            </p>
          )}
          {listState.kind === "error" && (
            <RequestNotice error={listState.error} onRetry={retryList} />
          )}
          {page && page.total > 0 && page.items.length === 0 && (
            <p className="empty-state persisted-history__notice" role="status">
              No records are present in this page range. Refresh to read the current history.
            </p>
          )}
          {page && page.items.length > 0 && (
            <div className="persisted-history__table-scroll">
              <table className="persisted-history__table">
                <thead>
                  <tr>
                    <th scope="col">Persisted record</th>
                    <th scope="col">Title</th>
                    <th scope="col">Recorded status</th>
                    <th scope="col">Record resolved flag</th>
                    <th scope="col">Environment resolved</th>
                    <th scope="col"><span className="sr-only">Details</span></th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((record) => {
                    const recordKey = historyRecordKey(record);
                    const isSelected = selectedKey === recordKey;
                    return (
                      <tr key={recordKey}>
                        <th scope="row">
                          <div className="historical-attempt-summary">
                            <span><Badge tone="outline">{record.record_type}</Badge></span>
                            <span><code>{record.incident_id}</code></span>
                            <span>
                              execution_id: {record.execution_id === null ? "null" : record.execution_id}
                            </span>
                          </div>
                        </th>
                        <td>{displayNullableText(record.title)}</td>
                        <td>{displayNullableText(record.status)}</td>
                        <td>{displayNullableBoolean(record.resolved)}</td>
                        <td>{displayNullableBoolean(record.env_resolved)}</td>
                        <td>
                          <Button
                            type="button"
                            variant="quiet"
                            size="sm"
                            aria-label={`Open ${record.record_type} record ${record.incident_id}`}
                            aria-expanded={isSelected}
                            aria-controls={isSelected ? "persisted-history-detail" : undefined}
                            onClick={() => toggleRecord(record)}
                          >
                            <FileSearch size={15} aria-hidden="true" />
                            {isSelected ? "Close" : "Details"}
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <nav className="persisted-history__pagination" aria-label="Workflow history pages">
            <Button
              type="button"
              variant="quiet"
              size="icon"
              aria-label="Previous history page"
              title="Previous history page"
              disabled={offset === 0}
              onClick={() => changePage(Math.max(0, offset - PAGE_SIZE))}
            >
              <ChevronLeft size={17} aria-hidden="true" />
            </Button>
            <Button
              type="button"
              variant="quiet"
              size="icon"
              aria-label="Next history page"
              title="Next history page"
              disabled={!canGoNext || listState.kind === "loading"}
              onClick={() => changePage(offset + PAGE_SIZE)}
            >
              <ChevronRight size={17} aria-hidden="true" />
            </Button>
          </nav>

          {selectedRecord && (
            <div id="persisted-history-detail" className="historical-detail persisted-history__detail">
              {detailState.kind === "loading" && (
                <div className="data-unavailable is-loading" role="status">
                  <span className="status-mark" aria-hidden="true" />
                  <span>Reading this exact persisted record…</span>
                </div>
              )}
              {detailState.kind === "error" && (
                <RequestNotice error={detailState.error} onRetry={retryDetail} />
              )}
              {detailState.kind === "ready" && (
                <HistoryRecordDetail detail={detailState.detail} />
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function RequestNotice({
  error,
  onRetry,
}: {
  error: IncidentHistoryApiError;
  onRetry: () => void;
}) {
  return (
    <div className="data-unavailable" role="alert">
      <span className="status-mark" aria-hidden="true" />
      <span>{errorMessage(error.kind, error.status)}</span>
      <Button type="button" variant="outline" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

function errorMessage(kind: IncidentHistoryErrorKind, status?: number): string {
  switch (kind) {
    case "unauthorized":
      return "The entered access key was rejected (HTTP 401).";
    case "configuration_unavailable":
      return "The service has no configured operator API key (HTTP 503).";
    case "service_unavailable":
      return "Persisted incident history is unavailable (HTTP 503).";
    case "not_found":
      return "This exact persisted record was not found (HTTP 404). No substitute record was selected.";
    case "malformed_response":
      return "The history response did not match the supported safe record shape. No details were displayed.";
    case "network_error":
      return "The history service could not be reached. No record data is available.";
    case "http_error":
      return `The history request failed with HTTP ${status ?? "unknown"}.`;
    case "invalid_request":
      return "The history request parameters were outside the supported range.";
    default: {
      const _exhaustive: never = kind;
      return _exhaustive;
    }
  }
}

function requestError(error: unknown): IncidentHistoryApiError {
  return error instanceof IncidentHistoryApiError
    ? error
    : new IncidentHistoryApiError("malformed_response");
}

function historyRecordKey(record: IncidentHistorySummary): string {
  return `${record.record_type}:${record.incident_id}:${record.execution_id ?? "null"}`;
}

function displayNullableText(value: string | null): string {
  return value && value.trim() ? value : "Unavailable";
}

function displayNullableBoolean(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return "Unknown";
  return value ? "Yes" : "No";
}

function HistoryRecordDetail({ detail }: { detail: IncidentHistoryDetail }) {
  const overviewFields: DisplayField[] = [
    { label: "Record type", value: detail.record_type },
    { label: "Incident ID", value: detail.incident_id },
    {
      label: "Execution ID",
      value: detail.execution_id === null ? "null" : detail.execution_id,
    },
    { label: "Scenario ID", value: detail.scenario_id },
    { label: "Episode status", value: detail.episode_status },
    { label: "Execution mode", value: detail.execution_mode },
    { label: "Evidence class", value: detail.evidence_class },
    { label: "Certification status", value: detail.certification_status },
    { label: "Recorded resolved flag", value: displayNullableBoolean(detail.resolved) },
    {
      label: "Top-level agent_claimed_resolved",
      value: displayNullableBoolean(detail.agent_claimed_resolved),
    },
    {
      label: "Top-level env_resolved",
      value: displayNullableBoolean(detail.env_resolved),
    },
  ];

  return (
    <div className="historical-detail-grid persisted-history__detail-content">
      <header className="historical-heading persisted-history__detail-heading">
        <div>
          <p className="eyebrow">Exact persisted record</p>
          <h3>{detail.incident_id}</h3>
        </div>
        <Badge tone="outline">{detail.record_type}</Badge>
      </header>

      <DetailSection title="Record overview">
        <DisplayFields fields={overviewFields} />
      </DetailSection>
      <DetailSection title="Triage">
        <SectionFields
          section={detail.triage}
          fields={[
            ["severity", "Severity"],
            ["title", "Title"],
            ["blast_radius", "Blast radius"],
            ["affected_services", "Affected services"],
            ["correlated_alerts", "Correlated alerts"],
            ["handoff_notes", "Handoff notes"],
          ]}
        />
      </DetailSection>
      <DetailSection title="Diagnosis">
        <SectionFields
          section={detail.diagnosis}
          fields={[
            ["root_cause", "Root cause"],
            ["category", "Category"],
            ["confidence", "Confidence"],
            ["blast_radius_update", "Blast-radius update"],
            ["recommended_fix", "Recommended fix"],
            ["evidence", "Evidence tools"],
          ]}
        />
      </DetailSection>
      <DetailSection title="Approval">
        <SectionFields
          section={detail.approval}
          fields={[
            ["mode", "Mode"],
            ["severity", "Severity"],
            ["triage_severity", "Triage severity"],
            ["decision", "Decision"],
            ["action_digest", "Action digest"],
            ["action_summary", "Action summary"],
            ["approved_by", "Approved by"],
            ["reason", "Reason"],
          ]}
        />
      </DetailSection>
      <DetailSection title="Remediation and actions">
        <SectionFields
          section={detail.remediation}
          fields={[
            ["mode", "Mode"],
            ["status", "Recorded status"],
            ["outcome", "Recorded outcome"],
            ["verified_by", "Agent-reported verified_by text"],
            ["summary", "Summary"],
            ["time_to_resolve_seconds", "Recorded time to resolve (seconds)"],
            ["runbook", "Runbook"],
          ]}
        />
        <ActionFields title="Proposed actions" value={detail.remediation?.proposed_actions} />
        <ActionFields title="Executed actions" value={detail.remediation?.executed_actions} />
      </DetailSection>
      <DetailSection title="Environment verification">
        <SectionFields
          section={detail.verification}
          fields={[
            ["scenario_id", "Scenario ID"],
            ["agent_claimed_resolved", "Agent claimed resolved"],
            ["env_resolved", "Environment resolved (recorded)"],
            ["verification_status", "Verification status"],
            ["is_false_resolution", "False-resolution flag"],
            ["is_false_negative", "False-negative flag"],
            ["failed_checks", "Failed checks"],
            ["checks", "Checks"],
            ["observed_metrics", "Observed metrics"],
            ["verification_timestamp", "Verification timestamp"],
          ]}
        />
      </DetailSection>
      <DetailSection title="Communications">
        <SectionFields
          section={detail.comms}
          fields={[
            ["status", "Status"],
            ["slack_posted", "Slack posted"],
            ["postmortem_reference_recorded", "Postmortem reference"],
            ["summary", "Summary"],
            ["summary_for_dashboard", "Dashboard summary"],
            ["lessons_learned", "Lessons learned"],
          ]}
        />
      </DetailSection>
    </div>
  );
}

interface DisplayField {
  label: string;
  value: IncidentHistoryValue | undefined;
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  const headingId = `history-section-${slugify(title)}`;
  return (
    <section
      className="historical-checks historical-detail-cell persisted-history__detail-section"
      aria-labelledby={headingId}
    >
      <h4 id={headingId}>{title}</h4>
      {children}
    </section>
  );
}

function DisplayFields({ fields }: { fields: DisplayField[] }) {
  return (
    <dl className="historical-detail-grid persisted-history__fields">
      {fields.map(({ label, value }) => (
        <div className="historical-detail-cell" key={label}>
          <dt>{label}</dt>
          <dd>
            {value === undefined ? (
              <span>Unavailable</span>
            ) : (
              <SafeHistoryValue value={value} />
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function SectionFields({
  section,
  fields,
}: {
  section: IncidentHistorySection | null;
  fields: [string, string][];
}) {
  if (section === null) {
    return <p className="muted-copy">Not recorded.</p>;
  }
  return (
    <dl className="historical-detail-grid persisted-history__fields">
      {fields.map(([key, label]) => {
        const present = Object.prototype.hasOwnProperty.call(section, key);
        return (
          <div className="historical-detail-cell" key={key}>
            <dt>{label}</dt>
            <dd>
              {present ? (
                <SafeHistoryValue value={section[key]} />
              ) : (
                <span>Unavailable</span>
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function ActionFields({ title, value }: { title: string; value: IncidentHistoryValue | undefined }) {
  if (value === undefined) return null;
  if (!Array.isArray(value)) {
    return (
      <div className="historical-checks persisted-history__action-list">
        <strong>{title}</strong>
        <SafeHistoryValue value={value} />
      </div>
    );
  }
  if (value.length === 0) {
    return (
      <div className="historical-checks persisted-history__action-list">
        <strong>{title}</strong>
        <p className="muted-copy">No actions recorded.</p>
      </div>
    );
  }

  return (
    <div className="historical-checks persisted-history__action-list">
      <strong>{title}</strong>
      <ol>
        {value.map((action, index) => (
          <li key={`${title}:${index}`}>
            <strong>Action {index + 1}</strong>
            <SafeHistoryValue value={action} />
          </li>
        ))}
      </ol>
    </div>
  );
}

function SafeHistoryValue({ value }: { value: IncidentHistoryValue }) {
  if (value === null) return <span>Unknown</span>;
  if (typeof value === "boolean") return <span>{value ? "Yes" : "No"}</span>;
  if (typeof value === "number") return <span>{Number.isFinite(value) ? value.toLocaleString() : "Unavailable"}</span>;
  if (typeof value === "string") return <span>{value.trim() ? value : "Empty"}</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span>None recorded</span>;
    return (
      <ul className="persisted-history__value-list">
        {value.map((item, index) => (
          <li key={index}>
            <SafeHistoryValue value={item} />
          </li>
        ))}
      </ul>
    );
  }

  const entries = Object.entries(value);
  if (entries.length === 0) return <span>No structured fields</span>;
  return (
    <dl className="historical-detail-grid persisted-history__nested-fields">
      {entries.map(([key, item]) => (
        <div className="historical-detail-cell" key={key}>
          <dt>{key}</dt>
          <dd>
            <SafeHistoryValue value={item} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function slugify(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}
