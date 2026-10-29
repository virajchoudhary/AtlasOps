import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Activity,
  AlertTriangle,
  Check,
  CircleHelp,
  Clock3,
  Download,
  FileSearch,
  KeyRound,
  LoaderCircle,
  Pause,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Unplug,
  X,
} from "lucide-react";
import {
  fetchOperationsEvidence,
  fetchOperationsStatus,
  OperationsApiError,
  pauseOperations,
  submitOperationsDecision,
} from "../api/operations";
import type {
  ActionDigest,
  ApprovalToken,
  OperationsApiErrorKind,
  OperationsEvidenceBundle,
  OperationsExecution,
  OperationsJson,
  OperationsStatus,
  OperationDecision,
  ExecutionId,
  PendingOperationApproval,
} from "../api/operations";
import { PageHeading } from "../components/SectionHeading";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import "./OperationsPage.css";

const POLL_INTERVAL_MS = 5_000;
const MAX_VISIBLE_EVENTS = 200;

type ConnectionState = "disconnected" | "connecting" | "connected" | "error";

type EvidenceState =
  | { kind: "idle" }
  | { kind: "loading"; executionId: ExecutionId }
  | {
      kind: "ready";
      executionId: ExecutionId;
      bundle: OperationsEvidenceBundle;
    }
  | { kind: "error"; executionId: ExecutionId; message: string };

function errorFor(error: unknown): OperationsApiError {
  if (error instanceof OperationsApiError) return error;
  return new OperationsApiError("network_error");
}

function errorMessage(kind: OperationsApiErrorKind, status?: number): string {
  switch (kind) {
    case "invalid_request":
      return "Enter the operator key to connect.";
    case "unauthorized":
      return "Operator key rejected. The live snapshot and controls were cleared.";
    case "configuration_unavailable":
      return "Operational mode or operator identity is not configured.";
    case "service_unavailable":
      return "Operations evidence is unavailable. The live snapshot and controls were cleared.";
    case "malformed_response":
      return "The Operations API returned malformed data. The live snapshot and controls were cleared.";
    case "network_error":
      return "The Operations API could not be reached. No previous controls are available.";
    case "stale_action":
      return "This prepared action expired or changed. No decision was accepted; current approvals are being refreshed.";
    case "http_error":
      return `The Operations API rejected the request${status ? ` (${status})` : ""}.`;
    case "decision_rejected":
      return "The server did not accept this decision. Refreshing the current request.";
    default: {
      const _exhaustive: never = kind;
      return _exhaustive;
    }
  }
}

function isAbort(error: unknown, signal: AbortSignal): boolean {
  return signal.aborted ||
    (error instanceof DOMException && error.name === "AbortError");
}

function jsonObject(value: OperationsJson | undefined): Readonly<Record<string, OperationsJson>> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  return value;
}

function jsonText(
  value: OperationsJson | Readonly<Record<string, OperationsJson>>,
): string {
  return JSON.stringify(value, null, 2) ?? "Unavailable";
}

function timestampMilliseconds(value: number | string): number | null {
  const milliseconds = typeof value === "number"
    ? value < 100_000_000_000 ? value * 1000 : value
    : Date.parse(value);
  return Number.isFinite(milliseconds) ? milliseconds : null;
}

function formatTimestamp(value: number | string): string {
  const milliseconds = timestampMilliseconds(value);
  return milliseconds === null ? "Unavailable" : new Date(milliseconds).toLocaleString();
}

