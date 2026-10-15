import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchIncidentHistoryDetail,
  fetchIncidentHistoryPage,
} from "./incident-history";
import type { IncidentHistorySummary } from "./types";

const TEST_KEY = "fixture-only-operator-key";
const EXECUTION_ID = "a".repeat(32);

function response(payload: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
  };
}

function canonicalSummary(): IncidentHistorySummary {
  return {
    incident_id: "incident-legacy",
    execution_id: null,
    record_type: "canonical",
    severity: "P2",
    title: "Synthetic persisted incident",
    status: "recorded",
    resolved: true,
    env_resolved: null,
    verification_status: "unknown",
    summary: "Stored summary",
  };
}

function detailRecord(
  record: IncidentHistorySummary,
  extras: Record<string, unknown> = {},
) {
  return {
    incident_id: record.incident_id,
    execution_id: record.execution_id,
    record_type: record.record_type,
    triage: null,
    diagnosis: null,
    remediation: null,
    approval: null,
    verification: null,
    comms: null,
    ...extras,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("persisted incident history API", () => {
  it("uses only the bounded same-origin GET and keeps the key out of the URL and cookies", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({
        items: [
          {
            incident_id: "incident-legacy",
            execution_id: null,
            record_type: "canonical",
            severity: "P2",
            title: "Synthetic persisted incident",
            status: "recorded",
            resolved: true,
            env_resolved: null,
            verification_status: "unknown",
            summary: "Stored summary",
            internal_note: "This field is not in the public projection.",
          },
        ],
        total: 1,
        limit: 25,
        offset: 0,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const page = await fetchIncidentHistoryPage({
      apiKey: TEST_KEY,
      limit: 25,
      offset: 0,
    });

    expect(page.items[0]?.record_type).toBe("canonical");
    expect(page.items[0]?.execution_id).toBeNull();
    expect("internal_note" in (page.items[0] ?? {})).toBe(false);
    const call = fetchMock.mock.calls[0];
    if (!call) throw new Error("Expected one protected history request.");
    const [url, options] = call;
    expect(url).toBe("/incidents/history?limit=25&offset=0");
    expect(String(url)).not.toContain(TEST_KEY);
    expect(options?.method).toBe("GET");
    expect(options?.credentials).toBe("omit");
    const headers = new Headers(options?.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("X-AtlasOps-Key")).toBe(TEST_KEY);
    expect(headers.get("Authorization")).toBeNull();
    expect(headers.get("Cookie")).toBeNull();
  });

  it("reads a canonical record without inventing an execution identifier", async () => {
    const record = canonicalSummary();
    const fetchMock = vi.fn().mockResolvedValue(response(detailRecord(record)));
    vi.stubGlobal("fetch", fetchMock);

    const detail = await fetchIncidentHistoryDetail({ apiKey: TEST_KEY, record });

    expect(detail.record_type).toBe("canonical");
    expect(detail.execution_id).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith(
      "/incidents/incident-legacy",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("requests an execution snapshot by its exact execution identifier", async () => {
    const record: IncidentHistorySummary = {
      ...canonicalSummary(),
      incident_id: "incident-current",
      execution_id: EXECUTION_ID,
      record_type: "execution",
    };
    const fetchMock = vi.fn().mockResolvedValue(response(detailRecord(record)));
    vi.stubGlobal("fetch", fetchMock);

    const detail = await fetchIncidentHistoryDetail({ apiKey: TEST_KEY, record });

    expect(detail.record_type).toBe("execution");
    expect(detail.execution_id).toBe(EXECUTION_ID);
    expect(fetchMock).toHaveBeenCalledWith(
      `/incidents/incident-current?execution_id=${EXECUTION_ID}`,
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("rejects a detail response for a different persisted identity", async () => {
    const record = canonicalSummary();
    const fetchMock = vi.fn().mockResolvedValue(
      response(detailRecord({ ...record, incident_id: "other-incident" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchIncidentHistoryDetail({ apiKey: TEST_KEY, record })).rejects.toMatchObject({
      kind: "malformed_response",
    });
  });

  it("drops unapproved, credential, and path fields from the safe detail projection", async () => {
    const record = canonicalSummary();
    const fetchMock = vi.fn().mockResolvedValue(
      response(
        detailRecord(record, {
          remediation: {
            proposed_actions: [
              {
                step: "restart",
                tool: "service_restart",
                args: {
                  service: "payments",
                  authorization: "Basic fixture-placeholder-value",
                  cookie: "session=fixture-only",
                  target_path: "docs/private/fixture.json",
                  note: "Authorization: Basic fixture-placeholder-value",
                },
                result: "Recorded from C:\\Private\\fixture.json",
                status: "reported",
                unapproved_field: "not projected",
              },
            ],
          },
          comms: {
            postmortem_path: "docs/postmortems/fixture.md",
            summary: "<img src=x onerror=alert(1)> C:\\Private\\fixture.json",
          },
          unapproved_field: "not projected",
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const detail = await fetchIncidentHistoryDetail({ apiKey: TEST_KEY, record });

    expect("unapproved_field" in detail).toBe(false);
    expect(detail.comms).toEqual({
      postmortem_reference_recorded: true,
      summary: "<img src=x onerror=alert(1)> [PATH]",
    });
    expect(detail.remediation?.proposed_actions).toEqual([
      {
        step: "restart",
        tool: "service_restart",
        args: {
          service: "payments",
          note: "[REDACTED]",
        },
        result: "Recorded from [PATH]",
        status: "reported",
      },
    ]);
  });

  it("distinguishes authentication, missing server configuration, unavailable history, and not-found errors", async () => {
    const record = canonicalSummary();
    const unauthorizedFetch = vi.fn().mockResolvedValue(response({ detail: "unauthorized" }, 401));
    vi.stubGlobal("fetch", unauthorizedFetch);
    await expect(
      fetchIncidentHistoryPage({ apiKey: TEST_KEY, limit: 25, offset: 0 }),
    ).rejects.toMatchObject({ kind: "unauthorized", status: 401 });

    const configurationFetch = vi.fn().mockResolvedValue(
      response({ detail: "ATLASOPS_API_KEY is required for this endpoint" }, 503),
    );
    vi.stubGlobal("fetch", configurationFetch);
    await expect(
      fetchIncidentHistoryPage({ apiKey: TEST_KEY, limit: 25, offset: 0 }),
    ).rejects.toMatchObject({ kind: "configuration_unavailable", status: 503 });

    const historyFetch = vi.fn().mockResolvedValue(
      response({ detail: "Incident history is unavailable" }, 503),
    );
    vi.stubGlobal("fetch", historyFetch);
    await expect(
      fetchIncidentHistoryPage({ apiKey: TEST_KEY, limit: 25, offset: 0 }),
    ).rejects.toMatchObject({ kind: "service_unavailable", status: 503 });

    const notFoundFetch = vi.fn().mockResolvedValue(response({ detail: "not found" }, 404));
    vi.stubGlobal("fetch", notFoundFetch);
    await expect(
      fetchIncidentHistoryDetail({ apiKey: TEST_KEY, record }),
    ).rejects.toMatchObject({ kind: "not_found", status: 404 });
  });

  it("treats malformed JSON and unsupported pagination as explicit failures", async () => {
    const malformedFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => "{not-json",
    });
    vi.stubGlobal("fetch", malformedFetch);
    await expect(
      fetchIncidentHistoryPage({ apiKey: TEST_KEY, limit: 25, offset: 0 }),
    ).rejects.toMatchObject({ kind: "malformed_response" });

    const invalidPageFetch = vi.fn();
    vi.stubGlobal("fetch", invalidPageFetch);
    await expect(
      fetchIncidentHistoryPage({ apiKey: TEST_KEY, limit: 101, offset: 0 }),
    ).rejects.toMatchObject({ kind: "invalid_request" });
    expect(invalidPageFetch).not.toHaveBeenCalled();
  });
});
