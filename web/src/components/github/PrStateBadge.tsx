import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/** Compact PR state shown beside the title in the shared panel header. */
export function PullRequestStatus({ state }: { state: string }) {
  const normalized = state.toUpperCase();
  const visual =
    normalized === "OPEN"
      ? {
          label: "Open",
          className: "border-green-500/25 bg-green-500/10 text-green-700 dark:text-green-400",
        }
      : normalized === "MERGED"
        ? {
            label: "Merged",
            className: "border-brand-accent/25 bg-brand-accent/10 text-brand-accent",
          }
        : normalized === "CLOSED"
          ? {
              label: "Closed",
              className: "border-red-500/25 bg-red-500/10 text-red-700 dark:text-red-400",
            }
          : { label: state, className: "border-border bg-muted text-muted-foreground" };

  return (
    <Badge
      aria-label={`Pull request status: ${visual.label}`}
      className={cn("h-5 rounded-full border px-2 py-px text-xs leading-none", visual.className)}
    >
      {visual.label}
    </Badge>
  );
}
