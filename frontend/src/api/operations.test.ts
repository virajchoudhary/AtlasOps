import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchOperationsEvidence,
  fetchOperationsStatus,
  pauseOperations,
  submitOperationsDecision,
} from "./operations";

const TEST_KEY = "fixture-only-operator-key";
const EXECUTION_ID = "a".repeat(32);
const ACTION_DIGEST = "f".repeat(64);
const SOURCE_SHA = "a".repeat(40);

function response(payload: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
  };
}

function rawResponse(text: string, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => text,
  };
}

function integrity(ok = true) {
  return { ok, events: 3, last_hash: "b".repeat(64) };
}

function operationExecution(
  extras: Record<string, unknown> & { record?: Record<string, unknown> } = {},
) {
  const { record: recordOverrides = {}, ...metadata } = extras;
  return {
    incident_id: "incident-checkout",
    execution_id: EXECUTION_ID,
    source_sha: SOURCE_SHA,
    phase: "remediation",
    state: "running",
    updated_at: 1_800_000_000,
    record: {
      resolved: false,
      env_resolved: null,
      verification: { verification_status: "unknown", env_resolved: null },
      triage: { final: { severity: "P1" }, trajectory: ["received"] },
      replay_allowed: false,
      ...recordOverrides,
    },
    integrity: integrity(),
    ...metadata,
  };
}

function pendingApproval(extras: Record<string, unknown> = {}) {
  return {
    incident_id: "incident-checkout",
    severity: "P1",
    summary: "Checkout workload requires an exact change.",
    token: "apr-fixture-token",
    created_at: 1_800_000_000,
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
    action_digest: ACTION_DIGEST,
    action_summary: "kubectl_scale arguments={...}",
    expires_at: 1_800_000_300,
    ...extras,
  };
}

function operationsStatus(extras: Record<string, unknown> = {}) {
  return {
    enabled: true,
    source_sha: SOURCE_SHA,
    certification_status: "NOT_CERTIFIED",
    operator_id: "operator-fixture",
    approval_policy: "exact-action-all-mutations",
    executions: [operationExecution()],
    pending: [pendingApproval()],
    circuit_breaker: {
      tripped: false,
      active_incidents: 1,
      consecutive_failures: 0,
      cluster_mutating_actions_this_hour: 1,
    },
    ...extras,
  };
}

