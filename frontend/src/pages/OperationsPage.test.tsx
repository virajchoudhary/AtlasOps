import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OperationsPage } from "./OperationsPage";

const TEST_KEY = "fixture-only-operator-key";
const SECOND_KEY = "second-fixture-operator-key";
const EXECUTION_ID = "a".repeat(32);
const SECOND_EXECUTION_ID = "b".repeat(32);
const ACTION_DIGEST = "f".repeat(64);

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
  record = {},
}: {
  executionId?: string;
  incidentId?: string;
  state?: string;
  phase?: string;
  record?: Record<string, unknown>;
} = {}) {
  return {
    incident_id: incidentId,
    execution_id: executionId,
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
}: {
  expiresAt?: number;
  actionDigest?: string;
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
    action: {
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
    source_sha: "fixture-source-sha",
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
    source_sha: "fixture-source-sha",
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
    audit_integrity: integrity(),
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
});

describe("Operations page", () => {
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

  it("requires a rejection reason and submits the selected decision", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/operations/decision") {
        return Promise.resolve(response({ ok: true, decision: "rejected" }));
      }
      return Promise.resolve(response(statusFixture()));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<OperationsPage />);

    await user.type(screen.getByLabelText("Operator API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    const rejectButton = await screen.findByRole("button", { name: "Reject action for incident-current" });
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

  it("ignores event evidence that returns after the user selects another execution", async () => {
    const oldEvidence = deferred<ReturnType<typeof response>>();
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/api/operations/${EXECUTION_ID}/evidence`)) return oldEvidence.promise;
      if (url === `/api/operations/${SECOND_EXECUTION_ID}/evidence`) {
        return Promise.resolve(response(evidenceFixture(SECOND_EXECUTION_ID, "new-selection-event")));
      }
      return Promise.resolve(response(statusFixture({
        executions: [
          execution(),
          execution({
            executionId: SECOND_EXECUTION_ID,
            incidentId: "incident-preserved",
            state: "interrupted",
            phase: "finished",
          }),
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
      oldEvidence.resolve(response(evidenceFixture(EXECUTION_ID)));
    });

    expect(screen.queryByText("old-event-from-prior-selection")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "incident-preserved" })).toBeInTheDocument();
  });
});
