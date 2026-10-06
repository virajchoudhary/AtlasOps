import { describe, expect, it } from "vitest";
import {
  comparisonWidth,
  displayBoolean,
  displayNumber,
  displayNumberWithUnit,
  displayRatio,
  displayText,
  matchesEvidenceFilter,
} from "./format";
import type { EvidenceBrowserEntry } from "../api/types";

const evidenceRow = (overrides: Partial<EvidenceBrowserEntry> = {}): EvidenceBrowserEntry => ({
  id: "evidence-1",
  title: "Reference",
  path: "docs/reference.json",
  classification: "Current artifact provenance",
  area: "SFT",
  available: true,
  sha256: null,
  scope: "current",
  kind: "provenance",
  negative: false,
  availability: "present",
  details: "Reference record",
  ...overrides,
});

describe("truth-safe display helpers", () => {
  it("keeps missing values unavailable and preserves finite zeroes", () => {
    expect(displayText(null)).toBe("Unavailable");
    expect(displayNumber(null)).toBe("Unavailable");
    expect(displayNumber(0)).toBe("0");
    expect(displayRatio(0)).toBe("0.00000");
    expect(displayBoolean(null)).toBe("Unavailable");
  });

  it("uses the full zero-to-one domain without clamping out-of-range values", () => {
    expect(comparisonWidth(0.25)).toBe(25);
    expect(comparisonWidth(1)).toBe(100);
    expect(comparisonWidth(-0.1)).toBeNull();
    expect(comparisonWidth(1.1)).toBeNull();
  });

  it("only adds units to finite numeric values", () => {
    for (const value of [null, undefined, NaN, Infinity, ""]) {
      expect(displayNumberWithUnit(value, "s")).toBe("Unavailable");
    }
    expect(displayNumberWithUnit(0, "s")).toBe("0 s");
    expect(displayNumberWithUnit(12, "s")).toBe("12 s");
  });
});

describe("typed evidence filters", () => {
  it("uses explicit scope, kind, disposition, and availability fields", () => {
    const currentEmpirical = evidenceRow({ kind: "empirical", negative: true });
    const historical = evidenceRow({ scope: "historical", kind: "non-empirical" });
    const external = evidenceRow({ availability: "external", available: false, path: null });
    const missing = evidenceRow({ availability: "missing", available: false, path: null });

    expect(matchesEvidenceFilter(currentEmpirical, "empirical")).toBe(true);
    expect(matchesEvidenceFilter(currentEmpirical, "negative")).toBe(true);
    expect(matchesEvidenceFilter(historical, "historical")).toBe(true);
    expect(matchesEvidenceFilter(external, "external")).toBe(true);
    expect(matchesEvidenceFilter(missing, "external")).toBe(false);
  });
});
