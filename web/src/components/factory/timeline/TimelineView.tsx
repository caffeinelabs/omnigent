/**
 * Swimlanes: a time axis, then per group (project) a lane per agent, loop,
 * PR and env with its bars: work, tools and turns (solid), waits (hatched;
 * amber for CI or review, rose for a human), attempts `r0 ✗` and rounds,
 * milestones `◆` (opened, gates, demo), interventions `▲` and incidents.
 * Every bar opens its page: an agent at that moment, a PR, a project
 * section. A vertical rule marks "as of" while replaying.
 */

import { type CSSProperties, memo, useCallback, useMemo, useRef } from "react";
import { useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { useElementWidth } from "../agents/useElementWidth";
import { agentPageHref, prPageHref, projectPageHref } from "../factoryRoutes";
import { clock } from "../overview/LiveFeed";
import {
  type Bar,
  type BarTarget,
  type Lane,
  type LaneGroup,
  position,
  ticks,
} from "./timelineModel";

const HATCH: Record<"warning" | "human", CSSProperties> = {
  warning: {
    backgroundImage:
      "repeating-linear-gradient(135deg, color-mix(in oklch, var(--warning) 45%, transparent) 0 3px, transparent 3px 7px)",
    boxShadow: "inset 0 0 0 1px color-mix(in oklch, var(--warning) 45%, transparent)",
  },
  human: {
    backgroundImage:
      "repeating-linear-gradient(135deg, color-mix(in oklch, var(--destructive) 35%, transparent) 0 3px, transparent 3px 7px)",
    boxShadow: "inset 0 0 0 1px color-mix(in oklch, var(--destructive) 40%, transparent)",
  },
};

const SOLID: Record<string, string> = {
  active: "bg-session-active/70 text-white",
  muted: "bg-muted-foreground/25 text-foreground",
  pass: "bg-success/70 text-white",
  fail: "bg-destructive/70 text-white",
  warning: "bg-warning/60 text-foreground",
  human: "bg-brand-accent/60 text-white",
};

const MILESTONE_TONE: Record<string, string> = {
  pass: "text-success",
  fail: "text-destructive",
  active: "text-session-active",
  muted: "text-muted-foreground",
  warning: "text-warning",
  human: "text-brand-accent",
};

export function targetHref(factoryId: string, target: BarTarget): string {
  switch (target.kind) {
    case "agent":
      return agentPageHref(factoryId, target.sessionId, { at: target.at });
    case "pr":
      return prPageHref(factoryId, target.prId);
    case "project":
      return projectPageHref(factoryId, target.slug, { section: target.section });
  }
}

/** Milestones closer than this to the next one draw only their glyph. */
const MILESTONE_GAP_PCT = 7;
/** A 10px label's width per character and its bar's padding, in px. */
const LABEL_CHAR_PX = 6;
const LABEL_PAD_PX = 10;

function isMark(bar: Bar): boolean {
  return (
    bar.kind === "milestone" ||
    bar.kind === "intervention" ||
    (bar.kind === "incident" && bar.end <= bar.start)
  );
}

function isThin(bar: Bar): boolean {
  return (
    bar.kind === "stage" || bar.kind === "heartbeat" || bar.kind === "round" || bar.kind === "turn"
  );
}

/**
 * The bars of a lane that draw their label, with where in the bar it starts
 * (px from the bar's left): the first stretch of the bar wide enough for it
 * that nothing painted above the bar (a later bar, a milestone, the "as of"
 * rule) covers. Every other bar keeps its label in the tooltip.
 *
 * @param trackPx - The lane's track width in px.
 */
export function labelledBars(
  bars: readonly Bar[],
  from: number,
  to: number,
  trackPx: number,
  replayAt: number | null = null,
): Map<string, number> {
  const px = (at: number) => (position(at, from, to) / 100) * trackPx;
  const out = new Map<string, number>();
  bars.forEach((bar, i) => {
    if (isMark(bar) || isThin(bar) || !bar.label) return;
    const left = px(bar.start);
    const right = Math.max(left, px(bar.end));
    const need = bar.label.length * LABEL_CHAR_PX + LABEL_PAD_PX;
    // What covers the bar, as [start, end] px, left to right.
    const covers: [number, number][] = [];
    if (replayAt != null) covers.push([px(replayAt) - 2, px(replayAt) + 2]);
    bars.forEach((other, j) => {
      if (other === bar || (j < i && !isMark(other))) return;
      const start = px(other.start);
      if (isMark(other)) covers.push([start - 6, start + 6]);
      else covers.push([start, Math.max(start + 1, px(other.end))]);
    });
    covers.sort((a, b) => a[0] - b[0]);
    let cursor = left;
    for (const [start, end] of covers) {
      if (end <= cursor || start >= right) continue;
      if (start - cursor >= need) break;
      cursor = Math.max(cursor, end);
    }
    if (right - cursor >= need) out.set(bar.id, Math.round(cursor - left));
  });
  return out;
}

/** Milestones whose label would run into the next one's. */
export function crowdedMilestones(bars: readonly Bar[], from: number, to: number): Set<string> {
  const marks = bars.filter((b) => b.end <= b.start).sort((a, b) => a.start - b.start);
  const out = new Set<string>();
  for (let i = 0; i < marks.length - 1; i += 1) {
    const gap = position(marks[i + 1]!.start, from, to) - position(marks[i]!.start, from, to);
    if (gap < MILESTONE_GAP_PCT) out.add(marks[i]!.id);
  }
  return out;
}

function BarView({
  bar,
  from,
  to,
  onOpen,
  quiet = false,
  labelAt = 0,
}: {
  bar: Bar;
  from: number;
  to: number;
  onOpen: (bar: Bar) => void;
  /** Draw no label (crowded, covered or too narrow). */
  quiet?: boolean;
  /** Where the label starts in the bar (px), clear of what covers it. */
  labelAt?: number;
}) {
  const left = position(bar.start, from, to);
  const title = `${bar.title} · ${clock(bar.start)}${bar.end > bar.start ? `–${bar.open ? "now" : clock(bar.end)}` : ""}`;
  const common = {
    "data-testid": `timeline-bar-${bar.kind}`,
    "data-bar": bar.id,
    "data-tone": bar.tone,
    title,
    "aria-label": title,
    onClick: bar.target ? () => onOpen(bar) : undefined,
  } as const;
  if (isMark(bar)) {
    const glyph = bar.kind === "intervention" ? "▲" : bar.kind === "incident" ? "✕" : "◆";
    // Past the middle the label reads leftwards, so it never runs off the frame.
    const leftward = left > 70;
    return (
      <button
        type="button"
        {...common}
        className={cn(
          "absolute top-1/2 z-[1] inline-flex cursor-pointer items-center gap-0.5 rounded-sm px-0.5 text-[10px] leading-none whitespace-nowrap hover:bg-muted",
          leftward ? "flex-row-reverse" : "",
          MILESTONE_TONE[bar.tone],
        )}
        style={{
          left: `${left}%`,
          transform: `translate(${leftward ? "calc(-100% + 0.4rem)" : "-0.4rem"}, -50%)`,
        }}
      >
        <span aria-hidden>{glyph}</span>
        {/* A chip of the card's colour keeps the label legible over any bar. */}
        {!quiet && (
          <span className="rounded-[2px] bg-card px-0.5 py-px max-md:hidden">{bar.label}</span>
        )}
      </button>
    );
  }
  const width = Math.max(0.35, position(bar.end, from, to) - left);
  const hatched =
    bar.kind === "wait" ? HATCH[bar.tone === "human" ? "human" : "warning"] : undefined;
  const thin = isThin(bar);
  return (
    <button
      type="button"
      {...common}
      className={cn(
        "absolute top-1/2 -translate-y-1/2 cursor-pointer overflow-hidden rounded-[3px] px-1 text-left text-[10px] leading-none whitespace-nowrap hover:ring-2 hover:ring-ring/40 focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:outline-none",
        thin ? "h-2.5" : "h-4",
        !hatched &&
          SOLID[bar.kind === "work" ? (bar.tone === "active" ? "muted" : bar.tone) : bar.tone],
        bar.kind === "work" && "bg-muted-foreground/20 text-foreground",
        bar.kind === "attempt" && "font-mono font-medium",
        bar.kind === "now" && "animate-pulse motion-reduce:animate-none",
        bar.kind === "env" && "opacity-80",
        hatched && "text-foreground",
      )}
      style={{ left: `${left}%`, width: `${width}%`, ...hatched }}
    >
      {!thin && !quiet && (
        <span
          className={cn(
            "pointer-events-none truncate",
            // Hatching shows through text: a chip keeps a wait's label legible.
            hatched && "rounded-[2px] bg-card px-0.5 py-px",
          )}
          style={labelAt > 0 ? { marginLeft: labelAt } : undefined}
        >
          {bar.label}
        </span>
      )}
    </button>
  );
}

const LaneRow = memo(function LaneRow({
  lane,
  from,
  to,
  marks,
  replayAt,
  trackPx,
  onOpen,
}: {
  lane: Lane;
  from: number;
  to: number;
  marks: number[];
  replayAt: number | null;
  trackPx: number;
  onOpen: (bar: Bar) => void;
}) {
  const crowded = useMemo(() => crowdedMilestones(lane.bars, from, to), [lane.bars, from, to]);
  const labelled = useMemo(
    () => labelledBars(lane.bars, from, to, trackPx, replayAt),
    [lane.bars, from, to, trackPx, replayAt],
  );
  return (
    <div
      role="row"
      data-testid={`timeline-lane-${lane.kind}`}
      data-lane={lane.key}
      className={cn(
        "grid min-w-0 grid-cols-[minmax(6.5rem,10rem)_minmax(0,1fr)] max-sm:grid-cols-[6.5rem_minmax(0,1fr)] items-center gap-2 border-b border-border/50 last:border-0",
        lane.kind === "stage" ? "h-5" : "h-6",
      )}
    >
      <span
        role="rowheader"
        className="flex min-w-0 items-baseline gap-1 pl-3 text-xs"
        title={lane.label}
      >
        {lane.sub && lane.kind === "agent" && (
          <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{lane.sub}</span>
        )}
        <span
          className={cn(
            "min-w-0 truncate",
            lane.kind === "agent" ? "font-medium" : "text-muted-foreground",
          )}
        >
          {lane.kind === "pr" || lane.kind === "loop" ? (
            <span className="font-mono">{lane.label}</span>
          ) : (
            lane.label
          )}
        </span>
      </span>
      <div role="cell" className="relative h-full min-w-0 pr-3">
        <div className="relative h-full">
          {marks.map((t) => (
            <span
              key={t}
              aria-hidden
              className="absolute inset-y-0 w-px bg-border/60"
              style={{ left: `${position(t, from, to)}%` }}
            />
          ))}
          {replayAt != null && (
            <span
              aria-hidden
              data-testid="timeline-asof"
              className="absolute inset-y-0 z-[2] w-0.5 bg-session-active"
              style={{ left: `${position(replayAt, from, to)}%` }}
            />
          )}
          {lane.bars.map((bar) => (
            <BarView
              key={bar.id}
              bar={bar}
              from={from}
              to={to}
              onOpen={onOpen}
              quiet={isMark(bar) ? crowded.has(bar.id) : !labelled.has(bar.id)}
              labelAt={labelled.get(bar.id) ?? 0}
            />
          ))}
          {lane.endMark && lane.bars.length > 0 && (
            <span
              aria-label="exhausted"
              className="absolute top-1/2 -translate-y-1/2 pl-0.5 text-[10px]"
              style={{ left: `${position(lane.bars[lane.bars.length - 1]!.end, from, to)}%` }}
            >
              {lane.endMark}
            </span>
          )}
        </div>
      </div>
    </div>
  );
});

export function TimelineView({
  factoryId,
  groups,
  from,
  to,
  replayAt,
  testId = "timeline-view",
}: {
  factoryId: string;
  groups: LaneGroup[];
  from: number;
  to: number;
  /** The "as of" rule while replaying. */
  replayAt?: number | null;
  testId?: string;
}) {
  const navigate = useNavigate();
  const onOpen = useCallback(
    (bar: Bar) => {
      if (bar.target) navigate(targetHref(factoryId, bar.target));
    },
    [navigate, factoryId],
  );
  const frame = useRef<HTMLDivElement>(null);
  const width = useElementWidth(frame);
  // About one tick per 80 px of track (the label column takes ~10rem).
  const count = width == null ? 6 : Math.max(2, Math.min(8, Math.floor((width - 160) / 80)));
  const marks = useMemo(() => ticks(from, to, count), [from, to, count]);
  // The track: the frame less the lane label column (6.5rem on a phone, else 10rem),
  // the gap and the right pad.
  const trackPx = width == null ? 600 : Math.max(0, width - (width < 600 ? 104 : 160) - 20);
  return (
    <div
      ref={frame}
      role="table"
      aria-label="Timeline"
      data-testid={testId}
      className="min-w-0 overflow-hidden rounded-xl border border-border bg-card"
    >
      <div
        role="row"
        className="sticky top-0 z-[2] grid h-7 grid-cols-[minmax(6.5rem,10rem)_minmax(0,1fr)] max-sm:grid-cols-[6.5rem_minmax(0,1fr)] items-end gap-2 border-b border-border bg-card"
      >
        <span role="columnheader" className="pb-1 pl-3 text-xs text-muted-foreground">
          {clock(from).slice(0, 5)}–{clock(to).slice(0, 5)}
        </span>
        <div role="columnheader" className="relative h-full pr-3">
          <div className="relative h-full">
            {marks.map((t) => {
              const at = position(t, from, to);
              // Ticks at the edges sit inside the frame rather than half outside it.
              const shift = at > 96 ? "-100%" : at < 4 ? "0" : "-50%";
              return (
                <span
                  key={t}
                  className="absolute bottom-1 font-mono text-[10px] text-muted-foreground tabular-nums"
                  style={{ left: `${at}%`, transform: `translateX(${shift})` }}
                >
                  {clock(t).slice(0, 5)}
                </span>
              );
            })}
          </div>
        </div>
      </div>
      <div className="relative">
        {groups.map((group) => (
          <div key={group.key} role="rowgroup" data-testid={`timeline-group-${group.key}`}>
            <div className="sticky top-7 z-[1] border-b border-border bg-muted/50 px-3 py-1 text-xs font-semibold">
              {group.label}
              <span className="ml-1.5 font-normal text-muted-foreground tabular-nums">
                {group.lanes.length}
              </span>
            </div>
            {group.lanes.map((lane) => (
              <LaneRow
                key={lane.key}
                lane={lane}
                from={from}
                to={to}
                marks={marks}
                replayAt={replayAt ?? null}
                trackPx={trackPx}
                onOpen={onOpen}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** The legend under the swimlanes. */
export function TimelineLegend() {
  const item = (swatch: React.ReactNode, text: string) => (
    <span className="inline-flex items-center gap-1.5">
      {swatch}
      {text}
    </span>
  );
  return (
    <p
      data-testid="timeline-legend"
      className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"
    >
      {item(
        <span className="h-2.5 w-4 rounded-[2px] bg-session-active/70" />,
        "tool / working now",
      )}
      {item(<span className="h-2.5 w-4 rounded-[2px] bg-muted-foreground/25" />, "turn · task")}
      {item(<span className="h-2.5 w-4 rounded-[2px]" style={HATCH.human} />, "waiting on a human")}
      {item(
        <span className="h-2.5 w-4 rounded-[2px]" style={HATCH.warning} />,
        "waiting on CI, review, env",
      )}
      {item(<span className="font-mono">r0 ✗</span>, "attempt")}
      {item(<span>⛔</span>, "loop exhausted")}
      {item(<span>◆</span>, "milestone")}
      {item(<span className="text-brand-accent">▲</span>, "human intervention")}
    </p>
  );
}
