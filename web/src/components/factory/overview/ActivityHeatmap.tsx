/**
 * The last hour per project at a glance: one row of 2-minute cells per
 * project in flight (a tool running, thinking, waiting on a human, waiting on
 * CI or a preview), and on the right what it waits on now. Rows open the
 * project page. Reads spans and events (events alone on a server without spans).
 */

import { useMemo } from "react";
import { ActivityIcon } from "lucide-react";
import { useFactoryEvents, useFactorySpans } from "@/hooks/useFactory";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { ApiError } from "@/lib/sessionsApi";
import { cn } from "@/lib/utils";
import { FactoryLoadError } from "../FactoryLoadError";
import { projectPageHref, projectsPageHref } from "../factoryRoutes";
import { SectionCard } from "../project/projectParts";
import {
  type CellKind,
  HEATMAP_CELLS,
  HEATMAP_SPAN_KINDS,
  type HeatmapRow,
  heatmapFromEvents,
  heatmapFromSpans,
  heatmapStartS,
} from "./heatmapModel";

const CELL_CLASS: Record<CellKind, string> = {
  tool: "bg-session-active",
  thinking: "bg-session-active/40",
  human: "bg-warning/70",
  ci: "bg-muted-foreground/35",
  idle: "bg-muted",
};

const LEGEND: { kind: CellKind; label: string }[] = [
  { kind: "tool", label: "tool" },
  { kind: "thinking", label: "thinking" },
  { kind: "human", label: "waiting on a human" },
  { kind: "ci", label: "waiting on CI / preview" },
];

const NOTE_TONE: Record<HeatmapRow["tone"], string> = {
  muted: "text-muted-foreground",
  warning: "text-warning",
  fail: "text-destructive",
};

/** Spans this far before the window still count (a wait that began earlier). */
const LOOKBACK_MS = 6 * 3600 * 1000;
/** The spans read starts on this grid, so its key (and the read) moves once an hour. */
const SINCE_GRID_MS = 3600 * 1000;

const HEATMAP_EVENT_FILTERS = { limit: 500 };

function useHeatmapRows(snapshot: FactorySnapshot, nowS: number) {
  const fid = snapshot.factory.id;
  const sinceMs =
    Math.floor((heatmapStartS(nowS) * 1000 - LOOKBACK_MS) / SINCE_GRID_MS) * SINCE_GRID_MS;
  const spans = useFactorySpans(fid, sinceMs, { kinds: HEATMAP_SPAN_KINDS });
  const noSpans = spans.error instanceof ApiError && spans.error.status === 404;
  // Agent activity comes from events either way (no turn or tool spans yet).
  const events = useFactoryEvents(fid, HEATMAP_EVENT_FILTERS);
  const eventRows = events.data?.pages[0]?.data;
  const rows = useMemo(() => {
    if (spans.data) return heatmapFromSpans(snapshot, spans.data.data, nowS, eventRows ?? []);
    return noSpans && eventRows ? heatmapFromEvents(snapshot, eventRows, nowS) : null;
  }, [spans.data, noSpans, eventRows, snapshot, nowS]);
  const source = spans.data ? "spans" : noSpans ? "events" : null;
  const error = noSpans ? events.error : spans.error;
  return {
    rows,
    source,
    error,
    loading: !rows && !error,
    refetch: () => void (noSpans ? events.refetch() : spans.refetch()),
  };
}

export function ActivityHeatmap({
  snapshot,
  nowS,
  limit = 8,
}: {
  snapshot: FactorySnapshot;
  nowS: number;
  limit?: number;
}) {
  const { rows: all, source, error, loading, refetch } = useHeatmapRows(snapshot, nowS);
  const rows = all?.slice(0, limit) ?? null;
  const hidden = all ? all.length - (rows?.length ?? 0) : 0;
  return (
    <SectionCard
      title="Activity"
      meta="last 60 min · 2 min cells"
      testId="overview-heatmap"
      className="gap-2.5"
      action={
        <ul
          aria-label="Legend"
          className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground max-sm:hidden"
        >
          {LEGEND.map((item) => (
            <li key={item.kind} className="inline-flex items-center gap-1">
              <span aria-hidden className={cn("size-2.5 rounded-[2px]", CELL_CLASS[item.kind])} />
              {item.label}
            </li>
          ))}
        </ul>
      }
    >
      {error instanceof Error && !rows && (
        <FactoryLoadError
          error={error}
          updatedAt={0}
          onRetry={refetch}
          what="the activity"
          testId="overview-heatmap-error"
          className="mb-0"
        />
      )}
      {loading && (
        <div aria-busy="true" aria-label="Loading activity" className="flex flex-col gap-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-3.5 animate-pulse rounded bg-muted" />
          ))}
        </div>
      )}
      {rows && rows.length === 0 && (
        <p
          data-testid="overview-heatmap-empty"
          className="flex items-center gap-1.5 text-sm text-muted-foreground"
        >
          <ActivityIcon aria-hidden className="size-3.5" />
          No project in flight.
        </p>
      )}
      {rows && rows.length > 0 && (
        <div
          data-testid="overview-heatmap-rows"
          data-source={source ?? undefined}
          className="flex flex-col gap-1.5"
        >
          {rows.map((row) => (
            <div
              key={row.slug}
              data-testid={`heatmap-row-${row.slug}`}
              className="grid min-w-0 grid-cols-[minmax(0,7rem)_minmax(0,1fr)] items-center gap-x-3 gap-y-0.5 text-sm md:grid-cols-[minmax(0,9rem)_minmax(0,1fr)_minmax(0,12rem)]"
            >
              <Link
                to={projectPageHref(snapshot.factory.id, row.slug)}
                componentId="factory.overview.heatmap_project"
                className="min-w-0 truncate font-mono text-foreground hover:underline"
                title={row.title}
              >
                {row.slug}
              </Link>
              <div
                role="img"
                aria-label={`${row.slug}: ${row.busy} of ${HEATMAP_CELLS} cells active`}
                className="flex h-3.5 min-w-0 gap-px"
              >
                {row.cells.map((cell, i) => (
                  <span
                    // eslint-disable-next-line react/no-array-index-key -- cells are positional
                    key={i}
                    data-kind={cell}
                    className={cn("min-w-0 flex-1 rounded-[2px]", CELL_CLASS[cell])}
                  />
                ))}
              </div>
              <span
                className={cn(
                  "min-w-0 truncate max-md:col-start-2 md:text-right",
                  NOTE_TONE[row.tone],
                  !row.note && "max-md:hidden",
                )}
                title={row.note ?? undefined}
              >
                {row.note ?? "—"}
              </span>
            </div>
          ))}
          <div className="grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)] gap-x-3 text-xs text-muted-foreground md:grid-cols-[minmax(0,9rem)_minmax(0,1fr)_minmax(0,12rem)]">
            {hidden > 0 ? (
              <Link
                to={projectsPageHref(snapshot.factory.id)}
                componentId="factory.overview.heatmap_more"
                data-testid="overview-heatmap-more"
                className="min-w-0 truncate hover:text-foreground hover:underline"
              >
                +{hidden} more → Projects
              </Link>
            ) : (
              <span />
            )}
            <span className="flex justify-between tabular-nums">
              <span>−60m</span>
              <span>−30m</span>
              <span>now</span>
            </span>
          </div>
        </div>
      )}
    </SectionCard>
  );
}
