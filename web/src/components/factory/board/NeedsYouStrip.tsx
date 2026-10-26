/**
 * What needs the human, as chips that jump to (and flash) their target: PRs
 * ready for review, decisions, alerts (a dead lead, a preview gone on a ready
 * PR) and projects whose PL claims a stage the gates don't show.
 */

import type { ReactNode } from "react";
import { CircleCheckIcon, CircleXIcon, StarIcon, TriangleAlertIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { type FactoryTone, TONE_CHIP, plural } from "../factoryFormat";
import { type NeedsYou, needsYouCount, prAnchorId, projectAnchorId } from "./boardModel";

function Chip({
  tone,
  icon,
  testId,
  count,
  onClick,
  expanded,
  children,
}: {
  tone: FactoryTone;
  icon: ReactNode;
  testId: string;
  count: number;
  onClick: () => void;
  expanded?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      data-count={count}
      aria-expanded={expanded}
      className={cn(
        "inline-flex h-6 cursor-pointer items-center gap-1 rounded-md px-2 text-sm font-medium whitespace-nowrap transition-colors hover:brightness-95 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none dark:hover:brightness-110",
        TONE_CHIP[tone],
      )}
    >
      {icon}
      {children}
    </button>
  );
}

export function NeedsYouStrip({
  needs,
  onJump,
  onDecisions,
  decisionsExpanded,
  decisionsPanel,
}: {
  needs: NeedsYou;
  /** Scroll to and flash an element by DOM id. */
  onJump: (targetId: string) => void;
  /** Show the decisions (rail, or the panel below on narrow screens). */
  onDecisions: () => void;
  /** Narrow screens: whether the decisions panel is open. */
  decisionsExpanded?: boolean;
  /** Narrow screens: the decisions rail, shown under the strip when expanded. */
  decisionsPanel?: ReactNode;
}) {
  const count = needsYouCount(needs);
  const { readyPrs, openDecisions, blockingDecisions, alerts, claimMismatches } = needs;
  return (
    <section
      data-testid="factory-needs-you"
      data-count={count}
      aria-label="Needs you"
      className="flex flex-col gap-2"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <h2 className="mr-1 text-sm font-medium text-muted-foreground">Needs you</h2>
        {count === 0 && (
          <span className="inline-flex items-center gap-1 text-sm text-muted-foreground">
            <CircleCheckIcon aria-hidden className="size-3.5 text-success" />
            Nothing right now
          </span>
        )}
        {readyPrs.length > 0 && (
          <Chip
            tone="pass"
            testId="needs-you-ready"
            count={readyPrs.length}
            icon={<StarIcon aria-hidden className="size-3 fill-current" />}
            onClick={() => onJump(prAnchorId(readyPrs[0]!.number))}
          >
            {plural(readyPrs.length, "PR")} ready for review
          </Chip>
        )}
        {openDecisions.length > 0 && (
          <Chip
            tone="warning"
            testId="needs-you-decisions"
            count={openDecisions.length}
            expanded={decisionsPanel ? decisionsExpanded : undefined}
            icon={<TriangleAlertIcon aria-hidden className="size-3" />}
            onClick={onDecisions}
          >
            {blockingDecisions.length > 0
              ? `${plural(blockingDecisions.length, "blocking decision")}`
              : plural(openDecisions.length, "open decision")}
          </Chip>
        )}
        {alerts.length > 0 && (
          <Chip
            tone="fail"
            testId="needs-you-alerts"
            count={alerts.length}
            icon={<CircleXIcon aria-hidden className="size-3" />}
            onClick={() => onJump(alerts[0]!.targetId)}
          >
            {alerts.length === 1 ? alerts[0]!.text : plural(alerts.length, "alert")}
          </Chip>
        )}
        {claimMismatches.length > 0 && (
          <Chip
            tone="warning"
            testId="needs-you-claims"
            count={claimMismatches.length}
            icon={<TriangleAlertIcon aria-hidden className="size-3" />}
            onClick={() => onJump(projectAnchorId(claimMismatches[0]!.slug))}
          >
            {claimMismatches.length === 1
              ? `${claimMismatches[0]!.slug}: claim ≠ gates`
              : `${claimMismatches.length} claims ≠ gates`}
          </Chip>
        )}
      </div>
      {decisionsPanel && decisionsExpanded && (
        <div
          data-testid="needs-you-decisions-panel"
          className="rounded-xl border border-border bg-muted/30 p-3"
        >
          {decisionsPanel}
        </div>
      )}
    </section>
  );
}
