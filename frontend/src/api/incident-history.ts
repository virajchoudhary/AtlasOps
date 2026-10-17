import type {
  IncidentHistoryDetail,
  IncidentHistoryErrorKind,
  IncidentHistoryPage,
  IncidentHistorySection,
  IncidentHistorySummary,
  IncidentHistoryValue,
} from "./types";

const MAX_PAGE_LIMIT = 100;
const MAX_PAGE_OFFSET = 10_000;
const MAX_RESPONSE_CHARS = 2_097_152;
const MAX_COLLECTION_ITEMS = 100;
const MAX_VALUE_DEPTH = 4;
const MAX_TEXT_LENGTH = 2_000;
const API_KEY_NOT_CONFIGURED = "ATLASOPS_API_KEY is required for this endpoint";

const INCIDENT_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const EXECUTION_ID_PATTERN = /^[0-9a-f]{32}$/;
const SAFE_TOOL_PATTERN = /^[A-Za-z0-9_.-]{1,80}$/;
const SECRET_FIELD_PATTERN =
  /api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?(?:token|id)|authorization|proxy[_-]?authorization|token|bearer|password|passwd|secret|credential|private[_-]?key|cookie|set[_-]?cookie|basic[_-]?auth|headers?/i;
const PATH_FIELD_PATTERN =
  /(?:^|[_-])(?:path|paths|file|files|filename|file_name|directory|dir)(?:$|[_-])/i;
