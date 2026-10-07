import type { AttemptDetail, Catalog } from "./types";
import type { RehearsalCatalog } from "../demo/workflow";

async function readJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`Read request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export function fetchCatalog(signal?: AbortSignal): Promise<Catalog> {
  return readJson<Catalog>("/api/catalog", signal);
}

export function fetchAttempt(name: string, signal?: AbortSignal): Promise<AttemptDetail> {
  return readJson<AttemptDetail>(`/api/attempts/${encodeURIComponent(name)}`, signal);
}

export function fetchRehearsal(signal?: AbortSignal): Promise<RehearsalCatalog> {
  return readJson<RehearsalCatalog>("/api/rehearsal", signal);
}
