import type { ReactNode } from "react";
import { ArrowRightIcon, FactoryIcon, StarIcon, TriangleAlertIcon } from "lucide-react";
import { relativeTime } from "@/lib/relativeTime";
import { Link } from "@/lib/routing";
import type { FactoryStage, FactorySummary, MemberState } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import {
  type FactoryTone,
  STAGE_SHORT_LABEL,
  TONE_CHIP,
  TONE_DOT,
  formatUsd,
  plural,
} from "./factoryFormat";

/** The index strip's pipeline; finished (`done`) projects count as merged. */
const INDEX_STAGES: FactoryStage[] = [
  "queued",
  "planning",
  "building",
  "review",
  "verify",
  "ready",
  "merged",
];

const STATE_TONE: Record<MemberState, FactoryTone> = {
  active: "pass",
  paused: "warning",
  done: "muted",
};

const STATE_LABEL: Record<MemberState, string> = {
  active: "Active",
  paused: "Paused",
  done: "Done",
};

/** `● Active` / `● Paused` / `● Done`. */
export function FactoryStateChip({ state }: { state: MemberState }) {
  return (
    <span
      data-testid="factory-state-chip"
      data-state={state}
      className="inline-flex items-center gap-1.5 text-sm text-foreground"
    >
      <span aria-hidden className={cn("size-1.5 rounded-full", TONE_DOT[STATE_TONE[state]])} />
      {STATE_LABEL[state]}
    </span>
  );
}

function stageCount(factory: FactorySummary, stage: FactoryStage): number {
  const counts = factory.stage_counts;
  const n = counts[stage] ?? 0;
  return stage === "merged" ? n + (counts.done ?? 0) : n;
}

/** Projects per derived stage, zero counts dimmed and Ready called out. */
export function FactoryStageCounts({ factory }: { factory: FactorySummary }) {
  return (
    <ol
      aria-label="Projects by stage"
      data-testid="factory-stage-counts"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm"
    >
      {INDEX_STAGES.map((stage) => {
        const n = stageCount(factory, stage);
        const highlight = stage === "ready" && n > 0;
        return (
          <li
            key={stage}
            data-stage={stage}
            className={cn(
              "inline-flex items-baseline gap-1",
              n === 0 ? "text-muted-foreground/50" : "text-muted-foreground",
              highlight && "text-success",
            )}
          >
            {highlight && <StarIcon aria-hidden className="size-3 self-center fill-current" />}
            {STAGE_SHORT_LABEL[stage]}
            <span
              className={cn(
                "tabular-nums",
                n > 0 && !highlight && "text-foreground",
                highlight && "font-medium",
              )}
            >
              {n}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function Chip({
  tone,
  icon,
  children,
}: {
  tone: FactoryTone;
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1 rounded-md px-1.5 text-sm whitespace-nowrap",
        TONE_CHIP[tone],
      )}
    >
      {icon}
      {children}
    </span>
  );
}

/** `Shared with you by luka@… · operator`, or null for the viewer's own factory. */
export function sharedLine(factory: FactorySummary): string | null {
  if (!factory.shared_by) return null;
  return `Shared with you by ${factory.shared_by}${factory.viewer_role ? ` · ${factory.viewer_role}` : ""}`;
}

/** What waits on the human, then the team size; omits zero counts. */
function NeedsYou({ factory }: { factory: FactorySummary }) {
  const agents =
    factory.members_running > 0
      ? `${plural(factory.members_total, "agent")} (${factory.members_running} running)`
      : plural(factory.members_total, "agent");
  return (
    <div
      data-testid="factory-needs-you"
      className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1"
    >
      {(factory.mine ?? 0) > 0 && (
        <Chip tone="warning" icon={<TriangleAlertIcon aria-hidden className="size-3" />}>
          {factory.mine} for you
        </Chip>
      )}
      {factory.decisions_open > 0 && factory.mine == null && (
        <Chip tone="warning" icon={<TriangleAlertIcon aria-hidden className="size-3" />}>
          {plural(factory.decisions_open, "decision")}
        </Chip>
      )}
      {factory.prs_ready > 0 && (
        <Chip tone="pass" icon={<StarIcon aria-hidden className="size-3 fill-current" />}>
          {factory.prs_ready} ready for review
        </Chip>
      )}
      {factory.drifted_members > 0 && (
        <Chip tone="warning">{plural(factory.drifted_members, "old launch mode")}</Chip>
      )}
      <span className="text-sm text-muted-foreground">{agents}</span>
    </div>
  );
}

/** One factory on the index: state, pipeline counts, what needs you, cost. */
export function FactoryIndexCard({ factory, now }: { factory: FactorySummary; now: Date }) {
  const started =
    factory.started_at != null ? relativeTime(factory.started_at * 1000, now.getTime()) : null;
  const cost = formatUsd(factory.cost_usd);
  return (
    <Link
      to={`/factory/${factory.id}`}
      componentId="factory.index.open"
      data-testid={`factory-card-${factory.id}`}
      aria-label={`Open ${factory.name}`}
      className="group flex flex-col gap-2.5 rounded-xl border border-border bg-card p-4 text-left transition-colors hover:bg-muted/40 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <span className="flex min-w-0 items-center gap-2">
          <FactoryIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
          <span className="truncate text-ui font-semibold">{factory.name}</span>
        </span>
        <FactoryStateChip state={factory.state} />
        <span className="text-sm text-muted-foreground">
          {factory.cadence === "demo" ? "Demo cadence" : "Normal cadence"}
        </span>
        {started && (
          <span className="text-sm text-muted-foreground">
            started {started === "now" ? "just now" : `${started} ago`}
          </span>
        )}
        <span
          className="ml-auto font-mono text-sm text-foreground tabular-nums"
          title="Cost so far (all agents)"
        >
          {cost ?? "—"}
        </span>
      </div>
      {sharedLine(factory) && (
        <p data-testid="factory-shared-line" className="-mt-1 text-sm text-muted-foreground">
          {sharedLine(factory)}
        </p>
      )}
      <FactoryStageCounts factory={factory} />
      <div className="flex items-end justify-between gap-3">
        <NeedsYou factory={factory} />
        <span className="inline-flex shrink-0 items-center gap-1 text-sm text-muted-foreground group-hover:text-foreground">
          Open
          <ArrowRightIcon aria-hidden className="size-3.5" />
        </span>
      </div>
    </Link>
  );
}

/** Placeholder card while the factory list loads. */
export function FactoryIndexCardSkeleton() {
  return (
    <div
      aria-hidden
      data-testid="factory-card-skeleton"
      className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4"
    >
      <div className="flex items-center gap-3">
        <div className="h-4 w-40 animate-pulse rounded bg-muted" />
        <div className="h-4 w-16 animate-pulse rounded bg-muted" />
        <div className="ml-auto h-4 w-12 animate-pulse rounded bg-muted" />
      </div>
      <div className="h-4 w-4/5 animate-pulse rounded bg-muted" />
      <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
    </div>
  );
}
