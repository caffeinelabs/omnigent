/**
 * Who told whom what, newest first: one row per factory event (time, from,
 * kind, to, summary, delivery), virtualized and paged backwards. Filters for
 * project, member and kinds become the events query; heartbeats stay folded
 * into ♥ rows unless asked for. Used by the Activity tab and the project
 * drawer (fixed to its project).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  ArrowRightIcon,
  CheckIcon,
  ChevronRightIcon,
  ClockIcon,
  HeartPulseIcon,
  RefreshCwIcon,
  TriangleAlertIcon,
  WifiOffIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { FACTORY_FALLBACK_POLL_MS, useFactoryEvents } from "@/hooks/useFactory";
import type { EventFilters, EventView, MemberView, ProjectView } from "@/lib/factoryApi";
import { ApiError } from "@/lib/sessionsApi";
import { cn } from "@/lib/utils";
import { useFactoryLive } from "../board/factoryLive";
import {
  ACTIVITY_KIND_GROUPS,
  type ActivityRow,
  DEFAULT_KIND_GROUPS,
  type Delivery,
  type KindGroupId,
  buildActivityRows,
  fromLabel,
  kindPill,
  kindsQuery,
  toLabel,
} from "./activityModel";

const ALL = "__all__";

/**
 * Older pages load on their own while the list's end is in view, up to this
 * many in a row; then a scroll (or the button) asks for more. Folded
 * heartbeats can leave a page adding no rows at all.
 */
const AUTO_PAGES = 3;

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

const SEVERITY_PILL: Record<EventView["severity"], string> = {
  info: "bg-muted text-muted-foreground",
  notice: "bg-session-active/10 text-session-active",
  warning: "bg-warning/15 text-warning",
  critical: "bg-destructive/10 text-destructive",
};

function DeliveryState({ delivery }: { delivery: Delivery | null }) {
  if (!delivery) return null;
  if (delivery === "delivered") {
    return (
      <span
        data-delivery="delivered"
        className="inline-flex shrink-0 items-center gap-0.5 text-success"
        title="Delivered"
      >
        <CheckIcon aria-hidden className="size-3" />
        <span className="max-lg:sr-only">delivered</span>
      </span>
    );
  }
  if (delivery === "bounced") {
    return (
      <span
        data-delivery="bounced"
        className="inline-flex shrink-0 items-center gap-0.5 text-destructive"
        title="Undeliverable"
      >
        <TriangleAlertIcon aria-hidden className="size-3" />
        <span className="max-lg:sr-only">bounced</span>
      </span>
    );
  }
  return (
    <span
      data-delivery="queued"
      className="inline-flex shrink-0 items-center gap-0.5 text-muted-foreground"
      title="Queued"
    >
      <ClockIcon aria-hidden className="size-3" />
      <span className="max-lg:sr-only">queued</span>
    </span>
  );
}

function Chip({ children }: { children: string }) {
  return (
    <span
      className="max-w-40 shrink-0 truncate rounded-md border border-border px-1.5 text-sm leading-5"
      title={children}
    >
      {children}
    </span>
  );
}

