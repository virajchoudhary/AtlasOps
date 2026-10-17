export type ApplicationRoute =
  | { kind: "console" }
  | { kind: "reference"; pathname: string }
  | { kind: "not-found"; pathname: string };

const legalRoutes = new Set([
  "/legal/privacy-policy",
  "/legal/terms-of-service",
]);

const articleSlugs = [
  "the-content-architecture-four-months-in",
  "serve-content-to-ai-agents-llms-txt-markdown-sanity",
  "stale-sanity-content-nextjs-caching-revalidation",
] as const;

const referenceRoutes = new Set([
  "/reference",
  "/blog",
  "/blog/articles",
  "/roadmap",
  ...legalRoutes,
  ...articleSlugs.flatMap((slug) => [`/blog/${slug}`, `/blog/articles/${slug}`]),
]);

export function normalizeRoutePath(pathname: string): string {
  if (pathname === "/") return pathname;
  return pathname.replace(/\/+$/, "") || "/";
}

export function resolveApplicationRoute(pathname: string): ApplicationRoute {
  const path = normalizeRoutePath(pathname);
  if (path === "/") return { kind: "console" };
  if (referenceRoutes.has(path)) {
    return { kind: "reference", pathname: path };
  }
  return { kind: "not-found", pathname: path };
}
