import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { useCatalog } from "./api/useCatalog";
import { AppShell } from "./components/layout/AppShell";
import { resolveApplicationRoute } from "./reference/routes";

const ReferenceRoutes = lazy(() =>
  import("./reference/ReferenceRoutes").then((module) => ({
    default: module.ReferenceRoutes,
  })),
);

function OperationalConsole() {
  const { catalog, status, refreshFailed, reload } = useCatalog();
  return (
    <AppShell
      catalog={catalog}
      status={status}
      refreshFailed={refreshFailed}
      reload={reload}
    />
  );
}

function RouteNotFound({ pathname }: { pathname: string }) {
  useEffect(() => {
    const previousTitle = document.title;
    document.title = "404 | AtlasOps";
    return () => {
      document.title = previousTitle;
    };
  }, []);

  return (
    <main className="route-not-found" aria-labelledby="route-not-found-title">
      <p className="route-not-found-code">404</p>
      <h1 id="route-not-found-title">Page not found</h1>
      <p>
        <code>{pathname}</code> is not an AtlasOps console route or a public reference page.
      </p>
      <nav className="route-not-found-links" aria-label="Available destinations">
        <a href="/">AtlasOps console</a>
        <a href="/reference">Reference reconstruction</a>
      </nav>
    </main>
  );
}

function ReferenceLoading() {
  return (
    <main className="reference-route-loading" role="status" aria-live="polite">
      Loading reference page...
    </main>
  );
}

export default function App() {
  const [pathname, setPathname] = useState(() => window.location.pathname);

  useEffect(() => {
    const syncPathname = () => setPathname(window.location.pathname);
    window.addEventListener("popstate", syncPathname);
    return () => window.removeEventListener("popstate", syncPathname);
  }, []);

  const navigateReference = useCallback((href: string) => {
    const destination = new URL(href, window.location.origin);
    if (destination.origin !== window.location.origin) return;

    window.history.pushState({}, "", `${destination.pathname}${destination.search}${destination.hash}`);
    setPathname(destination.pathname);
    window.scrollTo(0, 0);
  }, []);

  const route = resolveApplicationRoute(pathname);
  switch (route.kind) {
    case "console":
      return <OperationalConsole />;
    case "reference":
      return (
        <Suspense fallback={<ReferenceLoading />}>
          <ReferenceRoutes pathname={route.pathname} onNavigate={navigateReference} />
        </Suspense>
      );
    case "not-found":
      return <RouteNotFound pathname={route.pathname} />;
    default: {
      const _exhaustive: never = route;
      return _exhaustive;
    }
  }
}
