import { describe, expect, it } from "vitest";
import { resolveApplicationRoute } from "./routes";

describe("application pathname routing", () => {
  it("keeps the operational console at the root for every hash route", () => {
    expect(resolveApplicationRoute("/")).toEqual({ kind: "console" });
  });

  it.each([
    "/reference",
    "/blog",
    "/blog/articles",
    "/blog/articles/serve-content-to-ai-agents-llms-txt-markdown-sanity",
    "/roadmap",
    "/legal/privacy-policy",
    "/legal/terms-of-service",
  ])("resolves the reference surface at %s", (pathname) => {
    expect(resolveApplicationRoute(pathname)).toEqual({
      kind: "reference",
      pathname,
    });
  });

  it("normalizes trailing slashes on known client routes", () => {
    expect(resolveApplicationRoute("/reference/")).toEqual({
      kind: "reference",
      pathname: "/reference",
    });
  });

  it.each([
    "/incidents",
    "/blog/not-an-article",
    "/legal/unknown",
    "/reference/nested",
    "/artifacts/evidence/stage7/free-t4-v17/RESULT.json",
  ])("does not route unknown paths to the console at %s", (pathname) => {
    expect(resolveApplicationRoute(pathname)).toEqual({
      kind: "not-found",
      pathname,
    });
  });
});
