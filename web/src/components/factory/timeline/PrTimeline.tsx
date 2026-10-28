/**
 * A PR's timeline on its page: its rounds, gates, demo and the wait for a
 * human, its loops' attempts, and its project's agents, from the PR's spans
 * (`GET …/spans?pr_id=`). A link opens the full Timeline on its project.
 */

import { useMemo } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  type FactorySnapshot,
  type PrView,
  type SpanPage,
  fetchFactorySpans,
} from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { isMissingRoute } from "../agents/opsQueries";
import { FactoryLoadError } from "../FactoryLoadError";
import { timelinePageHref } from "../factoryRoutes";
import { DEFAULT_SHOW, buildTimeline } from "./timelineModel";
import { TimelineView } from "./TimelineView";

const MARGIN_MS = 10 * 60_000;

export function PrTimeline({
  snapshot,
  pr,
  nowS,
}: {
  snapshot: FactorySnapshot;
  pr: PrView;
  nowS: number;
}) {
  const fid = snapshot.factory.id;
  const spans = useQuery<SpanPage>({
    queryKey: ["factory-spans", fid, "pr", pr.id],
    queryFn: ({ signal }) => fetchFactorySpans(fid, { prId: pr.id, limit: 2000 }, signal),
    placeholderData: keepPreviousData,
    retry: false,
  });
  const data = spans.data?.data;
  const to = Math.floor(nowS / 10) * 10_000;
  const from = useMemo(() => {
    const first = (data ?? []).reduce((min, s) => Math.min(min, s.started_at), to - 3_600_000);
    return first - MARGIN_MS;
  }, [data, to]);
  const groups = useMemo(
    () =>
      data?.length
        ? buildTimeline(data, snapshot, {
            from,
            to,
            show: DEFAULT_SHOW,
            project: pr.project_slug,
            nowS,
          })
        : [],
    [data, snapshot, from, to, pr.project_slug, nowS],
  );
  return (
    <section
      aria-label="Timeline"
      data-testid="pr-timeline"
      className="flex min-w-0 flex-col gap-2"
    >
      <div className="flex min-w-0 items-baseline gap-2">
        <h2 className="text-ui font-semibold">Timeline</h2>
        <span className="text-sm text-muted-foreground">rounds, gates, waits and attempts</span>
        <Link
          to={timelinePageHref(
            fid,
            pr.project_slug ? { project: pr.project_slug, window: "24h" } : {},
          )}
          componentId="factory.pr.timeline_open"
          className="ml-auto shrink-0 text-sm text-muted-foreground hover:text-foreground"
        >
          Open the Timeline →
        </Link>
      </div>
      {spans.isLoading ? (
        <div
          aria-busy="true"
          aria-label="Loading the timeline"
          className="h-24 animate-pulse rounded-xl bg-muted"
        />
      ) : spans.error && !data && !isMissingRoute(spans.error) ? (
        <FactoryLoadError
          error={spans.error}
          updatedAt={0}
          onRetry={() => void spans.refetch()}
          what="the timeline"
          className="mb-0"
        />
      ) : groups.length === 0 ? (
        <p data-testid="pr-timeline-empty" className="text-sm text-muted-foreground">
          Nothing recorded for this PR yet: its rounds, gates and waits show here as they happen.
        </p>
      ) : (
        <TimelineView
          factoryId={fid}
          groups={groups}
          from={from}
          to={to}
          testId="pr-timeline-view"
        />
      )}
    </section>
  );
}
