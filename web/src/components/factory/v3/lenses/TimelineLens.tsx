// The Timeline lens: one lane per child over the day. Work is a solid bar
// (blue while the node still works, green once it is done), a wait is hatched
// (grey when it waits on another node or the server, amber when a person is
// the cause), and the stretch since this viewer's last look is shaded. Lanes
// are rows of boxes: nothing is drawn between them, and there is no SVG.

import type { CSSProperties } from "react";
import { cn } from "@/lib/utils";
import { NodeLink } from "../nodeParts";
import { STATE_HUE, STATE_WORDS } from "../nodeTokens";
import type { TimelineBar, TimelineView } from "../nodeTypes";

const MIN = 60_000;
/** Tick steps, in minutes: the first that gives at most five ticks across the window. */
const STEPS = [15, 30, 60, 120, 180, 360, 720, 1440];

const KIND_WORDS: Record<TimelineBar["kind"], string> = {
  work: "working",
  done: "worked, now done",
  wait: "waiting on another node",
  you: "waiting on you",
};

const HATCH_GREY = "var(--border-strong)";
const HATCH_AMBER = "color-mix(in srgb, var(--status-yellow) 45%, transparent)";

/** A bar's look: solid for work, hatched for waits (amber when a person is the cause). */
export function barStyle(kind: TimelineBar["kind"]): CSSProperties {
  switch (kind) {
    case "work":
      return { background: STATE_HUE.moving };
    case "done":
      return { background: STATE_HUE.done };
    case "you":
      return {
        background: `repeating-linear-gradient(135deg, ${HATCH_AMBER} 0 4px, transparent 4px 8px)`,
        boxShadow: `inset 0 0 0 1px ${STATE_HUE.needs}`,
      };
    default:
      return {
        background: `repeating-linear-gradient(135deg, ${HATCH_GREY} 0 4px, transparent 4px 8px)`,
        boxShadow: `inset 0 0 0 1px ${HATCH_GREY}`,
      };
  }
}

function ms(iso: string): number {
  return Date.parse(iso);
}

