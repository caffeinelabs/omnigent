import type * as React from "react";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import type { GithubCheckRun } from "@/hooks/useGithub";
import { cn } from "@/lib/utils";

/**
 * A CI-status pill (e.g. "✓ 66 passed"); hovering it reveals the individual job
 * names in that bucket, each with the status icon and a divider between rows.
 * Renders nothing when the bucket is empty. The full list shows — the card is
 * height-capped and scrolls.
 */
export function CheckPill({
  label,
  count,
  runs,
  icon,
  className,
}: {
  label: string;
  count: number;
  /** All checks; filtered to this pill's bucket for the hover list. */
  runs: GithubCheckRun[];
  icon: React.ReactNode;
  /** Tint for the pill + icon. */
  className: string;
}) {
  if (count === 0) return null;
  const names = runs.filter((r) => r.name);
  return (
    <HoverCard openDelay={100} closeDelay={100}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          className={cn(
            "inline-flex cursor-pointer items-center gap-0.5 rounded-full border px-1.5 py-px text-xs tabular-nums",
            className,
          )}
        >
          {icon}
          {count} {label}
        </button>
      </HoverCardTrigger>
      <HoverCardContent
        side="bottom"
        align="start"
        className="max-h-64 w-auto max-w-xs overflow-y-auto p-1"
      >
        {names.length === 0 ? (
          <p className="px-1.5 py-1 text-muted-foreground">No job details.</p>
        ) : (
          <ul className="divide-y divide-border">
            {names.map((r) => (
              <li key={r.url ?? r.name} className="flex items-center gap-2 px-1.5 py-1.5">
                <span className="shrink-0">{icon}</span>
                <span className="truncate" title={r.name}>
                  {r.name}
                </span>
              </li>
            ))}
          </ul>
        )}
      </HoverCardContent>
    </HoverCard>
  );
}
