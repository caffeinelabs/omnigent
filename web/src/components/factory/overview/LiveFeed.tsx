/**
 * What the factory is doing right now, newest first: what each working agent
 * is running (when the server reports it) and the latest factory events.
 * Freeze holds the list still while reading; every line opens its page.
 */

import { useMemo, useState } from "react";
import { PauseIcon, PlayIcon, RadioIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useFactoryEvents } from "@/hooks/useFactory";
import type { EventView, FactorySnapshot, MemberView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { kindPill } from "../activity/activityModel";
import { useFactoryLive } from "../board/factoryLive";
import { FactoryLoadError } from "../FactoryLoadError";
import { activityPageHref, agentPageHref, prPageHref, projectPageHref } from "../factoryRoutes";
import { SectionCard } from "../project/projectParts";

/** Lines the feed shows. */
export const LIVE_FEED_LINES = 12;

const FEED_FILTERS = { limit: 40 };

export function clock(ms: number): string {
  // 24-hour in every locale: the column fits `08:29:48`, not `08:29:48 AM`.
  return new Date(ms).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** Where a feed line goes: its PR, its project, else the Activity page. */
export function eventHref(snapshot: FactorySnapshot, event: EventView): string {
  const fid = snapshot.factory.id;
  const subject = event.subject ?? "";
  if (subject.startsWith("pr:")) return prPageHref(fid, subject.slice(3));
  if (event.project_slug && snapshot.projects.some((p) => p.slug === event.project_slug)) {
    return projectPageHref(fid, event.project_slug, { section: "activity" });
  }
  return activityPageHref(fid);
}

/** Members whose "now" the server reports while they run (round-3 servers). */
export function nowLines(members: readonly MemberView[]): MemberView[] {
  return members
    .filter((m) => m.running && m.now?.tool)
    .sort((a, b) => (b.now?.tool_since ?? 0) - (a.now?.tool_since ?? 0));
}

const SEVERITY_TONE: Record<EventView["severity"], string> = {
  info: "text-muted-foreground",
  notice: "text-foreground",
  warning: "text-warning",
  critical: "text-destructive",
};

export function LiveFeed({ snapshot }: { snapshot: FactorySnapshot }) {
  const live = useFactoryLive();
  const [frozen, setFrozen] = useState<EventView[] | null>(null);
  const query = useFactoryEvents(snapshot.factory.id, FEED_FILTERS);
  const latest = useMemo(
    () =>
      (query.data?.pages[0]?.data ?? [])
        .filter((e) => !e.kind.startsWith("heartbeat."))
        .slice(0, LIVE_FEED_LINES),
    [query.data],
  );
  const events = frozen ?? latest;
  const running = nowLines(snapshot.members);
  return (
    <SectionCard
      title="Live"
      meta={live ? "as it happens" : "reconnecting"}
      testId="overview-live-feed"
      className="gap-2"
      action={
        <Button
          variant="ghost"
          size="xs"
          onClick={() => setFrozen(frozen ? null : latest)}
          aria-pressed={frozen != null}
          data-testid="live-feed-freeze"
          componentId="factory.overview.feed_freeze"
          className="text-muted-foreground"
        >
          {frozen ? <PlayIcon /> : <PauseIcon />}
          {frozen ? "Resume" : "Freeze"}
        </Button>
      }
    >
      {running.length > 0 && (
        <ul data-testid="live-feed-now" className="flex flex-col gap-1 border-b border-border pb-2">
          {running.map((member) => (
            <li key={member.session_id} className="flex min-w-0 items-center gap-2 text-sm">
              <RadioIcon aria-hidden className="size-3.5 shrink-0 text-session-active" />
              <Link
                to={agentPageHref(snapshot.factory.id, member.session_id)}
                componentId="factory.overview.feed_now"
                className="shrink-0 truncate font-medium hover:underline"
              >
                {member.title}
              </Link>
              <span className="min-w-0 truncate font-mono text-muted-foreground">
                {member.now?.tool}
                {member.now?.tool_arg ? ` ${member.now.tool_arg}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
      {query.error instanceof Error && !query.data && (
        <FactoryLoadError
          error={query.error}
          updatedAt={0}
          onRetry={() => void query.refetch()}
          what="the live feed"
          testId="live-feed-error"
          className="mb-0"
        />
      )}
      {query.isLoading && (
        <div aria-busy="true" aria-label="Loading the live feed" className="flex flex-col gap-1.5">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="h-4 animate-pulse rounded bg-muted" />
          ))}
        </div>
      )}
      {query.data && events.length === 0 && (
        <p data-testid="live-feed-empty" className="text-sm text-muted-foreground">
          Quiet: nothing has happened yet.
        </p>
      )}
      {events.length > 0 && (
        <ol data-testid="live-feed-lines" className="flex flex-col">
          {events.map((event) => (
            <li key={event.id}>
              <Link
                to={eventHref(snapshot, event)}
                componentId="factory.overview.feed_line"
                className="-mx-1.5 grid min-w-0 grid-cols-[4.25rem_minmax(0,1fr)] items-baseline gap-x-2 rounded-md px-1.5 py-0.5 text-sm hover:bg-muted"
              >
                <span className="font-mono text-xs whitespace-nowrap text-muted-foreground tabular-nums">
                  {clock(event.ts)}
                </span>
                <span className="min-w-0 truncate" title={event.summary}>
                  <span className={cn("mr-1.5 font-mono text-xs", SEVERITY_TONE[event.severity])}>
                    {kindPill(event)}
                  </span>
                  {event.summary}
                </span>
              </Link>
            </li>
          ))}
        </ol>
      )}
    </SectionCard>
  );
}
