const OPERATIONS_URL = "/api/operations";
const MAX_RESPONSE_CHARS = 8_388_608;
const MAX_JSON_DEPTH = 20;
const MAX_COLLECTION_ITEMS = 10_000;
const EXECUTION_ID_PATTERN = /^[0-9a-f]{32}$/;
const ACTION_DIGEST_PATTERN = /^[0-9a-f]{64}$/;

declare const executionIdBrand: unique symbol;
declare const approvalTokenBrand: unique symbol;
declare const actionDigestBrand: unique symbol;

export type ExecutionId = string & { readonly [executionIdBrand]: "ExecutionId" };
export type ApprovalToken = string & { readonly [approvalTokenBrand]: "ApprovalToken" };
export type ActionDigest = string & { readonly [actionDigestBrand]: "ActionDigest" };

export type OperationsJson =
  | null
  | boolean
  | number
  | string
  | OperationsJson[]
  | { readonly [key: string]: OperationsJson };

export type OperationPhase =
  | "queued"
  | "triage"
  | "diagnosis"
  | "remediation"
  | "approval"
  | "applying"
  | "verification"
  | "comms"
  | "finished";

export type OperationState =
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted"
  | "uncertain";

export type OperationDecision = "approved" | "rejected";

export interface OperationsIntegrity {
  ok: boolean;
  events: number;
  last_hash: string;
}

export interface OperationsExecution {
  incident_id: string;
  execution_id: ExecutionId;
  phase: OperationPhase;
  state: OperationState;
  updated_at: number | string;
  record: Readonly<Record<string, OperationsJson>>;
  integrity: OperationsIntegrity;
}

export interface PreparedOperationAction {
  tool: string;
  arguments: Readonly<Record<string, OperationsJson>>;
  target: Readonly<Record<string, OperationsJson>>;
  patch: OperationsJson;
}

export interface PendingOperationApproval {
  incident_id: string;
  severity: string;
  summary: string;
  token: ApprovalToken;
  created_at: number | string;
  decision: string;
  approved_by: string;
  reason: string;
  action: PreparedOperationAction;
  action_digest: ActionDigest;
  action_summary: string;
  expires_at: number | string;
}

export interface OperationsCircuitBreaker {
  tripped: boolean;
  [key: string]: OperationsJson;
}

export interface OperationsStatus {
  enabled: true;
  source_sha: string;
  certification_status: "NOT_CERTIFIED";
  operator_id: string | null;
  approval_policy: "exact-action-all-mutations";
  executions: OperationsExecution[];
  pending: PendingOperationApproval[];
  circuit_breaker: OperationsCircuitBreaker;
}

export interface OperationsEvent {
  execution_id: ExecutionId;
  event: string;
  phase: OperationPhase;
  state: OperationState;
  record: Readonly<Record<string, OperationsJson>>;
  ts: number;
  audit_entry_hash: string | null;
}

export interface OperationsEvidence extends OperationsExecution {
  source_sha: string;
  certification_status: "NOT_CERTIFIED";
  events: OperationsEvent[];
  audit_integrity: OperationsIntegrity;
  audit_scope: string;
  audit: OperationsJson[];
}

export interface OperationsEvidenceBundle {
  evidence: OperationsEvidence;
  content: string;
  filename: string;
}

export type OperationsApiErrorKind =
  | "invalid_request"
  | "unauthorized"
  | "configuration_unavailable"
  | "service_unavailable"
  | "malformed_response"
  | "network_error"
  | "http_error"
  | "stale_action"
  | "decision_rejected";

export class OperationsApiError extends Error {
  constructor(
    readonly kind: OperationsApiErrorKind,
    readonly status?: number,
    readonly detail?: string,
  ) {
    super(kind);
    this.name = "OperationsApiError";
  }
}

