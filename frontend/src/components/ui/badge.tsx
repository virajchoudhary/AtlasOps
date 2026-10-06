import type { HTMLAttributes } from "react";

type BadgeTone = "neutral" | "mint" | "amber" | "rose" | "outline";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

// Adapted from shadcn/ui Badge with AtlasOps semantic tones.
export function Badge({ tone = "neutral", className = "", ...props }: BadgeProps) {
  return (
    <span
      data-slot="badge"
      data-tone={tone}
      className={`badge badge--${tone} ${className}`.trim()}
      {...props}
    />
  );
}
