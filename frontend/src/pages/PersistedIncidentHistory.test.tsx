import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  IncidentHistoryDetail,
  IncidentHistoryPage,
  IncidentHistorySummary,
} from "../api/types";
import { PersistedIncidentHistory } from "./PersistedIncidentHistory";

const TEST_KEY = "fixture-only-operator-key";
const EXECUTION_ID = "b".repeat(32);

const legacyRecord = {
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
} satisfies IncidentHistorySummary;

const executionRecord = {
  ...legacyRecord,
  incident_id: "incident-next",
  execution_id: EXECUTION_ID,
  record_type: "execution",
} satisfies IncidentHistorySummary;

const legacyDetail = {
  incident_id: legacyRecord.incident_id,
  execution_id: null,
  record_type: "canonical",
  resolved: true,
  agent_claimed_resolved: true,
  env_resolved: null,
  triage: {
    severity: "P2",
    title: "<img src=x onerror=alert(1)>",
    blast_radius: { services: ["payments"], namespaces: ["checkout"] },
    affected_services: ["payments"],
    correlated_alerts: [],
    handoff_notes: "Synthetic handoff note",
  },
  diagnosis: {
    root_cause: "Synthetic diagnosis",
    category: "service",
    confidence: 0.72,
    blast_radius_update: null,
    recommended_fix: "Review the persisted action",
    evidence: [{ tool: "metrics_query" }],
  },
  remediation: {
    mode: "recorded",
    status: "reported",
    outcome: "Reported resolved",
    proposed_actions: [
      {
        step: "restart",
        tool: "service_restart",
        args: { service: "payments" },
        result: "Recorded result",
        status: "reported",
      },
    ],
    executed_actions: [],
    verified_by: "A workflow agent reported verification.",
    summary: "Recorded action summary",
    time_to_resolve_seconds: 18,
    runbook: "paymentservice",
  },
  approval: {
    mode: "required",
    severity: "P2",
    triage_severity: "P2",
    decision: "approved",
    action_digest: "fixture-digest",
    action_summary: "Synthetic approved action",
    approved_by: "fixture-operator",
    reason: "Synthetic test approval",
  },
  verification: {
    scenario_id: "synthetic-scenario",
    agent_claimed_resolved: true,
    env_resolved: null,
    verification_status: "unknown",
    is_false_resolution: null,
    is_false_negative: null,
    failed_checks: [],
    checks: [],
    observed_metrics: {},
    verification_timestamp: "2026-10-15T00:00:00Z",
  },
  comms: {
    status: "recorded",
    slack_posted: false,
    postmortem_path: "docs/postmortems/synthetic.md",
    summary: "Synthetic communication summary",
    summary_for_dashboard: "Synthetic dashboard summary",
    lessons_learned: ["Preserve verification state"],
  },
} satisfies IncidentHistoryDetail;

function response(payload: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
  };
}

function page(
  items: IncidentHistorySummary[],
  offset = 0,
  total = items.length,
): IncidentHistoryPage {
  return { items, total, limit: 25, offset };
}

