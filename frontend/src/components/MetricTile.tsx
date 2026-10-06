import type { ReactNode } from "react";

export function MetricTile({
  label,
  value,
  note,
  icon,
  tone = "mint",
}: {
  label: string;
  value: string;
  note?: string;
  icon?: ReactNode;
  tone?: "mint" | "amber" | "rose" | "graphite";
}) {
  return (
    <article className={`metric-tile metric-tile--${tone}`}>
      <div className="metric-topline">
        <span className="metric-label">{label}</span>
        {icon && <span className="metric-icon" aria-hidden="true">{icon}</span>}
      </div>
      <p className="metric-value">{value}</p>
      {note && <p className="metric-note">{note}</p>}
    </article>
  );
}