function operationEvidence() {
  return {
    ...operationExecution(),
    source_sha: SOURCE_SHA,
    certification_status: "NOT_CERTIFIED",
    events: [
      {
        execution_id: EXECUTION_ID,
        event: "admitted",
        phase: "queued",
        state: "running",
        record: { incident_id: "incident-checkout", evidence_class: "OPERATIONAL_UNCERTIFIED" },
        ts: 1_800_000_000,
        audit_entry_hash: "c".repeat(64),
      },
      {
        execution_id: EXECUTION_ID,
        event: "verification_completed",
        phase: "verification",
        state: "running",
        record: {
          verification: { verification_status: "failed", env_resolved: false },
          resolved: false,
        },
        ts: 1_800_000_002,
        audit_entry_hash: "d".repeat(64),
      },
    ],
    audit_integrity: { ok: true, entries: 3 },
    audit_scope: "last_1000_global_records",
    audit: [{ action_type: "execution_progress" }],
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("live operations API", () => {
  it.each([SOURCE_SHA, null])("preserves an execution's own source identity %s", async (sourceSha) => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(operationsStatus({
        source_sha: "2".repeat(40),
        executions: [operationExecution({ source_sha: sourceSha })],
      })))
      .mockResolvedValueOnce(response({ ...operationEvidence(), source_sha: sourceSha })));

    const status = await fetchOperationsStatus(TEST_KEY);
    expect(status.source_sha).toBe("2".repeat(40));
    expect(status.executions[0]).toHaveProperty("source_sha", sourceSha);
    const execution = status.executions[0];
    if (!execution) throw new Error("Fixture must contain an execution.");
    const bundle = await fetchOperationsEvidence({
      apiKey: TEST_KEY, executionId: execution.execution_id,
    });
    expect(bundle.evidence.source_sha).toBe(sourceSha);
  });

  it("accepts the actual backend audit-entry receipt without inventing an event hash", async () => {
    const payload = {
      ...operationEvidence(),
      audit_integrity: { ok: true, entries: 3 },
    };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(operationsStatus()))
      .mockResolvedValueOnce(response(payload)));
    const execution = (await fetchOperationsStatus(TEST_KEY)).executions[0];
    if (!execution) throw new Error("Expected a fixture execution.");
    const bundle = await fetchOperationsEvidence({
      apiKey: TEST_KEY, executionId: execution.execution_id,
    });
    expect(bundle.evidence.audit_integrity).toEqual({ ok: true, entries: 3 });
  });

  it("reads a bounded authenticated status without putting the key in a URL or cookie", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(operationsStatus()));
    vi.stubGlobal("fetch", fetchMock);

    const status = await fetchOperationsStatus(TEST_KEY);

    expect(status.enabled).toBe(true);
    expect(status.certification_status).toBe("NOT_CERTIFIED");
    expect(status.pending[0]?.action.target.resource_version).toBe("17");
    expect(status.executions[0]?.record.replay_allowed).toBe(false);
    const call = fetchMock.mock.calls[0];
    if (!call) throw new Error("Expected one authenticated status read.");
    const [url, options] = call;
    expect(url).toBe("/api/operations");
    expect(String(url)).not.toContain(TEST_KEY);
    expect(options?.method).toBe("GET");
    expect(options?.credentials).toBe("omit");
    expect(options?.cache).toBe("no-store");
    const headers = new Headers(options?.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("X-AtlasOps-Key")).toBe(TEST_KEY);
    expect(headers.get("Cookie")).toBeNull();
  });

  it("preserves and validates a distinct optional verification target", async () => {
    const action = {
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
      verification_target: {
        server: "https://cluster.fixture.invalid",
        context: "kind-atlasops-local",
        api_version: "apps/v1",
        kind: "Deployment",
        namespace: "default",
        name: "checkoutservice",
        uid: "uid-workload-fixture",
        resource_version: "23",
      },
      patch: {
        apiVersion: "v1",
        kind: "DeleteOptions",
        preconditions: { uid: "uid-chaos-fixture", resourceVersion: "17" },
      },
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(operationsStatus({
        pending: [pendingApproval({ action })],
      })))
      .mockResolvedValueOnce(response(operationsStatus({
        pending: [pendingApproval({
          action: {
            ...action,
            verification_target: { ...action.verification_target, uid: "" },
          },
        })],
      })));
    vi.stubGlobal("fetch", fetchMock);

    const status = await fetchOperationsStatus(TEST_KEY);
    expect(status.pending[0]?.action).toMatchObject({
      verification_target: action.verification_target,
    });
    await expect(fetchOperationsStatus(TEST_KEY)).rejects.toMatchObject({
      kind: "malformed_response",
    });
  });

  it("rejects unknown execution states and malformed action identity before exposing controls", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response(operationsStatus({
        executions: [operationExecution({ state: "unknown" })],
      })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchOperationsStatus(TEST_KEY)).rejects.toMatchObject({
      kind: "malformed_response",
    });

    fetchMock.mockResolvedValueOnce(response(operationsStatus({
      pending: [pendingApproval({
        action: { tool: "kubectl_scale", arguments: { replicas: 3 }, patch: [] },
      })],
    })));
    await expect(fetchOperationsStatus(TEST_KEY)).rejects.toMatchObject({
      kind: "malformed_response",
    });
  });

  it("distinguishes wrong key, missing operational configuration, and malformed JSON", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ detail: "unauthorized" }, 401))
      .mockResolvedValueOnce(response({ detail: "Operational controls are not enabled" }, 503))
      .mockResolvedValueOnce(rawResponse("{not-json"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchOperationsStatus(TEST_KEY)).rejects.toMatchObject({
      kind: "unauthorized",
      status: 401,
    });
    await expect(fetchOperationsStatus(TEST_KEY)).rejects.toMatchObject({
      kind: "configuration_unavailable",
      status: 503,
    });
    await expect(fetchOperationsStatus(TEST_KEY)).rejects.toMatchObject({
      kind: "malformed_response",
    });
  });

  it("classifies expired and action-changed decisions as stale", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(operationsStatus()))
      .mockResolvedValueOnce(response({ ok: false, error: "action_digest_mismatch" }, 400))
      .mockResolvedValueOnce(response({ ok: false, error: "expired" }, 400));
    vi.stubGlobal("fetch", fetchMock);

    const status = await fetchOperationsStatus(TEST_KEY);
    const pending = status.pending[0];
    if (!pending) throw new Error("Fixture must contain one pending action.");

    await expect(submitOperationsDecision({
      apiKey: TEST_KEY,
      token: pending.token,
      actionDigest: pending.action_digest,
      decision: "approved",
    })).rejects.toMatchObject({
      kind: "stale_action",
      status: 400,
      detail: "action_digest_mismatch",
    });
    await expect(submitOperationsDecision({
      apiKey: TEST_KEY,
      token: pending.token,
      actionDigest: pending.action_digest,
      decision: "rejected",
      reason: "The approval expired before submission.",
    })).rejects.toMatchObject({
      kind: "stale_action",
      status: 400,
      detail: "expired",
    });
  });

  it("submits only the displayed token, exact digest, decision, and rejection reason", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(operationsStatus()))
      .mockResolvedValueOnce(response({ ok: true, decision: "rejected" }));
    vi.stubGlobal("fetch", fetchMock);

    const status = await fetchOperationsStatus(TEST_KEY);
    const pending = status.pending[0];
    if (!pending) throw new Error("Fixture must contain one pending action.");
    await submitOperationsDecision({
      apiKey: TEST_KEY,
      token: pending.token,
      actionDigest: pending.action_digest,
      decision: "rejected",
      reason: "Target identity does not match the incident.",
    });

    const call = fetchMock.mock.calls[1];
    if (!call) throw new Error("Expected one decision submission.");
    const [url, options] = call;
    expect(url).toBe("/api/operations/decision");
    expect(options?.method).toBe("POST");
    expect(options?.credentials).toBe("omit");
    const headers = new Headers(options?.headers);
    expect(headers.get("X-AtlasOps-Key")).toBe(TEST_KEY);
    expect(JSON.parse(String(options?.body))).toEqual({
      token: pending.token,
      action_digest: pending.action_digest,
      decision: "rejected",
      reason: "Target identity does not match the incident.",
    });
  });

  it("reads ordered evidence through the authenticated API and parses object-shaped patches", async () => {
    const objectPatch = {
      apiVersion: "v1",
      kind: "DeleteOptions",
      preconditions: { uid: "uid-fixture", resourceVersion: "17" },
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(operationsStatus({
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
              uid: "uid-fixture",
              resource_version: "17",
            },
            patch: objectPatch,
          },
        })],
      })))
      .mockResolvedValueOnce(response(operationEvidence()));
    vi.stubGlobal("fetch", fetchMock);

    const status = await fetchOperationsStatus(TEST_KEY);
    const pending = status.pending[0];
    const execution = status.executions[0];
    if (!pending || !execution) throw new Error("Fixture status is incomplete.");
    expect(pending.action.patch).toEqual(objectPatch);

    const bundle = await fetchOperationsEvidence({
      apiKey: TEST_KEY,
      executionId: execution.execution_id,
    });

    expect(bundle.filename).toBe(`atlasops-${EXECUTION_ID}.json`);
    expect(bundle.content).toContain('"audit_scope":"last_1000_global_records"');
    expect(bundle.evidence.events.map((event) => event.event)).toEqual([
      "admitted",
      "verification_completed",
    ]);
    const call = fetchMock.mock.calls[1];
    if (!call) throw new Error("Expected one evidence read.");
    const [url, options] = call;
    expect(url).toBe(`/api/operations/${EXECUTION_ID}/evidence`);
    expect(options?.method).toBe("GET");
    expect(new Headers(options?.headers).get("X-AtlasOps-Key")).toBe(TEST_KEY);
  });

  it("classifies a missing full-evidence record separately from other HTTP errors", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(operationsStatus()))
      .mockResolvedValueOnce(response({ detail: "Evidence record not found" }, 404));
    vi.stubGlobal("fetch", fetchMock);
    const status = await fetchOperationsStatus(TEST_KEY);
    const execution = status.executions[0];
    if (!execution) throw new Error("Fixture must contain one execution.");

    await expect(fetchOperationsEvidence({
      apiKey: TEST_KEY,
      executionId: execution.execution_id,
    })).rejects.toMatchObject({
      kind: "evidence_not_found",
      status: 404,
    });
  });

  it.each([
    ["empty source SHA", operationsStatus({ source_sha: "" })],
    ["wrong-length source SHA", operationsStatus({ source_sha: "a".repeat(39) })],
    ["non-hex source SHA", operationsStatus({ source_sha: "g".repeat(40) })],
    [
      "empty execution integrity hash",
      operationsStatus({
        executions: [operationExecution({
          integrity: { ok: true, events: 3, last_hash: "" },
        })],
      }),
    ],
    [
      "wrong-length execution integrity hash",
      operationsStatus({
        executions: [operationExecution({
          integrity: { ok: true, events: 3, last_hash: "b".repeat(63) },
        })],
      }),
    ],
    [
      "non-hex execution integrity hash",
      operationsStatus({
        executions: [operationExecution({
          integrity: { ok: true, events: 3, last_hash: "z".repeat(64) },
        })],
      }),
    ],
  ])("rejects %s before returning a status snapshot", async (_case, payload) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));

    await expect(fetchOperationsStatus(TEST_KEY)).rejects.toMatchObject({
      kind: "malformed_response",
    });
  });

  it.each([
    ["empty evidence source SHA", { ...operationEvidence(), source_sha: "" }],
    [
      "malformed evidence source SHA",
      { ...operationEvidence(), source_sha: "g".repeat(40) },
    ],
    [
      "audit receipt missing its entry count",
      {
        ...operationEvidence(),
        audit_integrity: { ok: true, events: 3, last_hash: "" },
      },
    ],
    [
      "execution-shaped negative audit receipt",
      {
        ...operationEvidence(),
        audit_integrity: { ok: false, events: 3, last_hash: "z".repeat(64) },
      },
    ],
    ["empty positive audit", { ...operationEvidence(), audit_integrity: { ok: true, entries: 0 } }],
    ["negative audit count", { ...operationEvidence(), audit_integrity: { ok: true, entries: -1 } }],
    ["fractional audit count", { ...operationEvidence(), audit_integrity: { ok: true, entries: 1.5 } }],
    ["string audit count", { ...operationEvidence(), audit_integrity: { ok: true, entries: "3" } }],
  ])("rejects %s before returning full evidence", async (_case, payload) => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(operationsStatus()))
      .mockResolvedValueOnce(response(payload));
    vi.stubGlobal("fetch", fetchMock);
    const status = await fetchOperationsStatus(TEST_KEY);
    const execution = status.executions[0];
    if (!execution) throw new Error("Fixture must contain one execution.");

    await expect(fetchOperationsEvidence({
      apiKey: TEST_KEY,
      executionId: execution.execution_id,
    })).rejects.toMatchObject({
      kind: "malformed_response",
    });
  });

  it("preserves valid negative integrity states", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(operationsStatus({
        executions: [operationExecution({ integrity: integrity(false) })],
      })))
      .mockResolvedValueOnce(response({
        ...operationEvidence(),
        integrity: integrity(false),
        audit_integrity: { ok: false, entries: 3 },
      }));
    vi.stubGlobal("fetch", fetchMock);

    const status = await fetchOperationsStatus(TEST_KEY);
    const execution = status.executions[0];
    if (!execution) throw new Error("Fixture must contain one execution.");
    expect(execution.integrity.ok).toBe(false);

    const evidence = await fetchOperationsEvidence({
      apiKey: TEST_KEY,
      executionId: execution.execution_id,
    });
    expect(evidence.evidence.integrity.ok).toBe(false);
    expect(evidence.evidence.audit_integrity.ok).toBe(false);
  });

  it("accepts the server-confirmed pause state", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({
      tripped: true,
      active_incidents: 1,
    }));
    vi.stubGlobal("fetch", fetchMock);

    const status = await pauseOperations({ apiKey: TEST_KEY });

    expect(status.tripped).toBe(true);
    const call = fetchMock.mock.calls[0];
    if (!call) throw new Error("Expected one pause request.");
    const [url, options] = call;
    expect(url).toBe("/api/operations/pause");
    expect(options?.method).toBe("POST");
    expect(options?.body).toBeUndefined();
    expect(new Headers(options?.headers).get("X-AtlasOps-Key")).toBe(TEST_KEY);
  });
});