async function connect(user: ReturnType<typeof userEvent.setup>, key = TEST_KEY) {
  await user.type(screen.getByLabelText("AtlasOps access key"), key);
  await user.click(screen.getByRole("button", { name: "Connect" }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PersistedIncidentHistory", () => {
  it("starts locked without a request and keeps the entered key in the view while reading", async () => {
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const fetchMock = vi.fn().mockResolvedValue(response(page([legacyRecord])));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<PersistedIncidentHistory />);

    expect(screen.getByText("Locked. No persisted history request has been made.")).toBeInTheDocument();
    expect(screen.getByLabelText("AtlasOps access key")).toHaveAttribute("type", "password");
    expect(fetchMock).not.toHaveBeenCalled();

    await connect(user);

    expect(await screen.findByText("incident-legacy")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const call = fetchMock.mock.calls[0];
    if (!call) throw new Error("Expected the user-initiated list request.");
    const [url, options] = call;
    expect(url).toBe("/incidents/history?limit=25&offset=0");
    expect(String(url)).not.toContain(TEST_KEY);
    expect(options?.method).toBe("GET");
    expect(options?.credentials).toBe("omit");
    expect(new Headers(options?.headers).get("X-AtlasOps-Key")).toBe(TEST_KEY);
    expect(new Headers(options?.headers).get("Cookie")).toBeNull();
    expect(screen.getByLabelText("AtlasOps access key")).toHaveValue("");
    expect(window.localStorage.length).toBe(0);
    expect(storageWrite).not.toHaveBeenCalled();
    expect(document.cookie).toBe("");
  });

  it("keeps canonical identity exact and separates record claims from environment verification", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      return Promise.resolve(
        String(input).startsWith("/incidents/history")
          ? response(page([legacyRecord]))
          : response(legacyDetail),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const { container } = render(<PersistedIncidentHistory />);

    await connect(user);
    expect(await screen.findByText("execution_id: null")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Record resolved flag" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Environment resolved" })).toBeInTheDocument();
    expect(screen.getByText("Unknown")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Open canonical record incident-legacy" }),
    );

    const verification = await screen.findByRole("region", {
      name: "Environment verification",
    });
    expect(within(verification).getByText("Agent claimed resolved")).toBeInTheDocument();
    const environmentResult = within(verification).getByText("Environment resolved (recorded)");
    expect(environmentResult).toBeInTheDocument();
    expect(environmentResult.closest("div")).toHaveTextContent("Unknown");
    expect(
      screen.getByText("Agent-reported verified_by text"),
    ).toBeInTheDocument();
    expect(screen.getByText("Recorded resolved flag")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(screen.queryByText(/latest execution/i)).toBeNull();

    const detailCall = fetchMock.mock.calls.find(
      ([url]) => String(url) === "/incidents/incident-legacy",
    );
    if (!detailCall) throw new Error("Expected an exact detail request.");
    expect(detailCall[0]).toBe("/incidents/incident-legacy");
    expect(String(detailCall[0])).not.toContain("execution_id=");
    expect(detailCall[1]?.method).toBe("GET");
  });

  it("reports a rejected key, missing server configuration, empty history, and malformed response", async () => {
    const user = userEvent.setup();

    const unauthorizedFetch = vi.fn().mockResolvedValue(response({ detail: "rejected" }, 401));
    vi.stubGlobal("fetch", unauthorizedFetch);
    const unauthorizedView = render(<PersistedIncidentHistory />);
    await connect(user);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The entered access key was rejected (HTTP 401).",
    );
    unauthorizedView.unmount();

    const configurationFetch = vi.fn().mockResolvedValue(
      response({ detail: "ATLASOPS_API_KEY is required for this endpoint" }, 503),
    );
    vi.stubGlobal("fetch", configurationFetch);
    const configurationView = render(<PersistedIncidentHistory />);
    await connect(user);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The service has no configured operator API key (HTTP 503).",
    );
    configurationView.unmount();

    const emptyFetch = vi.fn().mockResolvedValue(response(page([])));
    vi.stubGlobal("fetch", emptyFetch);
    const emptyView = render(<PersistedIncidentHistory />);
    await connect(user);
    expect(await screen.findByText("No persisted workflow records were found.")).toBeInTheDocument();
    emptyView.unmount();

    const malformedFetch = vi.fn().mockResolvedValue(response({ items: "not-an-array" }));
    vi.stubGlobal("fetch", malformedFetch);
    render(<PersistedIncidentHistory />);
    await connect(user);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The history response did not match the supported safe record shape.",
    );
  });

  it("reports an exact-record 404 without selecting a substitute", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) =>
      Promise.resolve(
        String(input).startsWith("/incidents/history")
          ? response(page([legacyRecord]))
          : response({ detail: "missing" }, 404),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<PersistedIncidentHistory />);

    await connect(user);
    await user.click(
      screen.getByRole("button", { name: "Open canonical record incident-legacy" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This exact persisted record was not found (HTTP 404). No substitute record was selected.",
    );
  });

  it("aborts and ignores a pending list response when the user clears access", async () => {
    let resolvePending: (value: ReturnType<typeof response>) => void = () => {};
    const pending = new Promise<ReturnType<typeof response>>((resolve) => {
      resolvePending = resolve;
    });
    const fetchMock = vi.fn().mockReturnValue(pending);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<PersistedIncidentHistory />);

    await connect(user);
    const call = fetchMock.mock.calls[0];
    if (!call) throw new Error("Expected a pending list request.");
    const signal = call[1]?.signal;
    if (!signal) throw new Error("Expected the history request to be abortable.");

    await user.click(screen.getByRole("button", { name: "Clear key and history" }));
    expect(signal.aborted).toBe(true);
    resolvePending(response(page([legacyRecord])));

    expect(await screen.findByText("Locked. No persisted history request has been made.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("incident-legacy")).toBeNull());
  });

  it("aborts an in-flight detail request on key change and component unmount", async () => {
    let resolveDetail: (value: ReturnType<typeof response>) => void = () => {};
    const detailPending = new Promise<ReturnType<typeof response>>((resolve) => {
      resolveDetail = resolve;
    });
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) =>
      Promise.resolve(
        String(input).startsWith("/incidents/history")
          ? response(page([legacyRecord]))
          : detailPending,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const view = render(<PersistedIncidentHistory />);

    await connect(user);
    await user.click(
      screen.getByRole("button", { name: "Open canonical record incident-legacy" }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const detailCall = fetchMock.mock.calls[1];
    if (!detailCall?.[1]?.signal) throw new Error("Expected an abortable detail request.");
    const detailSignal = detailCall[1].signal;

    await user.type(screen.getByLabelText("AtlasOps access key"), "replacement-key");
    expect(detailSignal.aborted).toBe(true);
    expect(screen.getByText("Locked. No persisted history request has been made.")).toBeInTheDocument();
    resolveDetail(response(legacyDetail));

    view.unmount();
    const unmountPending = new Promise<ReturnType<typeof response>>(() => {});
    const unmountFetch = vi.fn().mockReturnValue(unmountPending);
    vi.stubGlobal("fetch", unmountFetch);
    const unmountView = render(<PersistedIncidentHistory />);
    await connect(user);
    const unmountCall = unmountFetch.mock.calls[0];
    if (!unmountCall?.[1]?.signal) throw new Error("Expected an abortable list request.");
    const unmountSignal = unmountCall[1].signal;
    unmountView.unmount();
    expect(unmountSignal.aborted).toBe(true);
  });

  it("shows one disjoint page at a time and cancels selected detail on pagination", async () => {
    let resolveDetail: (value: ReturnType<typeof response>) => void = () => {};
    const detailPending = new Promise<ReturnType<typeof response>>((resolve) => {
      resolveDetail = resolve;
    });
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/incidents/history") && url.includes("offset=25")) {
        return Promise.resolve(response(page([executionRecord], 25, 26)));
      }
      if (url.startsWith("/incidents/history")) {
        return Promise.resolve(response(page([legacyRecord], 0, 26)));
      }
      return detailPending;
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<PersistedIncidentHistory />);

    await connect(user);
    expect(await screen.findByText("incident-legacy")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Open canonical record incident-legacy" }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const detailCall = fetchMock.mock.calls[1];
    if (!detailCall?.[1]?.signal) throw new Error("Expected an abortable detail request.");

    await user.click(screen.getByRole("button", { name: "Next history page" }));

    expect(detailCall[1].signal.aborted).toBe(true);
    expect(await screen.findByText("incident-next")).toBeInTheDocument();
    expect(screen.queryByText("incident-legacy")).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const pageCall = fetchMock.mock.calls[2];
    if (!pageCall) throw new Error("Expected the second bounded page request.");
    expect(pageCall[0]).toBe("/incidents/history?limit=25&offset=25");
    resolveDetail(response(legacyDetail));
  });
});
