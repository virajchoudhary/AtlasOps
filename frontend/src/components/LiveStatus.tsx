import { useEffect, useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";

interface Observation {
  enabled: boolean;
  observed_at: string | null;
  services: { id: string; label: string; available: boolean; detail: string }[];
}

export function LiveStatus() {
  const [status, setStatus] = useState<Observation | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setFailed(false);
    fetch("/api/live-status", { method: "GET", cache: "no-store", signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Observation unavailable");
        return response.json() as Promise<Observation>;
      })
      .then(setStatus)
      .catch(() => { if (!controller.signal.aborted) setFailed(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);
  if (!status?.enabled && !failed) return null;
  return <section className="demo-live" aria-label="Live service observations">
    <div className="demo-toolbar">
      <div><p className="eyebrow">Local environment</p><h2>Live service observations</h2></div>
      <Button variant="quiet" size="icon" title="Refresh live observations" aria-label="Refresh live observations"
        disabled={loading} onClick={() => setRefresh((value) => value + 1)}><RefreshCw size={16} /></Button>
    </div>
    <p>Current service responses, not agent execution or incident-resolution evidence.</p>
    {failed ? <p role="alert">Live observations unavailable. No substitute health result is shown.</p>
      : <div className="demo-live-rows">{status?.services.map((service) => <div key={service.id}>
        <strong>{service.label}</strong>
        <Badge tone={service.available ? "mint" : "amber"}>{service.available ? "Responding" : "Unavailable"}</Badge>
        <span>{service.detail}</span>
      </div>)}</div>}
    <p className="demo-provenance">{loading ? "Reading current responses" : `Observed at: ${status?.observed_at ?? "Unavailable"}`}</p>
    <a href="http://127.0.0.1:17880/" target="_blank" rel="noreferrer" className="button button--outline button--md">
      <ExternalLink size={16} aria-hidden="true" /> Open live Boutique
    </a>
  </section>;
}