function excerptOf(event: EventView): string | null {
  const data = event.data ?? {};
  for (const key of ["excerpt", "text", "note", "reason"]) {
    const value = data[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function EventDetails({ event }: { event: EventView }) {
  const excerpt = excerptOf(event);
  const json = event.data ? JSON.stringify(event.data, null, 2) : null;
  return (
    <div
      data-testid="activity-row-details"
      className="flex flex-col gap-1.5 pt-1.5 pb-1 pl-[4.75rem] text-sm max-md:pl-0"
    >
      {excerpt && <p className="whitespace-pre-wrap text-foreground/90">{excerpt}</p>}
      {event.subject && <p className="font-mono text-muted-foreground">{event.subject}</p>}
      {json && (
        <pre className="max-h-48 overflow-auto rounded-md bg-muted/60 p-2 font-mono text-[11px] leading-4 text-muted-foreground">
          {json.length > 4000 ? `${json.slice(0, 4000)}…` : json}
        </pre>
      )}
    </div>
  );
}

function EventRow({
  row,
  expanded,
  onToggle,
}: {
  row: Extract<ActivityRow, { type: "event" }>;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { event } = row;
  const to = toLabel(event);
  return (
    <div
      data-testid="activity-row"
      data-kind={event.kind}
      data-severity={event.severity}
      className="border-b border-border/60"
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        // Bleeds into the list's padding so the inset focus ring clears the row's content.
        className="-mx-1.5 flex w-[calc(100%+0.75rem)] min-w-0 cursor-pointer flex-wrap items-center gap-x-2 gap-y-0.5 rounded-sm px-1.5 py-1.5 text-left text-sm hover:bg-muted/40 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none focus-visible:ring-inset"
      >
        <ChevronRightIcon
          aria-hidden
          className={cn(
            "size-3 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none",
            expanded && "rotate-90",
          )}
        />
        <time
          className="w-16 shrink-0 font-mono text-muted-foreground tabular-nums"
          dateTime={new Date(event.ts).toISOString()}
        >
          {clock(event.ts)}
        </time>
        <Chip>{fromLabel(event)}</Chip>
        <span className="inline-flex shrink-0 items-center gap-1 text-muted-foreground">
          <span
            className={cn(
              "rounded-sm px-1 font-mono text-[11px] leading-4",
              SEVERITY_PILL[event.severity],
            )}
          >
            {kindPill(event)}
          </span>
          {to && <ArrowRightIcon aria-hidden className="size-3" />}
        </span>
        {to && <Chip>{to}</Chip>}
        <span className="min-w-0 flex-1 basis-40 truncate text-foreground/90" title={event.summary}>
          {event.summary}
        </span>
        <DeliveryState delivery={row.delivery} />
      </button>
      {expanded && <EventDetails event={event} />}
    </div>
  );
}

/**
 * What a ♥ row says, counting beats (one skipped event tallies several):
 * `"4 heartbeats · 1 delivered · 3 skipped · no change"`, or one outcome for
 * all of them (`"3 heartbeats skipped · no change"`).
 */
export function heartbeatText(row: Extract<ActivityRow, { type: "heartbeats" }>): string {
  const beats = row.beats === 1 ? "1 heartbeat" : `${row.beats} heartbeats`;
  const outcomes: [number, string][] = [
    [row.delivered, "delivered"],
    [row.skipped, "skipped · no change"],
    [row.failed, "failed"],
  ];
  const only = outcomes.find(([count]) => count === row.beats);
  if (only) return `${beats} ${only[1]}`;
  const parts = outcomes.filter(([count]) => count > 0).map(([count, what]) => `${count} ${what}`);
  return [beats, ...parts].join(" · ");
}

function HeartbeatRow({ row }: { row: Extract<ActivityRow, { type: "heartbeats" }> }) {
  const newest = row.events[0]!;
  return (
    <div
      data-testid="activity-heartbeats"
      data-count={row.beats}
      className="flex items-center gap-2 border-b border-border/60 py-1.5 pl-5 text-sm text-muted-foreground"
    >
      <time
        className="w-16 shrink-0 font-mono tabular-nums"
        dateTime={new Date(newest.ts).toISOString()}
      >
        {clock(newest.ts)}
      </time>
      <HeartPulseIcon
        aria-hidden
        className={cn("size-3.5 shrink-0", row.failed > 0 && "text-warning")}
      />
      <span className="truncate">{heartbeatText(row)}</span>
    </div>
  );
}

function RowSkeleton() {
  return (
    <div aria-hidden className="flex items-center gap-2 border-b border-border/60 py-2">
      <div className="h-3.5 w-14 animate-pulse rounded bg-muted" />
      <div className="h-3.5 w-20 animate-pulse rounded bg-muted" />
      <div className="h-3.5 w-12 animate-pulse rounded bg-muted" />
      <div className="h-3.5 flex-1 animate-pulse rounded bg-muted" />
    </div>
  );
}

export interface ActivityListProps {
  factoryId: string;
  projects: ProjectView[];
  members: MemberView[];
  /** Pin the list to one project (the project drawer); hides the project filter. */
  project?: string;
  /** Max height of the scrolling list (it virtualizes inside it). */
  heightClassName?: string;
}

export function ActivityList({
  factoryId,
  projects,
  members,
  project: fixedProject,
  heightClassName = "max-h-[min(70vh,44rem)]",
}: ActivityListProps) {
  const [projectFilter, setProjectFilter] = useState<string | null>(null);
  const [member, setMember] = useState<string | null>(null);
  const [groups, setGroups] = useState<ReadonlySet<KindGroupId>>(DEFAULT_KIND_GROUPS);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const connected = useFactoryLive();
  const project = fixedProject ?? projectFilter ?? undefined;
  const noKinds = groups.size === 0;
  const filters: EventFilters = useMemo(() => {
    const kinds = kindsQuery(groups);
    return {
      ...(project ? { project } : {}),
      ...(member ? { member } : {}),
      ...(kinds ? { kinds } : {}),
    };
  }, [project, member, groups]);
  // With every kind switched off there is nothing to ask for (an empty `kinds` means all).
  const query = useFactoryEvents(noKinds ? null : factoryId, filters);
  const events = useMemo(() => query.data?.pages.flatMap((p) => p.data) ?? [], [query.data]);
  const rows = useMemo(
    () => buildActivityRows(events, { foldHeartbeats: !groups.has("heartbeats") }),
    [events, groups],
  );
  const memberOptions = fixedProject ? members.filter((m) => m.project === fixedProject) : members;

  const scrollRef = useRef<HTMLDivElement>(null);
  const hasMore = query.hasNextPage === true;
  const virtualizer = useVirtualizer({
    count: rows.length + (hasMore ? 1 : 0),
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 33,
    overscan: 10,
    getItemKey: (index) => rows[index]?.key ?? "more",
  });
  const items = virtualizer.getVirtualItems();
  const lastIndex = items.at(-1)?.index ?? -1;
  const { fetchNextPage, isFetchingNextPage } = query;
  const pageCount = query.data?.pages.length ?? 0;
  // Auto-loads left before the reader has to scroll; a filter change or a scroll refills it.
  const autoPages = useRef(AUTO_PAGES);
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    autoPages.current = AUTO_PAGES;
    setPaused(false);
  }, [filters]);
  useEffect(() => {
    if (!hasMore || isFetchingNextPage || paused || lastIndex < rows.length - 1) return;
    if (autoPages.current <= 0) {
      setPaused(true);
      return;
    }
    autoPages.current -= 1;
    void fetchNextPage();
  }, [hasMore, isFetchingNextPage, paused, lastIndex, rows.length, pageCount, fetchNextPage]);
  const onScroll = () => {
    if (autoPages.current === AUTO_PAGES && !paused) return;
    autoPages.current = AUTO_PAGES;
    setPaused(false);
  };
  const loadOlder = () => void fetchNextPage();

  const toggleGroup = (id: KindGroupId) =>
    setGroups((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleRow = (key: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <section
      data-testid="activity-list"
      aria-label="Activity"
      className="flex min-w-0 flex-col gap-2"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        {!fixedProject && (
          <Select
            value={projectFilter ?? ALL}
            onValueChange={(v) => setProjectFilter(v === ALL ? null : v)}
          >
            <SelectTrigger
              size="sm"
              aria-label="Project"
              data-testid="activity-filter-project"
              className="min-w-36"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All projects</SelectItem>
              {projects.map((p) => (
                <SelectItem key={p.slug} value={p.slug}>
                  {p.slug}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Select value={member ?? ALL} onValueChange={(v) => setMember(v === ALL ? null : v)}>
          <SelectTrigger
            size="sm"
            aria-label="Member"
            data-testid="activity-filter-member"
            className="min-w-36"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All agents</SelectItem>
            {memberOptions.map((m) => (
              <SelectItem key={m.session_id} value={m.session_id}>
                {m.title}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div role="group" aria-label="Kinds" className="flex min-w-0 flex-wrap items-center gap-1">
          {ACTIVITY_KIND_GROUPS.map((group) => {
            const on = groups.has(group.id);
            return (
              <button
                key={group.id}
                type="button"
                aria-pressed={on}
                data-testid={`activity-kind-${group.id}`}
                onClick={() => toggleGroup(group.id)}
                className={cn(
                  "inline-flex h-6 cursor-pointer items-center rounded-md border px-1.5 text-sm transition-colors",
                  on
                    ? "border-border bg-muted text-foreground"
                    : "border-dashed border-border text-muted-foreground hover:text-foreground",
                )}
              >
                {group.label}
              </button>
            );
          })}
        </div>
      </div>

      {!connected && (
        <p className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
          <WifiOffIcon aria-hidden className="size-3.5" />
          Reconnecting… (polling every {FACTORY_FALLBACK_POLL_MS / 1000} s)
        </p>
      )}
      {query.error != null && (
        <p
          data-testid="activity-error"
          className="inline-flex items-center gap-2 text-sm text-destructive"
        >
          <TriangleAlertIcon aria-hidden className="size-3.5" />
          Couldn’t load activity
          {query.error instanceof ApiError && (
            <span className="text-muted-foreground">(HTTP {query.error.status})</span>
          )}
          <Button
            variant="outline"
            size="xs"
            onClick={() => void query.refetch()}
            componentId="factory.activity.retry"
          >
            <RefreshCwIcon />
            Retry
          </Button>
        </p>
      )}

      {noKinds ? (
        <p
          data-testid="activity-no-kinds"
          className="rounded-lg border border-dashed border-border py-8 text-center text-sm text-muted-foreground"
        >
          Every kind is switched off. Turn one on to see activity.
        </p>
      ) : query.error != null && rows.length === 0 ? null : (
        <div
          ref={scrollRef}
          data-testid="activity-scroll"
          onScroll={onScroll}
          className={cn(
            "min-w-0 overflow-y-auto rounded-lg border border-border bg-card px-3",
            heightClassName,
          )}
        >
          {query.isLoading && (
            <div aria-busy="true" aria-label="Loading activity">
              {Array.from({ length: 6 }, (_, i) => (
                <RowSkeleton key={i} />
              ))}
            </div>
          )}
          {!query.isLoading && rows.length === 0 && query.error == null && (
            <p
              data-testid="activity-empty"
              className="py-8 text-center text-sm text-muted-foreground"
            >
              No activity yet.
            </p>
          )}
          {rows.length > 0 && (
            <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
              {items.map((item) => {
                const row = rows[item.index];
                return (
                  <div
                    key={item.key}
                    data-index={item.index}
                    ref={virtualizer.measureElement}
                    className="absolute top-0 left-0 w-full"
                    style={{ transform: `translateY(${item.start}px)` }}
                  >
                    {!row ? (
                      paused && !isFetchingNextPage ? (
                        <div className="flex justify-center py-1.5">
                          <Button
                            variant="ghost"
                            size="xs"
                            onClick={loadOlder}
                            data-testid="activity-load-older"
                            componentId="factory.activity.load_older"
                          >
                            Load older activity
                          </Button>
                        </div>
                      ) : (
                        <RowSkeleton />
                      )
                    ) : row.type === "heartbeats" ? (
                      <HeartbeatRow row={row} />
                    ) : (
                      <EventRow
                        row={row}
                        expanded={expanded.has(row.key)}
                        onToggle={() => toggleRow(row.key)}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
