import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchAttempt, fetchCatalog } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("read API client", () => {
  it("requests only the repository catalog with GET and no cache", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ source: "snapshot" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchCatalog();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/catalog",
      expect.objectContaining({ method: "GET", cache: "no-store" }),
    );
  });

  it("encodes historical attempt names and keeps the detail request read-only", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: "historical" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchAttempt("EXP-STAGE4-SF002-002.interruption.json");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/attempts/EXP-STAGE4-SF002-002.interruption.json",
      expect.objectContaining({ method: "GET" }),
    );
  });
});
