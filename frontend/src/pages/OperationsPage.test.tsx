import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OperationsPage } from "./OperationsPage";

const TEST_KEY = "fixture-only-operator-key";
const SECOND_KEY = "second-fixture-operator-key";
const EXECUTION_ID = "a".repeat(32);
const SECOND_EXECUTION_ID = "b".repeat(32);
const ACTION_DIGEST = "f".repeat(64);
const SOURCE_SHA = "a".repeat(40);

function response(payload: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
  };
}

function integrity(ok = true) {
  return { ok, events: 3, last_hash: "c".repeat(64) };
}

function execution({
  executionId = EXECUTION_ID,
  incidentId = "incident-current",
  state = "running",
  phase = "remediation",
  sourceSha = SOURCE_SHA,
  record = {},
}: {
  executionId?: string;
  incidentId?: string;
  state?: string;
  phase?: string;
  sourceSha?: string | null;
  record?: Record<string, unknown>;
} = {}) {
  return {
    incident_id: incidentId,
    execution_id: executionId,
    source_sha: sourceSha,
    phase,
    state,
    updated_at: 1_800_000_000,
    record: {
      evidence_class: "OPERATIONAL_UNCERTIFIED",
      resolved: false,
      env_resolved: null,
      verification: { verification_status: "unknown", env_resolved: null },
      triage: { final: { severity: "P1", title: "Checkout latency" }, trajectory: ["alert received"] },
      diagnosis: { final: { root_cause: "synthetic fixture" }, trajectory: [] },
      remediation: { final: { status: "prepared" }, trajectory: [] },
      comms: { final: { status: "deferred" }, trajectory: [] },
      replay_allowed: false,
      ...record,
    },
    integrity: integrity(),
  };
}

function pendingApproval({
  expiresAt = Math.floor(Date.now() / 1000) + 300,
  actionDigest = ACTION_DIGEST,
  action = {
    tool: "kubectl_scale",
    arguments: { deployment: "checkoutservice", namespace: "default", replicas: 3 },
    target: {
      server: "https://cluster.fixture.invalid",
      context: "kind-atlasops-local",
      api_version: "apps/v1",
      kind: "Deployment",
      namespace: "default",
      name: "checkoutservice",
      uid: "uid-fixture",
      resource_version: "17",
    },
    patch: [
      { op: "test", path: "/metadata/uid", value: "uid-fixture" },
      { op: "replace", path: "/spec/replicas", value: 3 },
    ],
  },
}: {
  expiresAt?: number;
  actionDigest?: string;
  action?: Record<string, unknown>;
} = {}) {
  return {
    incident_id: "incident-current",
    severity: "P1",
    summary: "Checkout needs an exact, identity-bound workload change.",
    token: "apr-fixture-token",
    created_at: Math.floor(Date.now() / 1000),
    decision: "pending",
    approved_by: "",
    reason: "",
    action,
    action_digest: actionDigest,
    action_summary: "kubectl_scale arguments={...} target={...}",
    expires_at: expiresAt,
  };
}

function statusFixture({
  executions = [execution()],
  pending = [pendingApproval()],
  operatorId = "operator-fixture",
  tripped = false,
}: {
  executions?: unknown[];
  pending?: unknown[];
  operatorId?: string | null;
  tripped?: boolean;
} = {}) {
  return {
    enabled: true,
    source_sha: SOURCE_SHA,
    certification_status: "NOT_CERTIFIED",
    operator_id: operatorId,
    approval_policy: "exact-action-all-mutations",
    executions,
    pending,
    circuit_breaker: {
      tripped,
      active_incidents: 1,
      consecutive_failures: 0,
      cluster_mutating_actions_this_hour: 1,
    },
  };
}