function malformed(): never {
  throw new OperationsApiError("malformed_response");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isOperationsJsonObject(
  value: OperationsJson,
): value is Readonly<Record<string, OperationsJson>> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseJsonValue(value: unknown, depth = 0): OperationsJson {
  if (depth > MAX_JSON_DEPTH) return malformed();
  if (value === null || typeof value === "boolean" || typeof value === "string") return value;
  if (typeof value === "number") {
    if (
      !Number.isFinite(value) ||
      (Number.isInteger(value) && !Number.isSafeInteger(value))
    ) {
      return malformed();
    }
    return value;
  }
  if (Array.isArray(value)) {
    if (value.length > MAX_COLLECTION_ITEMS) return malformed();
    return value.map((item) => parseJsonValue(item, depth + 1));
  }
  if (isRecord(value)) {
    const keys = Object.keys(value);
    if (keys.length > MAX_COLLECTION_ITEMS) return malformed();
    const entries: [string, OperationsJson][] = [];
    for (const key of keys) {
      if (key === "__proto__" || key === "constructor" || key === "prototype") {
        return malformed();
      }
      entries.push([key, parseJsonValue(value[key], depth + 1)]);
    }
    return Object.fromEntries(entries);
  }
  return malformed();
}

function parseJsonRecord(value: unknown): Readonly<Record<string, OperationsJson>> {
  if (!isRecord(value)) return malformed();
  const parsed = parseJsonValue(value);
  if (!isOperationsJsonObject(parsed)) return malformed();
  return parsed;
}

function requiredString(value: unknown): string {
  if (typeof value !== "string") return malformed();
  return value;
}

function requiredNonEmptyString(value: unknown): string {
  if (typeof value !== "string" || value.trim().length === 0) return malformed();
  return value;
}

function parseTimestamp(value: unknown): number | string {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.length > 0 && Number.isFinite(Date.parse(value))) {
    return value;
  }
  return malformed();
}

function parseExecutionId(value: unknown): ExecutionId {
  if (typeof value !== "string" || !EXECUTION_ID_PATTERN.test(value)) return malformed();
  return value as ExecutionId;
}

function parseApprovalToken(value: unknown): ApprovalToken {
  if (typeof value !== "string" || value.trim().length === 0 || value.length > 256) {
    return malformed();
  }
  return value as ApprovalToken;
}

function parseActionDigest(value: unknown): ActionDigest {
  if (typeof value !== "string" || !ACTION_DIGEST_PATTERN.test(value)) return malformed();
  return value as ActionDigest;
}

function parsePhase(value: unknown): OperationPhase {
  switch (value) {
    case "queued":
    case "triage":
    case "diagnosis":
    case "remediation":
    case "approval":
    case "applying":
    case "verification":
    case "comms":
    case "finished":
      return value;
    default:
      return malformed();
  }
}

function parseState(value: unknown): OperationState {
  switch (value) {
    case "running":
    case "completed":
    case "failed":
    case "cancelled":
    case "interrupted":
    case "uncertain":
      return value;
    default:
      return malformed();
  }
}

function parseIntegrity(value: unknown): OperationsIntegrity {
  if (!isRecord(value)) return malformed();
  const events = value.events;
  if (
    typeof value.ok !== "boolean" ||
    typeof events !== "number" ||
    !Number.isSafeInteger(events) ||
    events < 0
  ) {
    return malformed();
  }
  return {
    ok: value.ok,
    events,
    last_hash: requiredString(value.last_hash),
  };
}

function parseExecution(value: unknown): OperationsExecution {
  if (!isRecord(value)) return malformed();
  return {
    incident_id: requiredNonEmptyString(value.incident_id),
    execution_id: parseExecutionId(value.execution_id),
    phase: parsePhase(value.phase),
    state: parseState(value.state),
    updated_at: parseTimestamp(value.updated_at),
    record: parseJsonRecord(value.record),
    integrity: parseIntegrity(value.integrity),
  };
}

function parsePreparedAction(value: unknown): PreparedOperationAction {
  if (!isRecord(value)) return malformed();
  const tool = requiredNonEmptyString(value.tool);
  const args = parseJsonRecord(value.arguments);
  const target = parseJsonRecord(value.target);
  if (Object.keys(target).length === 0) return malformed();

  const patch = parseJsonValue(value.patch);
  if (
    !((Array.isArray(patch) && patch.length > 0)
      || (isOperationsJsonObject(patch) && Object.keys(patch).length > 0))
  ) {
    return malformed();
  }
  return { tool, arguments: args, target, patch };
}