function formatRemaining(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function verificationStatus(execution: OperationsExecution): {
  label: string;
  tone: "mint" | "amber" | "rose" | "outline";
  detail: string;
} {
  const verification = jsonObject(execution.record.verification);
  const verificationState = verification?.verification_status;
  const recordResolved = execution.record.resolved === true;
  const environmentResolved = execution.record.env_resolved === true;
  const verifierResolved = verification?.env_resolved === true;
  const remediation = jsonObject(execution.record.remediation);
  const remediationFinal = jsonObject(remediation?.final);
  const approval = jsonObject(execution.record.approval);
  const approvalDecision = jsonObject(approval?.decision);

  if (
    execution.record.failure_category === "approval_permit_invalid" ||
    remediationFinal?.status === "approval_permit_invalid" ||
    approval?.status === "approval_permit_invalid" ||
    approvalDecision?.status === "permit_invalid"
  ) {
    return {
      label: "Approval permit invalid",
      tone: "rose",
      detail: "The execution ended with approval_permit_invalid; the mutation was blocked. Verification does not override that decision.",
    };
  }

  if (
    execution.state === "uncertain" ||
    execution.record.failure_category === "mutation_uncertain"
  ) {
    const replayBlocked = execution.state === "uncertain" ||
      execution.record.failure_category === "mutation_uncertain" ||
      execution.record.replay_allowed === false;
    return {
      label: "Execution outcome uncertain",
      tone: "amber",
      detail: replayBlocked
        ? "The atomic API write may have taken effect. Replay is blocked; no recovery is inferred."
        : "The execution outcome is not conclusive.",
    };
  }
  if (!execution.integrity.ok) {
    return {
      label: "Execution evidence integrity invalid",
      tone: "rose",
      detail: "The execution journal hash chain did not validate. This record cannot support a resolution claim.",
    };
  }
  if (verificationState === "failed" || environmentResolved === false) {
    return {
      label: "Verification failed",
      tone: "rose",
      detail: "The environment verifier did not confirm recovery.",
    };
  }
  if (
    verificationState === "inconclusive" ||
    verificationState === "error" ||
    verificationState === "observation_error"
  ) {
    return {
      label: "Verification inconclusive",
      tone: "amber",
      detail: "No recovery claim is supported by this verifier result.",
    };
  }
  if (
    execution.state === "completed" &&
    recordResolved &&
    environmentResolved &&
    verifierResolved &&
    verificationState === "passed"
  ) {
    return {
      label: "Resolved and verified",
      tone: "mint",
      detail: "The execution record and conclusive environment verifier agree.",
    };
  }
  if (execution.state === "completed") {
    return {
      label: "Execution completed; resolution unverified",
      tone: "amber",
      detail: "Completion alone does not establish a resolved incident.",
    };
  }
  if (execution.state === "interrupted") {
    return {
      label: "Execution interrupted",
      tone: "amber",
      detail: "No replay or recovery claim is inferred from an interrupted run.",
    };
  }
  if (execution.state === "failed") {
    return {
      label: "Execution failed",
      tone: "rose",
      detail: "The operational execution ended with a failure.",
    };
  }
  if (execution.state === "cancelled") {
    return {
      label: "Execution cancelled",
      tone: "outline",
      detail: "Cancellation does not establish recovery.",
    };
  }
  return {
    label: "Execution in progress",
    tone: "outline",
    detail: "The environment has not been verified as resolved.",
  };
}

function connectionLabel(
  connection: ConnectionState,
  error: OperationsApiError | null,
): string {
  if (connection === "connecting") return "Connecting";
  if (connection === "connected") return "Connected";
  if (connection === "error" && error) {
    if (error.kind === "unauthorized") return "Key rejected";
    if (error.kind === "configuration_unavailable") return "Not configured";
    if (error.kind === "malformed_response") return "Invalid response";
    return "Unavailable";
  }
  return "Disconnected";
}

function downloadEvidence(bundle: OperationsEvidenceBundle): void {
  const blob = new Blob([bundle.content], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = bundle.filename;
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function OperationsPage() {
  const [apiKey, setApiKey] = useState("");
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const activeKeyRef = useRef<string | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("disconnected");
  const [apiError, setApiError] = useState<OperationsApiError | null>(null);
  const [snapshot, setSnapshot] = useState<OperationsStatus | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [statusRequestPending, setStatusRequestPending] = useState(false);
  const [selectedExecutionId, setSelectedExecutionId] = useState<ExecutionId | null>(null);
  const selectedExecutionIdRef = useRef<ExecutionId | null>(null);
  const [evidenceState, setEvidenceState] = useState<EvidenceState>({ kind: "idle" });
  const [now, setNow] = useState(Date.now());
  const [pauseSubmitting, setPauseSubmitting] = useState(false);
  const [pauseMessage, setPauseMessage] = useState<string | null>(null);

  const connectionVersionRef = useRef(0);
  const selectionVersionRef = useRef(0);
  const controllersRef = useRef(new Set<AbortController>());
  const pollControllerRef = useRef<AbortController | null>(null);
  const evidenceControllerRef = useRef<AbortController | null>(null);
  const refreshRef = useRef<() => void>(() => {});

  function addController(): AbortController {
    const controller = new AbortController();
    controllersRef.current.add(controller);
    return controller;
  }

  function releaseController(controller: AbortController): void {
    controllersRef.current.delete(controller);
    if (pollControllerRef.current === controller) pollControllerRef.current = null;
    if (evidenceControllerRef.current === controller) evidenceControllerRef.current = null;
  }

  function abortOwnedRequests(): void {
    for (const controller of controllersRef.current) controller.abort();
    controllersRef.current.clear();
    pollControllerRef.current = null;
    evidenceControllerRef.current = null;
  }

  function clearStatusState(error: OperationsApiError): void {
    setSnapshot(null);
    setConnection("error");
    setApiError(error);
    setLastUpdatedAt(null);
    selectedExecutionIdRef.current = null;
    setSelectedExecutionId(null);
    selectionVersionRef.current += 1;
    setEvidenceState({ kind: "idle" });
    setPauseMessage(null);
  }

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => () => {
    connectionVersionRef.current += 1;
    selectionVersionRef.current += 1;
    abortOwnedRequests();
  }, []);

  useEffect(() => {
    if (activeKey === null) return;

    let disposed = false;
    let busy = false;
    const key = activeKey;
    const connectionVersion = connectionVersionRef.current;

    async function poll(): Promise<void> {
      if (
        disposed ||
        busy ||
        activeKeyRef.current !== key ||
        connectionVersionRef.current !== connectionVersion
      ) {
        return;
      }

      busy = true;
      setStatusRequestPending(true);
      const selectionVersion = selectionVersionRef.current;
      const controller = addController();
      pollControllerRef.current = controller;
      try {
        const next = await fetchOperationsStatus(key, controller.signal);
        if (
          disposed ||
          controller.signal.aborted ||
          activeKeyRef.current !== key ||
          connectionVersionRef.current !== connectionVersion ||
          selectionVersionRef.current !== selectionVersion
        ) {
          return;
        }

        const selectedId = selectedExecutionIdRef.current;
        if (selectedId && !next.executions.some((item) => item.execution_id === selectedId)) {
          selectedExecutionIdRef.current = null;
          setSelectedExecutionId(null);
          selectionVersionRef.current += 1;
          setEvidenceState({ kind: "idle" });
        }
        setSnapshot(next);
        setConnection("connected");
        setApiError(null);
        setLastUpdatedAt(Date.now());
      } catch (error) {
        if (
          disposed ||
          isAbort(error, controller.signal) ||
          activeKeyRef.current !== key ||
          connectionVersionRef.current !== connectionVersion ||
          selectionVersionRef.current !== selectionVersion
        ) {
          return;
        }
        clearStatusState(errorFor(error));
      } finally {
        busy = false;
        if (!disposed) setStatusRequestPending(false);
        releaseController(controller);
      }
    }

    refreshRef.current = () => {
      void poll();
    };
    void poll();
    const timer = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      if (pollControllerRef.current) pollControllerRef.current.abort();
      refreshRef.current = () => {};
    };
  }, [activeKey]);

  function disconnect(clearInput: boolean): void {
    connectionVersionRef.current += 1;
    selectionVersionRef.current += 1;
    activeKeyRef.current = null;
    abortOwnedRequests();
    setActiveKey(null);
    setConnection("disconnected");
    setApiError(null);
    setSnapshot(null);
    setLastUpdatedAt(null);
    setStatusRequestPending(false);
    selectedExecutionIdRef.current = null;
    setSelectedExecutionId(null);
    setEvidenceState({ kind: "idle" });
    setPauseMessage(null);
    setPauseSubmitting(false);
    if (clearInput) setApiKey("");
  }

  function handleKeyChange(value: string): void {
    if (activeKeyRef.current !== null) disconnect(false);
    setApiKey(value);
  }

  function connect(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (apiKey.trim().length === 0) {
      clearStatusState(new OperationsApiError("invalid_request"));
      return;
    }

    connectionVersionRef.current += 1;
    selectionVersionRef.current += 1;
    abortOwnedRequests();
    activeKeyRef.current = apiKey;
    setActiveKey(apiKey);
    setConnection("connecting");
    setApiError(null);
    setSnapshot(null);
    setLastUpdatedAt(null);
    selectedExecutionIdRef.current = null;
    setSelectedExecutionId(null);
    setEvidenceState({ kind: "idle" });
    setPauseMessage(null);
  }

  function selectExecution(executionId: ExecutionId): void {
    if (executionId === selectedExecutionIdRef.current) return;
    selectedExecutionIdRef.current = executionId;
    selectionVersionRef.current += 1;
    pollControllerRef.current?.abort();
    evidenceControllerRef.current?.abort();
    setSelectedExecutionId(executionId);
    setEvidenceState({ kind: "idle" });
  }

  async function loadEvidence(executionId: ExecutionId): Promise<void> {
    const key = activeKeyRef.current;
    if (!key) return;

    const connectionVersion = connectionVersionRef.current;
    const selectionVersion = selectionVersionRef.current;
    const controller = addController();
    evidenceControllerRef.current = controller;
    setEvidenceState({ kind: "loading", executionId });
    try {
      const bundle = await fetchOperationsEvidence({
        apiKey: key,
        executionId,
        signal: controller.signal,
      });
      if (
        controller.signal.aborted ||
        activeKeyRef.current !== key ||
        connectionVersionRef.current !== connectionVersion ||
        selectionVersionRef.current !== selectionVersion ||
        selectedExecutionIdRef.current !== executionId
      ) {
        return;
      }
      setEvidenceState({ kind: "ready", executionId, bundle });
    } catch (error) {
      if (
        isAbort(error, controller.signal) ||
        activeKeyRef.current !== key ||
        connectionVersionRef.current !== connectionVersion ||
        selectionVersionRef.current !== selectionVersion ||
        selectedExecutionIdRef.current !== executionId
      ) {
        return;
      }
      const apiError = errorFor(error);
      if (
        apiError.kind === "unauthorized" ||
        apiError.kind === "configuration_unavailable" ||
        apiError.kind === "service_unavailable" ||
        apiError.kind === "malformed_response"
      ) {
        clearStatusState(apiError);
        return;
      }
      setEvidenceState({
        kind: "error",
        executionId,
        message: errorMessage(apiError.kind, apiError.status),
      });
    } finally {
      releaseController(controller);
    }
  }

  async function decide({
    token,
    actionDigest,
    decision,
    reason,
  }: {
    token: ApprovalToken;
    actionDigest: ActionDigest;
    decision: OperationDecision;
    reason?: string;
  }): Promise<void> {
    const key = activeKeyRef.current;
    if (!key || connection !== "connected") {
      throw new OperationsApiError("invalid_request");
    }

    const connectionVersion = connectionVersionRef.current;
    const controller = addController();
    try {
      await submitOperationsDecision({
        apiKey: key,
        token,
        actionDigest,
        decision,
        ...(reason !== undefined ? { reason } : {}),
        signal: controller.signal,
      });
      if (
        activeKeyRef.current !== key ||
        connectionVersionRef.current !== connectionVersion
      ) {
        return;
      }
      setApiError(null);
      refreshRef.current();
    } catch (error) {
      if (
        isAbort(error, controller.signal) ||
        activeKeyRef.current !== key ||
        connectionVersionRef.current !== connectionVersion
      ) {
        throw error;
      }
      const apiError = errorFor(error);
      if (
        apiError.kind === "unauthorized" ||
        apiError.kind === "configuration_unavailable" ||
        apiError.kind === "service_unavailable" ||
        apiError.kind === "malformed_response"
      ) {
        clearStatusState(apiError);
      } else {
        refreshRef.current();
      }
      throw apiError;
    } finally {
      releaseController(controller);
    }
  }

  async function pauseAutomation(): Promise<void> {
    const key = activeKeyRef.current;
    if (!key || connection !== "connected" || !snapshot) return;
    const connectionVersion = connectionVersionRef.current;
    const controller = addController();
    setPauseSubmitting(true);
    setPauseMessage(null);
    try {
      const circuitBreaker = await pauseOperations({ apiKey: key, signal: controller.signal });
      if (
        controller.signal.aborted ||
        activeKeyRef.current !== key ||
        connectionVersionRef.current !== connectionVersion
      ) {
        return;
      }
      setSnapshot((current) => current
        ? { ...current, circuit_breaker: circuitBreaker }
        : current);
      setPauseMessage(circuitBreaker.tripped
        ? "Future tool execution is paused."
        : "The server did not confirm a paused state.");
      refreshRef.current();
    } catch (error) {
      if (
        isAbort(error, controller.signal) ||
        activeKeyRef.current !== key ||
        connectionVersionRef.current !== connectionVersion
      ) {
        return;
      }
      const apiError = errorFor(error);
      if (
        apiError.kind === "unauthorized" ||
        apiError.kind === "configuration_unavailable" ||
        apiError.kind === "service_unavailable" ||
        apiError.kind === "malformed_response"
      ) {
        clearStatusState(apiError);
      } else {
        setPauseMessage(errorMessage(apiError.kind, apiError.status));
      }
    } finally {
      setPauseSubmitting(false);
      releaseController(controller);
    }
  }

  const activeExecutions = snapshot?.executions.filter((execution) => execution.state === "running") ?? [];
  const terminalExecutions = snapshot?.executions.filter((execution) => execution.state !== "running") ?? [];
  const selectedExecution = snapshot?.executions.find(
    (execution) => execution.execution_id === selectedExecutionId,
  );
  const evidence = evidenceState.kind === "ready" &&
      evidenceState.executionId === selectedExecutionId
    ? evidenceState.bundle
    : null;
  const breakerTripped = snapshot?.circuit_breaker.tripped === true;
  const operatorAvailable = Boolean(snapshot?.operator_id);
  const connectionText = connectionLabel(connection, apiError);

  return (
    <div className="page-stack operations-page">
      <PageHeading
        eyebrow="Live operations"
        title="Operations"
        description="Execution status, exact-action approvals, and preserved evidence."
        actions={snapshot
          ? <Badge tone="amber">NOT_CERTIFIED</Badge>
          : undefined}
      />

      <form className="operations-connection" onSubmit={connect}>
        <label className="operations-key-field" htmlFor="operations-api-key">
          <span><KeyRound size={15} aria-hidden="true" /> Operator API key</span>
          <Input
            id="operations-api-key"
            type="password"
            value={apiKey}
            onChange={(event) => handleKeyChange(event.currentTarget.value)}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            aria-label="Operator API key"
          />
        </label>
        <div className="operations-connection-actions">
          <Button
            type="submit"
            variant="primary"
            disabled={apiKey.trim().length === 0 || connection === "connecting"}
          >
            <ShieldCheck size={15} aria-hidden="true" />
            Connect
          </Button>
          <Button
            variant="outline"
            onClick={() => disconnect(true)}
            disabled={connection === "disconnected" && apiKey.length === 0}
            aria-label="Disconnect and clear operator key"
          >
            <Unplug size={15} aria-hidden="true" />
            Disconnect
          </Button>
          <Button
            variant="quiet"
            size="icon"
            onClick={() => refreshRef.current()}
            disabled={!activeKey || connection === "connecting" || statusRequestPending}
            aria-label="Refresh operations"
            title="Refresh operations"
          >
            {statusRequestPending
              ? <LoaderCircle className="operations-spin" size={16} aria-hidden="true" />
              : <RefreshCw size={16} aria-hidden="true" />}
          </Button>
        </div>
        <div className={`operations-connection-state operations-connection-state--${connection}`} role="status">
          <i aria-hidden="true" />
          <span>{connectionText}</span>
        </div>
      </form>

      {connection === "error" && apiError && (
        <div className="operations-api-error" role="alert">
          <AlertTriangle size={16} aria-hidden="true" />
          <span>{errorMessage(apiError.kind, apiError.status)}</span>
        </div>
      )}

      {!snapshot && connection !== "error" && (
        <div className="operations-empty">
          {connection === "connecting"
            ? <LoaderCircle className="operations-spin" size={18} aria-hidden="true" />
            : <CircleHelp size={18} aria-hidden="true" />}
          <span>
            {connection === "connecting"
              ? "Reading the authenticated operations snapshot."
              : "Connect to read current executions and pending approvals."}
          </span>
        </div>
      )}

      {snapshot && (
        <>
          <section className="operations-status-strip" aria-label="Operations status">
            <div className="operations-status-item">
              <span>Source SHA</span>
              <code>{snapshot.source_sha || "Unavailable"}</code>
            </div>
            <div className="operations-status-item">
              <span>Operator</span>
              <strong>{snapshot.operator_id ?? "Not configured"}</strong>
            </div>
            <div className="operations-status-item">
              <span>Approval policy</span>
              <strong>{snapshot.approval_policy}</strong>
            </div>
            <div className="operations-status-item">
              <span>Circuit breaker</span>
              <Badge tone={breakerTripped ? "rose" : "mint"}>
                {breakerTripped ? "PAUSED" : "CLOSED"}
              </Badge>
            </div>
            <div className="operations-status-item operations-status-item--updated">
              <span>Last read</span>
              <time>{lastUpdatedAt === null ? "Unavailable" : new Date(lastUpdatedAt).toLocaleTimeString()}</time>
            </div>
            {!breakerTripped && (
              <div className="operations-pause-control">
                <Button
                  variant="outline"
                  onClick={() => void pauseAutomation()}
                  disabled={!operatorAvailable || pauseSubmitting}
                  aria-label="Pause future tool execution"
                >
                  {pauseSubmitting
                    ? <LoaderCircle className="operations-spin" size={15} aria-hidden="true" />
                    : <Pause size={15} aria-hidden="true" />}
                  Pause future tool execution
                </Button>
              </div>
            )}
            {pauseMessage && (
              <p className="operations-pause-message" role="status">{pauseMessage}</p>
            )}
          </section>

          <section className="operations-approvals" aria-labelledby="operations-approvals-title">
            <div className="operations-section-heading">
              <div>
                <p className="eyebrow">Human decision required</p>
                <h2 id="operations-approvals-title">Pending approvals</h2>
              </div>
              <span className="operations-count">{snapshot.pending.length}</span>
            </div>
            {snapshot.pending.length === 0 ? (
              <p className="operations-empty-inline">No pending operational approvals.</p>
            ) : (
              <div className="operations-approval-list">
                {snapshot.pending.map((approval) => (
                  <PendingApprovalCard
                    key={approval.token}
                    approval={approval}
                    now={now}
                    operatorAvailable={operatorAvailable}
                    onDecision={decide}
                  />
                ))}
              </div>
            )}
          </section>

          <section className="operations-executions" aria-label="Operational executions">
            <div className="operations-section-heading">
              <div>
                <p className="eyebrow">Journal</p>
                <h2>Executions</h2>
              </div>
            </div>
            <div className="operations-execution-layout">
              <div className="operations-execution-lists">
                <ExecutionList
                  title="Current"
                  subtitle="Running executions"
                  executions={activeExecutions}
                  selectedId={selectedExecutionId}
                  onSelect={selectExecution}
                />
                <ExecutionList
                  title="Preserved terminal"
                  subtitle="Completed, failed, cancelled, interrupted, or uncertain"
                  executions={terminalExecutions}
                  selectedId={selectedExecutionId}
                  onSelect={selectExecution}
                />
              </div>
              <ExecutionDetails
                execution={selectedExecution}
                evidenceState={evidenceState}
                evidence={evidence}
                onLoadEvidence={loadEvidence}
                onDownloadEvidence={downloadEvidence}
              />
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function PendingApprovalCard({
  approval,
  now,
  operatorAvailable,
  onDecision,
}: {
  approval: PendingOperationApproval;
  now: number;
  operatorAvailable: boolean;
  onDecision: (input: {
    token: ApprovalToken;
    actionDigest: ActionDigest;
    decision: OperationDecision;
    reason?: string;
  }) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submittedDecision, setSubmittedDecision] = useState<OperationDecision | null>(null);
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const expiresAt = timestampMilliseconds(approval.expires_at);
  const expired = expiresAt === null || expiresAt <= now;
  const remainingSeconds = expiresAt === null
    ? 0
    : Math.max(0, Math.ceil((expiresAt - now) / 1000));
  const decisionPending = approval.decision === "pending" && submittedDecision === null;
  const canDecide = decisionPending && !expired && operatorAvailable && !submitting;
  const severityTone = approval.severity === "P1"
    ? "rose"
    : approval.severity === "P2" ? "amber" : "outline";

  async function submit(decision: OperationDecision): Promise<void> {
    if (!canDecide || (decision === "rejected" && reason.trim().length === 0)) return;
    setSubmitting(true);
    setDecisionError(null);
    try {
      await onDecision({
        token: approval.token,
        actionDigest: approval.action_digest,
        decision,
        ...(decision === "rejected" ? { reason: reason.trim() } : {}),
      });
      setSubmittedDecision(decision);
      setReason("");
    } catch (error) {
      const apiError = errorFor(error);
      setDecisionError(errorMessage(apiError.kind, apiError.status));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <article className="operations-approval">
      <header className="operations-approval-heading">
        <div className="operations-approval-identity">
          <Badge tone={severityTone}>{approval.severity}</Badge>
          <strong>{approval.incident_id}</strong>
        </div>
        <div className="operations-expiry" role="timer" aria-label="Approval expiration countdown">
          <Clock3 size={14} aria-hidden="true" />
          <span>{expired ? "Expired" : formatRemaining(remainingSeconds)}</span>
        </div>
      </header>

      <p className="operations-approval-summary">{approval.summary}</p>

      <div className="operations-approval-meta">
        <span><span>Created</span><time>{formatTimestamp(approval.created_at)}</time></span>
        <span><span>Expires</span><time>{formatTimestamp(approval.expires_at)}</time></span>
        <span><span>Decision</span><strong>{submittedDecision ?? approval.decision}</strong></span>
      </div>

      <div className="operations-action-head">
        <div>
          <span>Exact tool</span>
          <strong><code>{approval.action.tool}</code></strong>
        </div>
        <div>
          <span>Action digest</span>
          <code className="operations-break-all">{approval.action_digest}</code>
        </div>
      </div>
      <p className="operations-action-summary">{approval.action_summary}</p>

      <div className="operations-action-payload">
        <JsonBlock label="Arguments" value={approval.action.arguments} />
        <JsonBlock label="Target identity" value={approval.action.target} />
        <JsonBlock label="Complete prepared patch" value={approval.action.patch} />
      </div>

      <div className="operations-decision">
        <label htmlFor={`reason-${approval.token}`}>Rejection reason</label>
        <textarea
          id={`reason-${approval.token}`}
          value={reason}
          onChange={(event) => setReason(event.currentTarget.value)}
          maxLength={2000}
          placeholder="Required to reject this action"
          disabled={!decisionPending || expired || submitting || !operatorAvailable}
        />
        <div className="operations-decision-actions">
          <Button
            variant="primary"
            onClick={() => void submit("approved")}
            disabled={!canDecide}
            aria-label={`Approve exact action for ${approval.incident_id}`}
          >
            {submitting
              ? <LoaderCircle className="operations-spin" size={15} aria-hidden="true" />
              : <Check size={15} aria-hidden="true" />}
            Approve exact action
          </Button>
          <Button
            variant="outline"
            onClick={() => void submit("rejected")}
            disabled={!canDecide || reason.trim().length === 0}
            aria-label={`Reject action for ${approval.incident_id}`}
          >
            <X size={15} aria-hidden="true" />
            Reject
          </Button>
          {!operatorAvailable && (
            <span className="operations-decision-note">Server operator identity is not configured.</span>
          )}
          {expired && decisionPending && (
            <span className="operations-decision-note">This approval has expired. Decisions are disabled.</span>
          )}
          {decisionError && <span className="operations-decision-error" role="alert">{decisionError}</span>}
          {submittedDecision && (
            <span className="operations-decision-success" role="status">
              {submittedDecision === "approved" ? "Approval submitted." : "Rejection submitted."}
            </span>
          )}
        </div>
      </div>
    </article>
  );
}

function JsonBlock({
  label,
  value,
}: {
  label: string;
  value: OperationsJson | Readonly<Record<string, OperationsJson>>;
}) {
  const text = jsonText(value);
  return (
    <section className="operations-json-block">
      <h4>{label}</h4>
      <pre>{text}</pre>
    </section>
  );
}

function ExecutionList({
  title,
  subtitle,
  executions,
  selectedId,
  onSelect,
}: {
  title: string;
  subtitle: string;
  executions: OperationsExecution[];
  selectedId: ExecutionId | null;
  onSelect: (executionId: ExecutionId) => void;
}) {
  return (
    <section className="operations-execution-group">
      <div className="operations-execution-group-heading">
        <div>
          <h3>{title}</h3>
          <p>{subtitle}</p>
        </div>
        <span className="operations-count">{executions.length}</span>
      </div>
      {executions.length === 0 ? (
        <p className="operations-empty-inline">No executions in this group.</p>
      ) : (
        <ul className="operations-execution-list">
          {executions.map((execution) => {
            const resolution = verificationStatus(execution);
            return (
              <li key={execution.execution_id}>
                <button
                  type="button"
                  className={`operations-execution-row ${selectedId === execution.execution_id ? "is-selected" : ""}`}
                  onClick={() => onSelect(execution.execution_id)}
                  aria-pressed={selectedId === execution.execution_id}
                >
                  <span className="operations-execution-row-top">
                    <strong>{execution.incident_id}</strong>
                    <Badge tone={execution.state === "running" ? "mint" : "outline"}>
                      {execution.state}
                    </Badge>
                  </span>
                  <span className="operations-execution-row-phase">{execution.phase}</span>
                  <span className="operations-execution-row-verdict">
                    <Badge tone={resolution.tone}>{resolution.label}</Badge>
                  </span>
                  <span className="operations-execution-row-time">
                    Updated {formatTimestamp(execution.updated_at)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function ExecutionDetails({
  execution,
  evidenceState,
  evidence,
  onLoadEvidence,
  onDownloadEvidence,
}: {
  execution: OperationsExecution | undefined;
  evidenceState: EvidenceState;
  evidence: OperationsEvidenceBundle | null;
  onLoadEvidence: (executionId: ExecutionId) => Promise<void>;
  onDownloadEvidence: (bundle: OperationsEvidenceBundle) => void;
}) {
  if (!execution) {
    return (
      <section className="operations-execution-detail operations-execution-detail--empty">
        <Activity size={20} aria-hidden="true" />
        <h3>Select an execution</h3>
        <p>Current and terminal records remain separate from approval decisions.</p>
      </section>
    );
  }

  const resolution = verificationStatus(execution);
  const verification = execution.record.verification;
  const roles = [
    ["Triage", execution.record.triage],
    ["Diagnosis", execution.record.diagnosis],
    ["Remediation", execution.record.remediation],
    ["Comms", execution.record.comms],
  ] as const;
  const visibleEvents = evidence?.evidence.events.length && evidence.evidence.events.length > MAX_VISIBLE_EVENTS
    ? evidence.evidence.events.slice(-MAX_VISIBLE_EVENTS)
    : evidence?.evidence.events ?? [];
  const replayAllowed = execution.state === "uncertain" ||
    execution.record.failure_category === "mutation_uncertain"
    ? false
    : execution.record.replay_allowed;
  const evidenceLoading = evidenceState.kind === "loading" &&
    evidenceState.executionId === execution.execution_id;
  const evidenceError = evidenceState.kind === "error" &&
    evidenceState.executionId === execution.execution_id
    ? evidenceState.message
    : null;

  return (
    <section className="operations-execution-detail" aria-labelledby="operations-detail-title">
      <header className="operations-detail-heading">
        <div>
          <p className="eyebrow">Selected execution</p>
          <h3 id="operations-detail-title">{execution.incident_id}</h3>
        </div>
        <Badge tone={resolution.tone}>{execution.state}</Badge>
      </header>
      <dl className="operations-detail-facts">
        <div><dt>Execution ID</dt><dd><code>{execution.execution_id}</code></dd></div>
        <div><dt>Phase</dt><dd>{execution.phase}</dd></div>
        <div><dt>Last updated</dt><dd>{formatTimestamp(execution.updated_at)}</dd></div>
        <div><dt>Evidence integrity</dt><dd>{execution.integrity.ok ? "Valid" : "Invalid"} · {execution.integrity.events} events</dd></div>
        <div><dt>Integrity hash</dt><dd><code className="operations-break-all">{execution.integrity.last_hash || "Unavailable"}</code></dd></div>
        <div><dt>Failure category</dt><dd>{typeof execution.record.failure_category === "string" ? execution.record.failure_category : "None recorded"}</dd></div>
        <div><dt>Replay allowed</dt><dd>{replayAllowed === true ? "Yes" : replayAllowed === false ? "No" : "Unknown"}</dd></div>
      </dl>

      <div className={`operations-resolution operations-resolution--${resolution.tone}`}>
        {resolution.tone === "mint"
          ? <ShieldCheck size={17} aria-hidden="true" />
          : resolution.tone === "rose"
            ? <ShieldAlert size={17} aria-hidden="true" />
            : <CircleHelp size={17} aria-hidden="true" />}
        <div>
          <strong>{resolution.label}</strong>
          <p>{resolution.detail}</p>
        </div>
      </div>

      <section className="operations-detail-section">
        <div className="operations-section-heading operations-section-heading--small">
          <h4>Role records</h4>
        </div>
        <div className="operations-role-records">
          {roles.map(([label, value]) => (
            value !== undefined && value !== null
              ? <JsonBlock key={label} label={label} value={value} />
              : null
          ))}
        </div>
        {verification !== undefined && verification !== null
          ? <JsonBlock label="Environment verification" value={verification} />
          : <p className="operations-empty-inline">No verifier record is available yet.</p>}
        {execution.record.prepared_action !== undefined && execution.record.prepared_action !== null && (
          <JsonBlock label="Prepared action" value={execution.record.prepared_action} />
        )}
        {execution.record.action_result !== undefined && execution.record.action_result !== null && (
          <JsonBlock label="Action result" value={execution.record.action_result} />
        )}
      </section>

      <div className="operations-evidence-actions">
        <Button
          variant="outline"
          onClick={() => void onLoadEvidence(execution.execution_id)}
          disabled={evidenceLoading}
          aria-label={`Load full event history for ${execution.incident_id}`}
        >
          {evidenceLoading
            ? <LoaderCircle className="operations-spin" size={15} aria-hidden="true" />
            : <FileSearch size={15} aria-hidden="true" />}
          Load full event history
        </Button>
        <Button
          variant="quiet"
          size="icon"
          onClick={() => evidence && onDownloadEvidence(evidence)}
          disabled={!evidence}
          aria-label={`Download evidence JSON for ${execution.incident_id}`}
          title="Download authenticated evidence JSON"
        >
          <Download size={16} aria-hidden="true" />
        </Button>
      </div>
      {evidenceError && <p className="operations-evidence-error" role="alert">{evidenceError}</p>}

      {evidence && (
        <section className="operations-event-history" aria-labelledby="operations-events-title">
          <div className="operations-event-heading">
            <div>
              <p className="eyebrow">Authenticated evidence</p>
              <h4 id="operations-events-title">Ordered event history</h4>
            </div>
            <Badge tone={evidence.evidence.audit_integrity.ok ? "mint" : "rose"}>
              Audit {evidence.evidence.audit_integrity.ok ? "valid" : "invalid"}
            </Badge>
          </div>
          {evidence.evidence.events.length > MAX_VISIBLE_EVENTS && (
            <p className="operations-event-limit">
              Showing the latest {MAX_VISIBLE_EVENTS} of {evidence.evidence.events.length} events in recorded order.
              The download contains the complete response.
            </p>
          )}
          {visibleEvents.length === 0 ? (
            <p className="operations-empty-inline">No event records are available.</p>
          ) : (
            <ol className="operations-event-list">
              {visibleEvents.map((event, index) => (
                <li key={`${event.ts}:${index}`}>
                  <div className="operations-event-line">
                    <span className="operations-event-phase">{event.phase}</span>
                    <strong>{event.event}</strong>
                    <Badge tone={event.state === "running" ? "outline" : "amber"}>{event.state}</Badge>
                    <time>{new Date(event.ts * (event.ts < 100_000_000_000 ? 1000 : 1)).toLocaleTimeString()}</time>
                  </div>
                  <details>
                    <summary>Recorded state</summary>
                    <JsonBlock label="Event record" value={event.record} />
                  </details>
                </li>
              ))}
            </ol>
          )}
          <p className="operations-audit-scope">
            Audit scope: {evidence.evidence.audit_scope}. Source SHA: {evidence.evidence.source_sha || "Unavailable"}.
            Certification remains {evidence.evidence.certification_status}.
          </p>
        </section>
      )}
    </section>
  );
}
