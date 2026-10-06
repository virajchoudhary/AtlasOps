import { useCallback, useEffect, useRef, useState } from "react";
import { fetchCatalog } from "./client";
import type { Catalog } from "./types";

export type CatalogStatus = "loading" | "refreshing" | "ready" | "unavailable";

export function useCatalog() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [status, setStatus] = useState<CatalogStatus>("loading");
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const hasSnapshot = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setStatus(hasSnapshot.current ? "refreshing" : "loading");
    setRefreshFailed(false);

    fetchCatalog(controller.signal)
      .then((value) => {
        if (!active) return;
        hasSnapshot.current = true;
        setCatalog(value);
        setStatus("ready");
      })
      .catch(() => {
        if (!active) return;
        setRefreshFailed(hasSnapshot.current);
        setStatus(hasSnapshot.current ? "ready" : "unavailable");
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [refreshKey]);

  const reload = useCallback(() => setRefreshKey((key) => key + 1), []);
  return { catalog, status, refreshFailed, reload };
}