function evidenceFixture(
  executionId = EXECUTION_ID,
  event = "old-event-from-prior-selection",
) {
  return {
    ...execution({ executionId }),
    source_sha: SOURCE_SHA,
    certification_status: "NOT_CERTIFIED",
    events: [
      {
        execution_id: executionId,
        event,
        phase: "queued",
        state: "running",
        record: { evidence_class: "OPERATIONAL_UNCERTIFIED" },
        ts: 1_800_000_000,
        audit_entry_hash: "d".repeat(64),
      },
    ],
    audit_integrity: { ok: true, entries: 3 },
    audit_scope: "last_1000_global_records",
    audit: [{ action_type: "execution_progress" }],
  };
}

function evidenceForExecution(
  executionRecord: ReturnType<typeof execution>,
  auditIntegrity = { ok: true, entries: 3 },
) {
  return {
    ...executionRecord,
    certification_status: "NOT_CERTIFIED",
    events: [
      {
        execution_id: executionRecord.execution_id,
        event: "verification_completed",
        phase: "verification",
        state: "completed",
        record: {
          resolved: true,
          env_resolved: true,
          verification: { verification_status: "passed", env_resolved: true },
        },
        ts: 1_800_000_000,
        audit_entry_hash: "d".repeat(64),
      },
    ],
    audit_integrity: auditIntegrity,
    audit_scope: "last_1000_global_records",
    audit: [{ action_type: "execution_progress" }],
  };
}

