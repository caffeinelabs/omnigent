/**
 * A PR as a Room card: number, title, where it stands (open, ready for human
 * merge, merged, closed), its head, its gates as chips, and Review → to the
 * PR page. A PR the snapshot no longer lists shows the event's line.
 */

import {
  ArrowRightIcon,
  GitMergeIcon,
  GitPullRequestArrowIcon,
  GitPullRequestClosedIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import type { PrView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { GateChip, shownGates } from "../board/GateChip";
import { useFactoryServerNowS } from "../factoryClock";
import { TONE_CHIP, type FactoryTone } from "../factoryFormat";
import { prPageHref } from "../factoryRoutes";

export function prStateView(pr: PrView): { label: string; tone: FactoryTone } {
  if (pr.state === "merged") return { label: "Merged", tone: "merged" };
  if (pr.state === "closed") return { label: "Closed", tone: "muted" };
  if (pr.ready) return { label: "Ready for human merge", tone: "pass" };
  if (pr.is_draft) return { label: "Draft", tone: "muted" };
  return { label: "Open", tone: "active" };
}

export function PrCard({
  factoryId,
  pr,
  line,
  clockOffsetS = 0,
  className,
}: {
  factoryId: string;
  pr: PrView;
  /** The event that put the card here ("verify FAIL at 5e1c2d4"). */
  line?: string | null;
  clockOffsetS?: number;
  className?: string;
}) {
  const nowS = useFactoryServerNowS(clockOffsetS);
  const state = prStateView(pr);
  const Icon =
    pr.state === "merged"
      ? GitMergeIcon
      : pr.state === "closed"
        ? GitPullRequestClosedIcon
        : GitPullRequestArrowIcon;
  return (
    <article
      data-testid={`pr-card-${pr.number}`}
      data-state={pr.state}
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-lg border border-border bg-card p-3 text-sm",
        className,
      )}
    >
      <header className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1">
        <Icon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="font-mono font-medium">!{pr.number}</span>
        <span className="font-mono text-xs text-muted-foreground">{pr.head_sha.slice(0, 7)}</span>
        {pr.project_slug && <span className="text-muted-foreground">· {pr.project_slug}</span>}
        <span
          className={cn(
            "ml-auto shrink-0 rounded-full px-1.5 text-xs leading-5",
            TONE_CHIP[state.tone],
          )}
        >
          {state.label}
        </span>
      </header>
      <p className="line-clamp-2 text-ui break-words" title={pr.title}>
        {pr.title}
      </p>
      {line && <p className="text-muted-foreground break-words">{line}</p>}
      <div className="flex min-w-0 flex-wrap items-center gap-1">
        {shownGates(pr).map((gate) => (
          <GateChip key={gate} pr={pr} gate={gate} nowS={nowS} />
        ))}
        <Button asChild size="xs" variant="ghost" className="ml-auto text-muted-foreground">
          <Link
            to={prPageHref(factoryId, pr.id)}
            componentId="factory.room.pr_review"
            data-testid={`pr-card-review-${pr.number}`}
          >
            Review
            <ArrowRightIcon className="size-3.5" />
          </Link>
        </Button>
      </div>
    </article>
  );
}