/** "13:40" on this viewer's clock. */
export function clockWords(at: number | string): string {
  const t = typeof at === "number" ? new Date(at) : new Date(at);
  if (Number.isNaN(t.getTime())) return String(at);
  return t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

/** Round-minute ticks inside (from, to), at most five, none crowding the "now" label. */
export function timelineTicks(from: number, to: number): number[] {
  const span = to - from;
  if (!(span > 0)) return [];
  const step = (STEPS.find((m) => span / (m * MIN) <= 5) ?? STEPS.at(-1)!) * MIN;
  // Align on this viewer's clock (whole local hours, quarters, days).
  const offset = -new Date(from).getTimezoneOffset() * MIN;
  const out: number[] = [];
  for (let t = Math.ceil((from + offset) / step) * step - offset; t < to; t += step) {
    if ((to - t) / span > 0.12) out.push(t);
  }
  return out;
}

/** "today", or the day the window starts ("Nov 2"). */
export function dayWords(from: number, now: Date = new Date()): string {
  const d = new Date(from);
  if (d.toDateString() === now.toDateString()) return "today";
  return `since ${d.toLocaleDateString([], { month: "short", day: "numeric" })}`;
}

/** What the section's heading says under the title: the window, and what is shaded. */
export function timelineSub(view: TimelineView): string {
  const [from] = view.axis.map(ms) as [number, number];
  const parts = [dayWords(from, new Date(ms(view.now)))];
  if (view.last_look) parts.push(`shaded: since ${clockWords(view.last_look)}`);
  return parts.join(" · ");
}

function pct(x: number): string {
  return `${(Math.max(0, Math.min(1, x)) * 100).toFixed(3)}%`;
}

export function TimelineLens({ view }: { view: TimelineView }) {
  const from = ms(view.axis[0]);
  const to = ms(view.axis[1]);
  const span = to - from;
  if (!(span > 0) || !view.lanes.length) return null;
  const at = (t: number) => (t - from) / span;
  const look = view.last_look ? ms(view.last_look) : null;
  const now = Math.min(ms(view.now), to);
  const band =
    look != null && look < now ? { left: pct(at(look)), width: pct(at(now) - at(look)) } : null;
  const kinds = new Set(view.lanes.flatMap((l) => l.bars.map((b) => b.kind)));
  const ticks = timelineTicks(from, to);
  return (
    <div
      data-testid="v3-timeline"
      className="flex flex-col gap-2 rounded-xl bg-card-solid p-3.5 ring-1 ring-border"
    >
      <div
        aria-hidden
        className="grid grid-cols-[minmax(0,1fr)] gap-x-3 sm:grid-cols-[minmax(0,168px)_minmax(0,1fr)]"
      >
        <span className="max-sm:hidden" />
        <span className="relative h-4 text-[11px] text-foreground-tertiary tabular-nums">
          {ticks.map((t) => (
            <span
              key={t}
              // A tick at the very start sits inside the track rather than half outside it.
              className={cn("absolute top-0 whitespace-nowrap", at(t) > 0.04 && "-translate-x-1/2")}
              style={{ left: pct(at(t)) }}
            >
              {clockWords(t)}
            </span>
          ))}
          <span className="absolute top-0 right-0 whitespace-nowrap">
            <span className="max-sm:hidden">now </span>
            {clockWords(view.now)}
          </span>
        </span>
      </div>
      <ul className="flex flex-col gap-2.5 sm:gap-2" aria-label="Lanes">
        {view.lanes.map((lane) => (
          <li
            key={lane.ref}
            data-testid="v3-timeline-lane"
            data-ref={lane.ref}
            className="grid grid-cols-[minmax(0,1fr)] items-center gap-x-3 gap-y-1 sm:grid-cols-[minmax(0,168px)_minmax(0,1fr)]"
          >
            <span className="flex min-w-0 items-baseline gap-1.5 sm:flex-col sm:gap-0">
              <NodeLink
                nodeRef={lane.ref}
                roving
                className="min-w-0 overflow-hidden text-[13px] font-medium text-ellipsis whitespace-nowrap text-foreground hover:text-status-blue-fg"
              >
                {lane.title}
              </NodeLink>
              <small className="shrink-0 text-[11.5px] text-foreground-tertiary">
                {STATE_WORDS[lane.state] ?? lane.state}
              </small>
            </span>
            <span className="relative h-[22px] min-w-0 overflow-hidden rounded-[5px] bg-muted/50">
              {band && (
                <span
                  aria-hidden
                  data-testid="v3-timeline-since"
                  className="absolute inset-y-0 bg-status-blue/12"
                  style={band}
                />
              )}
              {lane.bars.map((bar) => {
                const a = Math.max(ms(bar.from), from);
                const b = Math.min(ms(bar.to), to);
                if (!(b > a)) return null;
                const words = `${clockWords(a)}–${clockWords(b)} · ${bar.label} · ${KIND_WORDS[bar.kind]}`;
                return (
                  <span
                    key={`${bar.from}-${bar.kind}`}
                    role="img"
                    aria-label={words}
                    title={words}
                    data-kind={bar.kind}
                    className={cn(
                      "absolute inset-y-[2px] min-w-[3px] overflow-hidden rounded-[4px] px-1.5 text-[11px] leading-[18px] font-medium text-ellipsis whitespace-nowrap",
                      bar.kind === "work" || bar.kind === "done"
                        ? "text-white"
                        : bar.kind === "you"
                          ? "text-status-yellow-fg"
                          : "text-muted-foreground",
                    )}
                    style={{ left: pct(at(a)), width: pct(at(b) - at(a)), ...barStyle(bar.kind) }}
                  >
                    {bar.kind === "wait" || bar.kind === "you" ? (
                      // Words over a hatch sit on the card's colour, so they stay readable.
                      <span className="rounded-[3px] bg-card-solid/90 px-1">{bar.label}</span>
                    ) : (
                      bar.label
                    )}
                  </span>
                );
              })}
            </span>
          </li>
        ))}
      </ul>
      <div
        data-testid="v3-timeline-legend"
        className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-foreground-tertiary"
      >
        {(Object.keys(KIND_WORDS) as TimelineBar["kind"][])
          .filter((k) => kinds.has(k))
          .map((k) => (
            <span key={k} className="inline-flex items-center gap-1.5">
              <i aria-hidden className="inline-block h-2 w-3.5 rounded-[2px]" style={barStyle(k)} />
              {KIND_WORDS[k]}
            </span>
          ))}
        {band && view.last_look && (
          <span className="inline-flex items-center gap-1.5">
            <i aria-hidden className="inline-block h-2 w-3.5 rounded-[2px] bg-status-blue/20" />
            since your last look ({clockWords(view.last_look)})
          </span>
        )}
      </div>
    </div>
  );
}
