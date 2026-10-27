/**
 * "Ready for your review" for an open PR whose gates all pass.
 *
 * `line` (drawers): one line with the gate count and Sentinel's verdict.
 * `card` (the board's PR row): the demo thumbnail and summary, the preview
 * pill, and the two ways forward (review here, merge on GitHub), with a
 * warning when a caffeine-ux change has no demo of its head.
 */

import { ExternalLinkIcon, PlayIcon, StarIcon, TriangleAlertIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { PrView, ProjectView } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { ageText, clipDuration } from "../board/boardModel";
import { DemoStatusTag, DemoThumb } from "../board/DemoThumb";
import { PreviewPill } from "../board/PreviewPill";
import { mergeUrl } from "../board/PrRow";
import { useFactoryNow } from "../factoryClock";
import { plural, shortSha } from "../factoryFormat";
import { IMergedItButton, HUMANS_MERGE } from "./ReviewActions";
import { gateTally, sentinelVerifiedHead, uxDemoMissing } from "./reviewModel";
import { DotParts } from "./reviewParts";

export interface ReadyForMergeBannerProps {
  /** The PR; renders nothing unless it is open and ready. */
  pr: PrView;
  /** `line` (default) for drawers; `card` replaces a ready PR row's lower half on the board. */
  variant?: "line" | "card";
  /** The PR's project (no-env and slot wording in the preview pill). */
  project?: ProjectView | null;
  /** The factory id ("I merged it" refreshes through it); card only. */
  factoryId?: string;
  /** Open the review drawer; card only. */
  onReview?: (number: number) => void;
  /** The clock ages read against (epoch seconds); the factory clock when omitted. */
  nowS?: number;
  /** Overrides the test id (`ready-banner-<n>` on cards, `drawer-ready-banner-<n>` in drawers). */
  testId?: string;
}

function Headline({ pr, nowS, label }: { pr: PrView; nowS: number; label: string }) {
  const tally = gateTally(pr);
  const sentinel = sentinelVerifiedHead(pr) ? pr.sentinel : null;
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-success">
      <StarIcon aria-hidden className="size-3.5 shrink-0 fill-current" />
      <DotParts
        parts={[
          { key: "label", node: label, className: "font-medium" },
          {
            key: "gates",
            node: `${tally.passing}/${tally.total} gates`,
            className: "tabular-nums",
          },
          sentinel && {
            key: "sentinel",
            node: `Sentinel verified${sentinel.at != null ? ` ${ageText(nowS - sentinel.at)}` : ""}`,
          },
        ]}
      />
    </span>
  );
}

function DemoMissing({ pr }: { pr: PrView }) {
  if (!uxDemoMissing(pr)) return null;
  return (
    <span
      data-testid={`ready-banner-demo-missing-${pr.number}`}
      className="flex items-center gap-1 text-sm text-warning"
    >
      <TriangleAlertIcon aria-hidden className="size-3.5 shrink-0" />
      {pr.demo
        ? `The demo is of ${shortSha(pr.demo.head_sha)}, not this head; this PR changes caffeine-ux.`
        : "No demo, and this PR changes caffeine-ux: watch it in the preview before merging."}
    </span>
  );
}

export function ReadyForMergeBanner({
  pr,
  variant = "line",
  project = null,
  factoryId,
  onReview,
  nowS: nowProp,
  testId,
}: ReadyForMergeBannerProps) {
  const clockS = Math.floor(useFactoryNow().getTime() / 1000);
  const nowS = nowProp ?? clockS;
  if (pr.state !== "open" || !pr.ready) return null;

  if (variant === "line") {
    return (
      <div
        data-testid={testId ?? `drawer-ready-banner-${pr.number}`}
        data-variant="line"
        className="flex flex-col gap-1 rounded-lg bg-success/10 px-3 py-1.5 text-sm"
      >
        <Headline pr={pr} nowS={nowS} label="Ready for human merge" />
        <DemoMissing pr={pr} />
      </div>
    );
  }

  const demo = pr.demo;
  const review = () => onReview?.(pr.number);
  const length = demo ? clipDuration(demo.duration_s) : null;
  return (
    <div
      data-testid={testId ?? `ready-banner-${pr.number}`}
      data-variant="card"
      className="flex min-w-0 flex-col gap-3 rounded-lg border border-success/25 bg-success/5 p-3 @2xl:flex-row @2xl:items-start"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-2 @md:flex-row @md:items-start @md:gap-3">
        {demo && (
          <DemoThumb
            prNumber={pr.number}
            demo={demo}
            onOpen={review}
            className="w-32 @max-md:w-full @max-md:max-w-sm"
          />
        )}
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <Headline pr={pr} nowS={nowS} label="Ready for your review" />
          {demo && (
            <p className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-sm text-muted-foreground">
              <span className="text-foreground">Demo</span>
              <DemoStatusTag status={demo.status} />
              <span>{plural(demo.steps.length, "step")}</span>
              {length && <span className="font-mono tabular-nums">· {length}</span>}
              <span className="font-mono tabular-nums">· {shortSha(demo.head_sha)}</span>
            </p>
          )}
          {demo?.summary && (
            <p className="line-clamp-2 text-sm text-foreground/80" title={demo.summary}>
              {demo.summary}
            </p>
          )}
          <DemoMissing pr={pr} />
          <div className="pt-0.5">
            <PreviewPill pr={pr} project={project} nowS={nowS} />
          </div>
        </div>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2 @2xl:flex-col @2xl:items-stretch">
        <Button
          size="sm"
          onClick={review}
          data-testid={`review-open-${pr.number}`}
          componentId="factory.board.review_open"
          className={cn("@max-md:flex-1")}
        >
          <PlayIcon className="size-3.5" />
          Review
        </Button>
        <Button asChild size="sm" variant="outline" className="@max-md:flex-1" title={HUMANS_MERGE}>
          <a
            href={mergeUrl(pr)}
            target="_blank"
            rel="noreferrer"
            data-testid={`merge-link-${pr.number}`}
          >
            Merge on GitHub
            <ExternalLinkIcon className="size-3.5" />
          </a>
        </Button>
        {factoryId && (
          <IMergedItButton factoryId={factoryId} pr={pr} testId={`i-merged-it-${pr.number}`} />
        )}
      </div>
    </div>
  );
}