function parsePendingApproval(value: unknown): PendingOperationApproval {
  if (!isRecord(value)) return malformed();
  return {
    incident_id: requiredNonEmptyString(value.incident_id),
    severity: requiredNonEmptyString(value.severity),
    summary: requiredString(value.summary),
    token: parseApprovalToken(value.token),
    created_at: parseTimestamp(value.created_at),
    decision: requiredString(value.decision),
    approved_by: requiredString(value.approved_by),
    reason: requiredString(value.reason),
    action: parsePreparedAction(value.action),
    action_digest: parseActionDigest(value.action_digest),
    action_summary: requiredNonEmptyString(value.action_summary),
    expires_at: parseTimestamp(value.expires_at),
  };
}

function parseCircuitBreaker(value: unknown): OperationsCircuitBreaker {
  const parsed = parseJsonRecord(value);
  const tripped = parsed.tripped;
  if (typeof tripped !== "boolean") return malformed();
  return { ...parsed, tripped };
}

function parseOperationsStatus(value: unknown): OperationsStatus {
  if (!isRecord(value) || value.enabled !== true) return malformed();
  if (value.certification_status !== "NOT_CERTIFIED") return malformed();
  if (value.approval_policy !== "exact-action-all-mutations") return malformed();
  if (!Array.isArray(value.executions) || value.executions.length > 100) return malformed();
  if (!Array.isArray(value.pending) || value.pending.length > 1000) return malformed();

  const operatorId = value.operator_id;
  if (operatorId !== null && typeof operatorId !== "string") return malformed();

  return {
    enabled: true,
    source_sha: requiredString(value.source_sha),
    certification_status: "NOT_CERTIFIED",
    operator_id: operatorId,
    approval_policy: "exact-action-all-mutations",
    executions: value.executions.map(parseExecution),
    pending: value.pending.map(parsePendingApproval),
    circuit_breaker: parseCircuitBreaker(value.circuit_breaker),
  };
}

function parseExecutionEvent(value: unknown, expectedId: ExecutionId): OperationsEvent {
  if (!isRecord(value)) return malformed();
  const executionId = parseExecutionId(value.execution_id);
  if (executionId !== expectedId) return malformed();
  const hash = value.audit_entry_hash;
  if (hash !== undefined && hash !== null && typeof hash !== "string") return malformed();
  if (typeof value.ts !== "number" || !Number.isFinite(value.ts)) return malformed();
  return {
    execution_id: executionId,
    event: requiredNonEmptyString(value.event),
    phase: parsePhase(value.phase),
    state: parseState(value.state),
    record: parseJsonRecord(value.record),
    ts: value.ts,
    audit_entry_hash: typeof hash === "string" ? hash : null,
  };
}

function parseOperationsEvidence(
  value: unknown,
  expectedId: ExecutionId,
): OperationsEvidence {
  if (!isRecord(value)) return malformed();
  const execution = parseExecution(value);
  if (execution.execution_id !== expectedId) return malformed();
  if (value.certification_status !== "NOT_CERTIFIED") return malformed();
  if (!Array.isArray(value.events) || value.events.length > MAX_COLLECTION_ITEMS) {
    return malformed();
  }
  if (!Array.isArray(value.audit)) return malformed();
  const auditIntegrity = parseIntegrity(value.audit_integrity);
  return {
    ...execution,
    source_sha: requiredString(value.source_sha),
    certification_status: "NOT_CERTIFIED",
    events: value.events.map((event) => parseExecutionEvent(event, expectedId)),
    audit_integrity: auditIntegrity,
    audit_scope: requiredNonEmptyString(value.audit_scope),
    audit: value.audit.map((item) => parseJsonValue(item)),
  };
}

function readParsedJson(text: string): { ok: true; value: unknown } | { ok: false } {
  try {
    const value: unknown = JSON.parse(text);
    return { ok: true, value };
  } catch {
    return { ok: false };
  }
}

function errorDetail(value: unknown): string | undefined {
  if (!isRecord(value)) return undefined;
  if (typeof value.detail === "string") return value.detail.slice(0, 240);
  if (typeof value.error === "string") return value.error.slice(0, 240);
  return undefined;
}

