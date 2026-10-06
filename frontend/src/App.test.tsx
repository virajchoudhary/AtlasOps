import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

vi.mock("motion/react", async () => {
  const React = await import("react");
  const passthrough = ({ children }: { children?: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children);
  return {
    AnimatePresence: passthrough,
    MotionConfig: passthrough,
    motion: { div: "div" },
    useReducedMotion: () => true,
  };
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("read-only research console routes", () => {
  it("renders all eight hash-routed pages from a minimal API response", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ source: "test snapshot" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    const routes = [
      ["Overview", "AtlasOps"],
      ["Agents", "Four roles. Clear authority boundaries."],
      ["Models", "Model lineage"],
      ["Evaluations", "Evaluations"],
      ["Incidents", "Incident chronology"],
      ["Evidence", "Provenance before presentation."],
      ["Runbooks", "Runbook catalog"],
      ["System", "A local, read-only presentation surface."],
    ];

    for (const [label, heading] of routes) {
      const links = screen.getAllByRole("link", { name: label });
      await user.click(links[0]);
      expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
    }
  }, 15000);
});