function deferred<T>() {
  let resolve: (value: T) => void = () => {
    throw new Error("Deferred result has not been initialized.");
  };
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("Operations page", () => {
  it("does not call a pending execution's absent environment observation a failed verification", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(statusFixture({
      executions: [execution({ phase: "approval" })],
    }))));
    const user = userEvent.setup();
    render(<OperationsPage />);
    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    const row = await screen.findByRole("button", { name: /^incident-current/ });
    expect(row).not.toHaveTextContent("Verification failed");
    expect(row).not.toHaveTextContent("Resolved and verified");
  });

  it.each([
    ["rejected", "Approval rejected"],
    ["timeout", "Approval timed out"],
  ])("names a terminal %s decision when no action was verified", async (decision, label) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(statusFixture({
      executions: [execution({
        state: "completed", phase: "finished",
        record: {
          approval: { decision },
          env_resolved: false,
          verification: { verification_status: "inconclusive", env_resolved: null },
        },
      })],
      pending: [],
    }))));
    const user = userEvent.setup();
    render(<OperationsPage />);
    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    const row = await screen.findByRole("button", { name: /^incident-current/ });
    expect(row).toHaveTextContent(label);
    expect(row).not.toHaveTextContent("Resolved and verified");
  });

  it("shows the full prepared action and never treats completion without a verifier as resolution", async () => {
    const completed = execution({
      executionId: SECOND_EXECUTION_ID,
      incidentId: "incident-completed-unknown",
      state: "completed",
      phase: "finished",
      record: {
        resolved: true,
        env_resolved: true,
        verification: { verification_status: "unknown", env_resolved: null },
      },
    });
    const uncertain = execution({
      executionId: "d".repeat(32),
      incidentId: "incident-uncertain",
      state: "uncertain",
      phase: "applying",
      record: {
        resolved: true,
        env_resolved: true,
        verification: { verification_status: "passed", env_resolved: true },
        failure_category: "mutation_uncertain",
        replay_allowed: false,
      },
    });
    const approvalPermitInvalid = execution({
      executionId: "c".repeat(32),
      incidentId: "incident-approval-permit-invalid",
      state: "completed",
      phase: "finished",
      record: {
        resolved: true,
        env_resolved: true,
        verification: { verification_status: "passed", env_resolved: true },
        remediation: { final: { status: "approval_permit_invalid" } },
      },
    });
    const invalidIntegrity = {
      ...execution({
        executionId: "e".repeat(32),
        incidentId: "incident-invalid-integrity",
        state: "completed",
        phase: "finished",
        record: {
          resolved: true,
          env_resolved: true,
          verification: { verification_status: "passed", env_resolved: true },
        },
      }),
      integrity: integrity(false),
    };
    const fetchMock = vi.fn().mockResolvedValue(response(statusFixture({
      executions: [execution(), completed, approvalPermitInvalid, invalidIntegrity, uncertain],
    })));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<OperationsPage />);

    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));

    expect(await screen.findByRole("heading", { name: "Pending approvals" })).toBeInTheDocument();
    expect(screen.getByText("NOT_CERTIFIED")).toBeInTheDocument();
    expect(screen.getByText("exact-action-all-mutations")).toBeInTheDocument();
    expect(screen.getByText("kubectl_scale")).toBeInTheDocument();
    expect(screen.getByText(ACTION_DIGEST)).toBeInTheDocument();
    expect(screen.getByText(/resource_version/)).toBeInTheDocument();
    expect(screen.getByText(/"replace"/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /incident-completed-unknown/ }));
    const completedDetail = await screen.findByRole("region", { name: "incident-completed-unknown" });
    expect(await within(completedDetail).findByText("Execution completed; resolution unverified")).toBeInTheDocument();
    expect(screen.queryByText("Resolved and verified")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /incident-approval-permit-invalid/ }));
    const permitInvalidDetail = await screen.findByRole("region", { name: "incident-approval-permit-invalid" });
    expect(await within(permitInvalidDetail).findByText("Approval permit invalid")).toBeInTheDocument();
    expect(screen.queryByText("Resolved and verified")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /incident-invalid-integrity/ }));
    const invalidIntegrityDetail = await screen.findByRole("region", { name: "incident-invalid-integrity" });
    expect(await within(invalidIntegrityDetail).findByText("Execution evidence integrity invalid")).toBeInTheDocument();
    expect(screen.queryByText("Resolved and verified")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /incident-uncertain/ }));
    const uncertainDetail = await screen.findByRole("region", { name: "incident-uncertain" });
    expect(await within(uncertainDetail).findByText("Execution outcome uncertain")).toBeInTheDocument();
    expect(within(uncertainDetail).getByText("No")).toBeInTheDocument();
    expect(screen.queryByText("Resolved and verified")).not.toBeInTheDocument();
  });

  it.each([
    {
      outcome: "approval.decision is rejected",
      label: "Approval rejected",
      record: { approval: { decision: "rejected" } },
    },
    {
      outcome: "approval.decision is timed out",
      label: "Approval timed out",
      record: { approval: { decision: "timeout" } },
    },
    {
      outcome: "remediation is rejected",
      label: "Approval rejected",
      record: { remediation: { final: { status: "approval_rejected" } } },
    },
    {
      outcome: "remediation is timed out",
      label: "Approval timed out",
      record: { remediation: { final: { status: "approval_timeout" } } },
    },
  ])("never reports a resolved state when $outcome despite positive verifier data", async ({ label, record }) => {
    const blockedExecution = execution({
      state: "completed",
      phase: "finished",
      record: {
        resolved: true,
        env_resolved: true,
        verification: { verification_status: "passed", env_resolved: true },
        ...record,
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(statusFixture({
      executions: [blockedExecution],
      pending: [],
    }))));
    const user = userEvent.setup();
    render(<OperationsPage />);

    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await user.click(await screen.findByRole("button", { name: /incident-current/ }));
    const detail = await screen.findByRole("region", { name: "incident-current" });

    expect(await within(detail).findByText(label)).toBeInTheDocument();
    expect(within(detail).queryByText("Resolved and verified")).not.toBeInTheDocument();
  });

  it("requires a rejection reason and submits the selected decision", async () => {
    const verificationTarget = {
      server: "https://cluster.fixture.invalid",
      context: "kind-atlasops-local",
      api_version: "apps/v1",
      kind: "Deployment",
      namespace: "default",
      name: "paymentservice",
      uid: "uid-verification-fixture",
      resource_version: "23",
    };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/operations/decision") {
        return Promise.resolve(response({ ok: true, decision: "rejected" }));
      }
      return Promise.resolve(response(statusFixture({
        pending: [pendingApproval({
          action: {
            tool: "chaos_stop_experiment",
            arguments: { name: "checkout-fault", kind: "PodChaos", namespace: "chaos-mesh" },
            target: {
              server: "https://cluster.fixture.invalid",
              context: "kind-atlasops-local",
              api_version: "chaos-mesh.org/v1alpha1",
              kind: "PodChaos",
              namespace: "chaos-mesh",
              name: "checkout-fault",
              uid: "uid-chaos-fixture",
              resource_version: "17",
            },
            verification_target: verificationTarget,
            patch: {
              apiVersion: "v1",
              kind: "DeleteOptions",
              preconditions: { uid: "uid-chaos-fixture", resourceVersion: "17" },
            },
          },
        })],
      })));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<OperationsPage />);

    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    const rejectButton = await screen.findByRole("button", { name: "Reject action for incident-current" });
    expect(screen.getByText("Verification target identity")).toBeInTheDocument();
    expect(screen.getByText(/uid-verification-fixture/)).toBeInTheDocument();
    expect(rejectButton).toBeDisabled();

    await user.type(screen.getByLabelText("Rejection reason"), "Observed target is not the affected workload.");
    expect(rejectButton).toBeEnabled();
    await user.click(rejectButton);

    expect(await screen.findByText("Rejection submitted.")).toBeInTheDocument();
    const decisionCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/operations/decision");
    if (!decisionCall) throw new Error("Expected an authenticated decision POST.");
    const [, options] = decisionCall;
    expect(JSON.parse(String(options?.body))).toEqual({
      token: "apr-fixture-token",
      action_digest: ACTION_DIGEST,
      decision: "rejected",
      reason: "Observed target is not the affected workload.",
    });
  });

  it("shows a stale-action rejection and refreshes the displayed digest", async () => {
    const changedDigest = "e".repeat(64);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(statusFixture()))
      .mockResolvedValueOnce(response({ ok: false, error: "action_digest_mismatch" }, 400))
      .mockResolvedValueOnce(response(statusFixture({
        pending: [pendingApproval({ actionDigest: changedDigest })],
      })));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<OperationsPage />);

    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await user.type(screen.getByLabelText("Rejection reason"), "The prepared resource changed.");
    await user.click(await screen.findByRole("button", { name: "Reject action for incident-current" }));

    expect(await screen.findByText(/prepared action expired or changed/i)).toBeInTheDocument();
    expect(screen.getByText(changedDigest)).toBeInTheDocument();
    const decisionCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/operations/decision");
    if (!decisionCall) throw new Error("Expected a decision POST for the displayed action.");
    const [, options] = decisionCall;
    expect(JSON.parse(String(options?.body)).action_digest).toBe(ACTION_DIGEST);
  });

  it("keeps pause confirmation when a pre-pause status read finishes late", async () => {
    vi.useFakeTimers();
    const staleRead = deferred<ReturnType<typeof response>>();
    const pausedRead = deferred<ReturnType<typeof response>>();
    let statusReads = 0;
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
      if (String(input) === "/api/operations/pause") {
        return Promise.resolve(response(statusFixture({ tripped: true }).circuit_breaker));
      }
      statusReads += 1;
      if (statusReads === 1) return Promise.resolve(response(statusFixture()));
      if (statusReads === 2) return staleRead.promise;
      return pausedRead.promise;
    }));
    render(<OperationsPage />);
    fireEvent.change(screen.getByLabelText("Operator API key"), { target: { value: TEST_KEY } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    });
    expect(screen.getByText("CLOSED")).toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(5_000));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Pause future tool execution" }));
    });
    expect(screen.getByText("PAUSED")).toBeInTheDocument();

    await act(async () => {
      staleRead.resolve(response(statusFixture({ tripped: false })));
    });
    expect(screen.getByText("PAUSED")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh operations" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Pause future tool execution" })).not.toBeInTheDocument();

    await act(async () => {
      pausedRead.resolve(response(statusFixture({ tripped: true })));
    });
    expect(screen.getByRole("button", { name: "Refresh operations" })).toBeEnabled();
    expect(screen.getByText("Future tool execution is paused.")).toBeInTheDocument();
  });

  it("clears a verified claim when the operations snapshot becomes unavailable", async () => {
    const verified = execution({
      state: "completed",
      phase: "finished",
      record: {
        resolved: true,
        env_resolved: true,
        verification: { verification_status: "passed", env_resolved: true },
      },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(statusFixture({ executions: [verified], pending: [] })))
      .mockResolvedValueOnce(response({ detail: "Operational evidence is unavailable" }, 503));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<OperationsPage />);

    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await user.click(await screen.findByRole("button", { name: /incident-current/ }));
    const detail = await screen.findByRole("region", { name: "incident-current" });
    expect(await within(detail).findByText("Resolved and verified")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Refresh operations" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/operations evidence is unavailable/i);
    expect(screen.queryByText("Resolved and verified")).not.toBeInTheDocument();
  });

  it("keeps an exact execution revision unverified after full evidence is missing and status is unchanged", async () => {
    const verified = execution({
      state: "completed",
      phase: "finished",
      record: {
        resolved: true,
        env_resolved: true,
        verification: { verification_status: "passed", env_resolved: true },
      },
    });
    const unchangedStatus = response(statusFixture({ executions: [verified], pending: [] }));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(unchangedStatus)
      .mockResolvedValueOnce(response({ detail: "Evidence record not found" }, 404))
      .mockResolvedValueOnce(unchangedStatus);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<OperationsPage />);

    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    const executionRow = await screen.findByRole("button", { name: /incident-current/ });
    await user.click(executionRow);
    const detail = await screen.findByRole("region", { name: "incident-current" });
    expect(await within(detail).findByText("Resolved and verified")).toBeInTheDocument();

    await user.click(within(detail).getByRole("button", { name: /Load full event history/ }));

    expect(await within(detail).findByRole("alert")).toHaveTextContent(/HTTP 404/i);
    expect(await within(detail).findByText("Evidence unavailable; verification unconfirmed")).toBeInTheDocument();
    expect(screen.queryByText("Resolved and verified")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Refresh operations" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    expect(within(detail).getByText("Evidence unavailable; verification unconfirmed")).toBeInTheDocument();
    expect(screen.queryByText("Resolved and verified")).not.toBeInTheDocument();
  });

  it("requires matching healthy full evidence before clearing an evidence review block", async () => {
    const verified = execution({
      state: "completed",
      phase: "finished",
      record: {
        resolved: true,
        env_resolved: true,
        verification: { verification_status: "passed", env_resolved: true },
      },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(statusFixture({ executions: [verified], pending: [] })))
      .mockResolvedValueOnce(response({ detail: "Evidence record not found" }, 404))
      .mockResolvedValueOnce(response(evidenceForExecution(verified, { ok: false, entries: 3 })))
      .mockResolvedValueOnce(response(evidenceForExecution(verified)));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<OperationsPage />);

    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await user.click(await screen.findByRole("button", { name: /incident-current/ }));
    const detail = await screen.findByRole("region", { name: "incident-current" });
    await user.click(within(detail).getByRole("button", { name: /Load full event history/ }));
    expect(await within(detail).findByText("Evidence unavailable; verification unconfirmed")).toBeInTheDocument();

    await user.click(within(detail).getByRole("button", { name: /Load full event history/ }));
    expect(await within(detail).findByText("Evidence integrity invalid; verification unconfirmed")).toBeInTheDocument();
    expect(screen.queryByText("Resolved and verified")).not.toBeInTheDocument();

    await user.click(within(detail).getByRole("button", { name: /Load full event history/ }));
    expect(await within(detail).findByText("verification_completed")).toBeInTheDocument();
    expect(within(detail).getByText("Resolved and verified")).toBeInTheDocument();
  });

  it.each([
    { label: "historical", sourceSha: "1".repeat(40) },
    { label: "unknown", sourceSha: null },
  ])("shows the $label execution source separately when full evidence is missing", async ({ sourceSha }) => {
    const preserved = execution({ sourceSha, state: "completed", phase: "finished" });
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(statusFixture({ executions: [preserved], pending: [] })))
      .mockResolvedValueOnce(response({ detail: "Evidence record not found" }, 404)));
    const user = userEvent.setup();
    render(<OperationsPage />);
    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await user.click(await screen.findByRole("button", { name: /incident-current/ }));
    const detail = await screen.findByRole("region", { name: "incident-current" });
    await user.click(within(detail).getByRole("button", { name: /Load full event history/ }));
    expect(await within(detail).findByRole("alert")).toBeInTheDocument();

    const serverSource = screen.getByText("Server source SHA").parentElement;
    const executionSource = within(detail).getByText("Execution source SHA").parentElement;
    expect(serverSource).toHaveTextContent(SOURCE_SHA);
    expect(executionSource).toHaveTextContent(sourceSha ?? "Unavailable");
    expect(executionSource).not.toHaveTextContent(SOURCE_SHA);
  });

  it("loads an older execution's evidence without assigning the running server's source", async () => {
    const oldExecution = execution({ sourceSha: "1".repeat(40), state: "completed", phase: "finished" });
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response({
        ...statusFixture({ executions: [oldExecution], pending: [] }),
        source_sha: "2".repeat(40),
      }))
      .mockResolvedValueOnce(response(evidenceForExecution(oldExecution))));
    const user = userEvent.setup();
    render(<OperationsPage />);
    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await user.click(await screen.findByRole("button", { name: /incident-current/ }));
    const detail = await screen.findByRole("region", { name: "incident-current" });
    await user.click(within(detail).getByRole("button", { name: /Load full event history/ }));
    expect(await within(detail).findByText("verification_completed")).toBeInTheDocument();
    expect(within(detail).getByText(/Source SHA: 1111111111111111111111111111111111111111/)).toBeInTheDocument();
    expect(within(detail).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not present an unknown-source record as verified recovery", async () => {
    const unknown = execution({
      sourceSha: null, state: "completed", phase: "finished",
      record: {
        resolved: true, env_resolved: true,
        verification: { verification_status: "passed", env_resolved: true },
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(statusFixture({
      executions: [unknown], pending: [],
    }))));
    const user = userEvent.setup();
    render(<OperationsPage />);
    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await user.click(await screen.findByRole("button", { name: /incident-current/ }));
    const detail = await screen.findByRole("region", { name: "incident-current" });
    expect(await within(detail).findByText("Source identity unavailable; verification unconfirmed")).toBeInTheDocument();
    expect(screen.queryByText("Resolved and verified")).not.toBeInTheDocument();
  });

  it("disables expired approval decisions", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(statusFixture({
      pending: [pendingApproval({ expiresAt: Math.floor(Date.now() / 1000) - 1 })],
    })));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<OperationsPage />);

    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));

    expect(await screen.findByText("Expired")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve exact action for incident-current" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject action for incident-current" })).toBeDisabled();
  });

  it("clears approval controls after an unauthorized refresh", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(statusFixture()))
      .mockResolvedValueOnce(response({ detail: "unauthorized" }, 401));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<OperationsPage />);

    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await screen.findByRole("button", { name: "Approve exact action for incident-current" });
    await user.click(screen.getByRole("button", { name: "Refresh operations" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/key rejected/i);
    expect(screen.queryByRole("button", { name: "Approve exact action for incident-current" })).not.toBeInTheDocument();
  });

  it("does not let a late response for an old key restore live controls", async () => {
    const firstResponse = deferred<ReturnType<typeof response>>();
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const key = new Headers(init?.headers).get("X-AtlasOps-Key");
      if (key === TEST_KEY) return firstResponse.promise;
      return Promise.resolve(response(statusFixture({
        executions: [execution({ incidentId: "incident-new-key" })],
      })));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<OperationsPage />);

    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await user.clear(screen.getByLabelText("Operator API key"));
    await user.type(screen.getByLabelText("Operator API key"), SECOND_KEY);
    await act(async () => {
      firstResponse.resolve(response(statusFixture({
        executions: [execution({ incidentId: "incident-old-key" })],
      })));
    });

    expect(screen.getByText("Disconnected")).toBeInTheDocument();
    expect(screen.queryByText("incident-old-key")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("incident-new-key")).toBeInTheDocument();
  });

  it("does not let a late 404 for an old operator key invalidate the current key's execution", async () => {
    const oldEvidence = deferred<ReturnType<typeof response>>();
    const verifiedExecution = (incidentId: string) => execution({
      incidentId,
      state: "completed",
      phase: "finished",
      record: {
        resolved: true,
        env_resolved: true,
        verification: { verification_status: "passed", env_resolved: true },
      },
    });
    const statusFor = (incidentId: string) => statusFixture({
      executions: [verifiedExecution(incidentId)],
      pending: [],
    });
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const key = new Headers(init?.headers).get("X-AtlasOps-Key");
      if (url === "/api/operations" && key === TEST_KEY) {
        return Promise.resolve(response(statusFor("incident-old-key")));
      }
      if (url.endsWith(`/api/operations/${EXECUTION_ID}/evidence`) && key === TEST_KEY) {
        return oldEvidence.promise;
      }
      if (url === "/api/operations" && key === SECOND_KEY) {
        return Promise.resolve(response(statusFor("incident-new-key")));
      }
      return Promise.resolve(response({ detail: "Not found" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<OperationsPage />);

    const keyField = screen.getByLabelText("Operator API key");
    await user.type(keyField, TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await user.click(await screen.findByRole("button", { name: /incident-old-key/ }));
    await user.click(screen.getByRole("button", { name: /Load full event history/ }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url, init]) =>
        String(url).endsWith(`/api/operations/${EXECUTION_ID}/evidence`) &&
        new Headers(init?.headers).get("X-AtlasOps-Key") === TEST_KEY,
      )).toBe(true);
    });

    await user.clear(keyField);
    await user.type(keyField, SECOND_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await user.click(await screen.findByRole("button", { name: /incident-new-key/ }));
    const detail = screen.getByRole("region", { name: "incident-new-key" });
    expect(within(detail).getByText("Resolved and verified")).toBeInTheDocument();

    await act(async () => {
      oldEvidence.resolve(response({ detail: "Evidence record not found" }, 404));
    });

    expect(within(detail).getByText("Resolved and verified")).toBeInTheDocument();
    expect(screen.queryByText("Evidence unavailable; verification unconfirmed")).not.toBeInTheDocument();
  });

  it("ignores event evidence that returns after the user selects another execution", async () => {
    const oldEvidence = deferred<ReturnType<typeof response>>();
    const verifiedOtherExecution = execution({
      executionId: SECOND_EXECUTION_ID,
      incidentId: "incident-preserved",
      state: "completed",
      phase: "finished",
      record: {
        resolved: true,
        env_resolved: true,
        verification: { verification_status: "passed", env_resolved: true },
      },
    });
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/api/operations/${EXECUTION_ID}/evidence`)) return oldEvidence.promise;
      if (url === `/api/operations/${SECOND_EXECUTION_ID}/evidence`) {
        return Promise.resolve(response(evidenceFixture(SECOND_EXECUTION_ID, "new-selection-event")));
      }
      return Promise.resolve(response(statusFixture({
        executions: [
          execution(),
          verifiedOtherExecution,
        ],
        pending: [],
      })));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<OperationsPage />);

    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await user.click(await screen.findByRole("button", { name: /incident-current/ }));
    await user.click(screen.getByRole("button", { name: /Load full event history/ }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith(`/api/operations/${EXECUTION_ID}/evidence`))).toBe(true);
    });

    await user.click(screen.getByRole("button", { name: /incident-preserved/ }));
    await act(async () => {
      oldEvidence.resolve(response({ detail: "Evidence record not found" }, 404));
    });

    expect(screen.queryByText("old-event-from-prior-selection")).not.toBeInTheDocument();
    const detail = screen.getByRole("region", { name: "incident-preserved" });
    expect(detail).toBeInTheDocument();
    expect(within(detail).getByText("Resolved and verified")).toBeInTheDocument();
    expect(screen.queryByText("Evidence unavailable; verification unconfirmed")).not.toBeInTheDocument();
  });
});
