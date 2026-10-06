import type { EvidenceBrowserEntry } from "../api/types";

export const UNAVAILABLE = "Unavailable";

export function displayText(value: unknown): string {
  return typeof value === "string" && value.trim() ? value.trim() : UNAVAILABLE;
}

export function displayNumber(value: unknown, digits = 0): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return UNAVAILABLE;
  return digits === 0 ? value.toLocaleString("en-US") : value.toFixed(digits);
}

export function displayRatio(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(5)
    : UNAVAILABLE;
}

export function displayBoolean(value: unknown, yes = "Yes", no = "No"): string {
  if (typeof value !== "boolean") return UNAVAILABLE;
  return value ? yes : no;
}

export function shortDigest(value: unknown): string {
  return typeof value === "string" && /^[a-f0-9]{64}$/i.test(value)
    ? value.slice(0, 12)
    : UNAVAILABLE;
}

export function comparisonWidth(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1
    ? value * 100
    : null;
}

export type EvidenceFilter = "all" | "current" | "empirical" | "negative" | "historical" | "external";

export function matchesEvidenceFilter(
  item: EvidenceBrowserEntry,
  filter: EvidenceFilter,
): boolean {
  switch (filter) {
    case "current":
      return item.scope === "current";
    case "empirical":
      return item.kind === "empirical";
    case "negative":
      return item.negative;
    case "historical":
      return item.scope === "historical";
    case "external":
      return item.availability === "external";
    default:
      return true;
  }
}