async function protectedJson({
  apiKey,
  url,
  method = "GET",
  body,
  signal,
}: {
  apiKey: string;
  url: string;
  method?: "GET" | "POST";
  body?: Readonly<Record<string, string>>;
  signal?: AbortSignal;
}): Promise<{ value: unknown; text: string }> {
  if (apiKey.trim().length === 0) {
    throw new OperationsApiError("invalid_request");
  }

  const headers = new Headers({
    Accept: "application/json",
    "X-AtlasOps-Key": apiKey,
  });
  if (body !== undefined) headers.set("Content-Type", "application/json");

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      cache: "no-store",
      credentials: "omit",
      ...(signal ? { signal } : {}),
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new OperationsApiError("network_error");
  }

  let text: string;
  try {
    text = await response.text();
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new OperationsApiError("network_error");
  }
  if (text.length > MAX_RESPONSE_CHARS) {
    throw new OperationsApiError("malformed_response");
  }
  const parsed = readParsedJson(text);
  const value = parsed.ok ? parsed.value : null;
  const detail = errorDetail(value);

  if (response.status === 401) {
    throw new OperationsApiError("unauthorized", 401);
  }
  if (response.status === 503) {
    const kind = detail && /not enabled|operator identity is required/i.test(detail)
      ? "configuration_unavailable"
      : "service_unavailable";
    throw new OperationsApiError(kind, 503);
  }
  if (
    response.status === 400 &&
    [
      "expired",
      "action_digest_mismatch",
      "unknown_token",
      "already_decided",
      "No matching prepared operational action",
    ].includes(detail ?? "")
  ) {
    throw new OperationsApiError("stale_action", 400, detail);
  }
  if (!response.ok) {
    throw new OperationsApiError("http_error", response.status, detail);
  }
  if (!parsed.ok) {
    throw new OperationsApiError("malformed_response");
  }
  return { value, text };
}

export async function fetchOperationsStatus(
  apiKey: string,
  signal?: AbortSignal,
): Promise<OperationsStatus> {
  const response = await protectedJson({
    apiKey,
    url: OPERATIONS_URL,
    signal,
  });
  return parseOperationsStatus(response.value);
}

export async function submitOperationsDecision({
  apiKey,
  token,
  actionDigest,
  decision,
  reason,
  signal,
}: {
  apiKey: string;
  token: ApprovalToken;
  actionDigest: ActionDigest;
  decision: OperationDecision;
  reason?: string;
  signal?: AbortSignal;
}): Promise<void> {
  const body = {
    token,
    action_digest: actionDigest,
    decision,
    ...(reason && reason.trim().length > 0 ? { reason: reason.trim() } : {}),
  };
  const response = await protectedJson({
    apiKey,
    url: `${OPERATIONS_URL}/decision`,
    method: "POST",
    body,
    signal,
  });
  if (!isRecord(response.value) || typeof response.value.ok !== "boolean") {
    return malformed();
  }
  if (response.value.ok !== true) {
    throw new OperationsApiError(
      "decision_rejected",
      400,
      errorDetail(response.value),
    );
  }
}

export async function pauseOperations({
  apiKey,
  signal,
}: {
  apiKey: string;
  signal?: AbortSignal;
}): Promise<OperationsCircuitBreaker> {
  const response = await protectedJson({
    apiKey,
    url: `${OPERATIONS_URL}/pause`,
    method: "POST",
    signal,
  });
  return parseCircuitBreaker(response.value);
}

export async function fetchOperationsEvidence({
  apiKey,
  executionId,
  signal,
}: {
  apiKey: string;
  executionId: ExecutionId;
  signal?: AbortSignal;
}): Promise<OperationsEvidenceBundle> {
  const response = await protectedJson({
    apiKey,
    url: `${OPERATIONS_URL}/${encodeURIComponent(executionId)}/evidence`,
    signal,
  });
  return {
    evidence: parseOperationsEvidence(response.value, executionId),
    content: response.text,
    filename: `atlasops-${executionId}.json`,
  };
}