const SENSITIVE_TEXT_PATTERNS = [
  /\b(?:authorization|proxy-authorization)\s*:\s*(?:basic|bearer)\s+[^\s,;"'`]+/gi,
  /\b(?:cookie|set-cookie)\s*:\s*[^\r\n]+/gi,
  /\b(?:basic|bearer)\s+[A-Za-z0-9+/=._~-]{8,}/gi,
  /\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?(?:token|id)|token|authorization|password|passwd|secret|credential|private[_-]?key|cookie|basic[_-]?auth)\s*[:=]\s*["']?[^,\s"'`]+/gi,
  /\bAKIA[0-9A-Z]{16}\b/g,
  /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g,
  /https?:\/\/[^:\s/@]+:[^@\s/]+@/gi,
];
const WINDOWS_PATH_PATTERN = /\b[A-Za-z]:\\[^\s,;"'`]+/g;
const UNC_PATH_PATTERN = /\\\\[^\\/\s]+\\[^\\/\s]+\\[^\s,;"'`]+/g;
const UNIX_PATH_PATTERN = /(^|[\s("'`])\/(?:[^/\s,;"'`]+\/)*[^/\s,;"'`]+/g;
const RELATIVE_PATH_PATTERN =
  /(^|[\s("'`])(?:\.{1,2}\/)?(?:[A-Za-z0-9_.-]+\/)+[A-Za-z0-9_.-]+/g;

const TRIAGE_FIELDS = [
  "severity",
  "title",
  "blast_radius",
  "affected_services",
  "correlated_alerts",
  "handoff_notes",
] as const;
const DIAGNOSIS_FIELDS = [
  "root_cause",
  "category",
  "confidence",
  "blast_radius_update",
  "recommended_fix",
  "evidence",
] as const;
const REMEDIATION_FIELDS = [
  "mode",
  "status",
  "outcome",
  "proposed_actions",
  "executed_actions",
  "verified_by",
  "summary",
  "time_to_resolve_seconds",
  "runbook",
] as const;
const APPROVAL_FIELDS = [
  "mode",
  "severity",
  "triage_severity",
  "decision",
  "action_digest",
  "action_summary",
  "approved_by",
  "reason",
] as const;
const VERIFICATION_FIELDS = [
  "scenario_id",
  "agent_claimed_resolved",
  "env_resolved",
  "verification_status",
  "is_false_resolution",
  "is_false_negative",
  "failed_checks",
  "checks",
  "observed_metrics",
  "verification_timestamp",
] as const;
const COMMS_FIELDS = [
  "status",
  "slack_posted",
  "postmortem_path",
  "summary",
  "summary_for_dashboard",
  "lessons_learned",
] as const;
const ACTION_FIELDS = ["step", "tool", "args", "result", "status"] as const;
const CHECK_FIELDS = ["name", "target", "passed", "required"] as const;
const COMMS_POSTMORTEM_REFERENCE = "postmortem_reference_recorded";

export class IncidentHistoryApiError extends Error {
  constructor(
    readonly kind: IncidentHistoryErrorKind,
    readonly status?: number,
  ) {
    super(kind);
    this.name = "IncidentHistoryApiError";
  }
}

function malformed(): never {
  throw new IncidentHistoryApiError("malformed_response");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasField(source: Record<string, unknown>, name: string): boolean {
  return Object.prototype.hasOwnProperty.call(source, name);
}

function isOmittedKey(name: string): boolean {
  return (
    name === "__proto__" ||
    name === "constructor" ||
    name === "prototype" ||
    SECRET_FIELD_PATTERN.test(name) ||
    PATH_FIELD_PATTERN.test(name)
  );
}

function sanitizeText(value: string, maximum = MAX_TEXT_LENGTH): string {
  let safe = value;
  for (const pattern of SENSITIVE_TEXT_PATTERNS) {
    safe = safe.replace(pattern, "[REDACTED]");
  }
  safe = safe
    .replace(WINDOWS_PATH_PATTERN, "[PATH]")
    .replace(UNC_PATH_PATTERN, "[PATH]")
    .replace(UNIX_PATH_PATTERN, (_match, prefix: string) => `${prefix}[PATH]`)
    .replace(RELATIVE_PATH_PATTERN, (_match, prefix: string) => `${prefix}[PATH]`);
  if (safe.length > maximum) {
    const suffix = "[truncated]";
    safe = `${safe.slice(0, Math.max(0, maximum - suffix.length))}${suffix}`;
  }
  return safe;
}

function parseSafeNumber(value: number): number {
  if (
    !Number.isFinite(value) ||
    (Number.isInteger(value) && !Number.isSafeInteger(value))
  ) {
    return malformed();
  }
  return value;
}

function parseSafeValue(value: unknown, depth = 0): IncidentHistoryValue {
  if (depth > MAX_VALUE_DEPTH) return malformed();
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "string") return sanitizeText(value);
  if (typeof value === "number") return parseSafeNumber(value);
  if (Array.isArray(value)) {
    if (value.length > MAX_COLLECTION_ITEMS) return malformed();
    return value.map((item) => parseSafeValue(item, depth + 1));
  }
  if (isRecord(value)) {
    const keys = Object.keys(value);
    if (keys.length > MAX_COLLECTION_ITEMS) return malformed();
    const entries: [string, IncidentHistoryValue][] = [];
    for (const key of keys) {
      if (isOmittedKey(key)) continue;
      const safeKey = sanitizeText(key);
      if (safeKey) entries.push([safeKey, parseSafeValue(value[key], depth + 1)]);
    }
    return Object.fromEntries(entries);
  }
  return malformed();
}

function parseAllowedObject(
  value: unknown,
  fields: readonly string[],
): IncidentHistoryValue {
  if (!isRecord(value)) return malformed();
  const entries: [string, IncidentHistoryValue][] = [];
  for (const field of fields) {
    if (hasField(value, field)) {
      entries.push([field, parseSafeValue(value[field])]);
    }
  }
  return Object.fromEntries(entries);
}

function parseActions(value: unknown): IncidentHistoryValue {
  if (!Array.isArray(value) || value.length > MAX_COLLECTION_ITEMS) return malformed();
  return value.map((item) => parseAllowedObject(item, ACTION_FIELDS));
}

function parseEvidence(value: unknown): IncidentHistoryValue {
  if (!Array.isArray(value) || value.length > MAX_COLLECTION_ITEMS) return malformed();
  return value.map((item) => {
    if (!isRecord(item) || typeof item.tool !== "string" || !SAFE_TOOL_PATTERN.test(item.tool)) {
      return malformed();
    }
    return { tool: item.tool };
  });
}

function parseChecks(value: unknown): IncidentHistoryValue {
  if (!Array.isArray(value) || value.length > MAX_COLLECTION_ITEMS) return malformed();
  return value.map((item) => parseAllowedObject(item, CHECK_FIELDS));
}

function parseObservedMetrics(value: unknown): IncidentHistoryValue {
  if (!isRecord(value)) return malformed();
  const entries: [string, IncidentHistoryValue][] = [];
  for (const [query, result] of Object.entries(value)) {
    if (isOmittedKey(query) || typeof result !== "number") continue;
    const safeResult = parseSafeNumber(result);
    const safeQuery = sanitizeText(query, 256);
    if (safeQuery) entries.push([safeQuery, safeResult]);
  }
  return Object.fromEntries(entries);
}

function parsePostmortemReference(value: unknown): IncidentHistoryValue {
  if (typeof value !== "string") return malformed();
  return true;
}

function parseSection(
  value: unknown,
  fields: readonly string[],
  specialFields: Readonly<Record<string, (fieldValue: unknown) => IncidentHistoryValue>> = {},
): IncidentHistorySection | null {
  if (value === null) return null;
  if (!isRecord(value)) return malformed();

  const entries: [string, IncidentHistoryValue][] = [];
  for (const field of fields) {
    if (!hasField(value, field)) continue;
    const projector =
      specialFields[field] ??
      (field === "postmortem_path" ? parsePostmortemReference : undefined);
    const projectedName = field === "postmortem_path" ? COMMS_POSTMORTEM_REFERENCE : field;
    entries.push([
      projectedName,
      projector ? projector(value[field]) : parseSafeValue(value[field]),
    ]);
  }
  return Object.fromEntries(entries);
}

function requiredNullableString(source: Record<string, unknown>, name: string): string | null {
  if (!hasField(source, name)) return malformed();
  const value = source[name];
  if (value === null) return null;
  if (typeof value !== "string") return malformed();
  return sanitizeText(value);
}

function requiredNullableBoolean(
  source: Record<string, unknown>,
  name: string,
): boolean | null {
  if (!hasField(source, name)) return malformed();
  const value = source[name];
  if (value === null || typeof value === "boolean") return value;
  return malformed();
}

function optionalNullableBoolean(
  source: Record<string, unknown>,
  name: string,
): boolean | null | undefined {
  if (!hasField(source, name)) return undefined;
  const value = source[name];
  if (value === null || typeof value === "boolean") return value;
  return malformed();
}

function parseIncidentId(value: unknown): string {
  if (
    typeof value !== "string" ||
    !INCIDENT_ID_PATTERN.test(value) ||
    value.endsWith(".")
  ) {
    return malformed();
  }
  return value;
}

function parseExecutionId(value: unknown, allowNull: boolean): string | null {
  if (value === null && allowNull) return null;
  if (typeof value !== "string" || !EXECUTION_ID_PATTERN.test(value)) return malformed();
  return value;
}

function parseSummary(value: unknown): IncidentHistorySummary {
  if (!isRecord(value)) return malformed();
  const incidentId = parseIncidentId(value.incident_id);
  const severity = requiredNullableString(value, "severity");
  const title = requiredNullableString(value, "title");
  const status = requiredNullableString(value, "status");
  const resolved = requiredNullableBoolean(value, "resolved");
  const envResolved = requiredNullableBoolean(value, "env_resolved");
  const verificationStatus = requiredNullableString(value, "verification_status");
  const summary = requiredNullableString(value, "summary");
  const scenario = hasField(value, "scenario_id")
    ? { scenario_id: parseSafeValue(value.scenario_id) }
    : {};
  const common = {
    incident_id: incidentId,
    severity,
    title,
    status,
    resolved,
    env_resolved: envResolved,
    verification_status: verificationStatus,
    summary,
    ...scenario,
  };

  if (value.record_type === "canonical") {
    return {
      ...common,
      record_type: "canonical",
      execution_id: parseExecutionId(value.execution_id, true),
    };
  }
  if (value.record_type === "execution") {
    const executionId = parseExecutionId(value.execution_id, false);
    if (executionId === null) return malformed();
    return { ...common, record_type: "execution", execution_id: executionId };
  }
  return malformed();
}

function parsePage(
  value: unknown,
  expected: { limit: number; offset: number },
): IncidentHistoryPage {
  if (!isRecord(value) || !Array.isArray(value.items)) return malformed();
  if (
    !Number.isSafeInteger(value.total) ||
    typeof value.total !== "number" ||
    value.total < 0 ||
    value.items.length > expected.limit ||
    value.limit !== expected.limit ||
    value.offset !== expected.offset
  ) {
    return malformed();
  }
  return {
    items: value.items.map(parseSummary),
    total: value.total,
    limit: expected.limit,
    offset: expected.offset,
  };
}

function parseDetail(
  value: unknown,
  expected: IncidentHistorySummary,
): IncidentHistoryDetail {
  if (!isRecord(value)) return malformed();
  const incidentId = parseIncidentId(value.incident_id);
  if (incidentId !== expected.incident_id) return malformed();
  if (value.record_type !== expected.record_type) return malformed();
  const executionId = parseExecutionId(
    value.execution_id,
    expected.record_type === "canonical",
  );
  if (executionId !== expected.execution_id) return malformed();

  const triage = parseSection(value.triage, TRIAGE_FIELDS);
  const diagnosis = parseSection(value.diagnosis, DIAGNOSIS_FIELDS, {
    evidence: parseEvidence,
  });
  const remediation = parseSection(value.remediation, REMEDIATION_FIELDS, {
    proposed_actions: parseActions,
    executed_actions: parseActions,
  });
  const approval = parseSection(value.approval, APPROVAL_FIELDS);
  const verification = parseSection(value.verification, VERIFICATION_FIELDS, {
    checks: parseChecks,
    observed_metrics: parseObservedMetrics,
  });
  const comms = parseSection(value.comms, COMMS_FIELDS);
  const optional = {
    ...(hasField(value, "scenario_id")
      ? { scenario_id: parseSafeValue(value.scenario_id) }
      : {}),
    ...(hasField(value, "episode_status")
      ? { episode_status: parseSafeValue(value.episode_status) }
      : {}),
    ...(hasField(value, "execution_mode")
      ? { execution_mode: parseSafeValue(value.execution_mode) }
      : {}),
    ...(hasField(value, "evidence_class")
      ? { evidence_class: parseSafeValue(value.evidence_class) }
      : {}),
    ...(hasField(value, "certification_status")
      ? { certification_status: parseSafeValue(value.certification_status) }
      : {}),
    ...(hasField(value, "agent_claimed_resolved")
      ? { agent_claimed_resolved: optionalNullableBoolean(value, "agent_claimed_resolved") }
      : {}),
    ...(hasField(value, "env_resolved")
      ? { env_resolved: optionalNullableBoolean(value, "env_resolved") }
      : {}),
    ...(hasField(value, "resolved")
      ? { resolved: optionalNullableBoolean(value, "resolved") }
      : {}),
  };
  const common = {
    incident_id: incidentId,
    ...optional,
    triage,
    diagnosis,
    remediation,
    approval,
    verification,
    comms,
  };

  if (expected.record_type === "canonical") {
    return { ...common, record_type: "canonical", execution_id: executionId };
  }
  if (executionId === null) return malformed();
  return { ...common, record_type: "execution", execution_id: executionId };
}

async function readResponseBody(response: Response): Promise<unknown> {
  let text: string;
  try {
    text = await response.text();
  } catch {
    return malformed();
  }
  if (text.length > MAX_RESPONSE_CHARS) return malformed();
  try {
    const value: unknown = JSON.parse(text);
    return value;
  } catch {
    return malformed();
  }
}

async function getProtectedJson(
  url: string,
  apiKey: string,
  signal?: AbortSignal,
): Promise<unknown> {
  if (apiKey.length === 0) throw new IncidentHistoryApiError("invalid_request");

  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers: {
        Accept: "application/json",
        "X-AtlasOps-Key": apiKey,
      },
      cache: "no-store",
      credentials: "omit",
      ...(signal ? { signal } : {}),
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new IncidentHistoryApiError("network_error");
  }

  if (response.status === 401) throw new IncidentHistoryApiError("unauthorized", 401);
  if (response.status === 404) throw new IncidentHistoryApiError("not_found", 404);
  if (response.status === 503) {
    const body = await readResponseBody(response);
    if (isRecord(body) && body.detail === API_KEY_NOT_CONFIGURED) {
      throw new IncidentHistoryApiError("configuration_unavailable", 503);
    }
    throw new IncidentHistoryApiError("service_unavailable", 503);
  }
  if (!response.ok) {
    throw new IncidentHistoryApiError("http_error", response.status);
  }
  return readResponseBody(response);
}

export async function fetchIncidentHistoryPage({
  apiKey,
  limit,
  offset,
  signal,
}: {
  apiKey: string;
  limit: number;
  offset: number;
  signal?: AbortSignal;
}): Promise<IncidentHistoryPage> {
  if (
    !Number.isSafeInteger(limit) ||
    limit < 1 ||
    limit > MAX_PAGE_LIMIT ||
    !Number.isSafeInteger(offset) ||
    offset < 0 ||
    offset > MAX_PAGE_OFFSET
  ) {
    throw new IncidentHistoryApiError("invalid_request");
  }

  const query = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  const payload = await getProtectedJson(`/incidents/history?${query.toString()}`, apiKey, signal);
  return parsePage(payload, { limit, offset });
}

export async function fetchIncidentHistoryDetail({
  apiKey,
  record,
  signal,
}: {
  apiKey: string;
  record: IncidentHistorySummary;
  signal?: AbortSignal;
}): Promise<IncidentHistoryDetail> {
  const path = `/incidents/${encodeURIComponent(record.incident_id)}`;
  const url =
    record.record_type === "execution"
      ? `${path}?execution_id=${encodeURIComponent(record.execution_id)}`
      : path;
  const payload = await getProtectedJson(url, apiKey, signal);
  return parseDetail(payload, record);
}
