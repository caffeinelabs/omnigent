/**
 * An agent's recent history as one strip of state segments (working, waiting
 * on checks or preview, waiting on you, stuck hatched, idle, stopped), with
 * an axis, a legend and a written equivalent. A strip built only from the
 * time someone had the cockpit open says it is partial.
 */

import { cn } from "@/lib/utils";
import { formatElapsed } from "../factoryFormat";
import { STRIP_FILL, STRIP_STATES, STRIP_WORD, type StripState } from "./statusTokens";

export interface StripSegment {
  state: StripState;
  seconds: number;
}

/** "Last 24 hours: idle 8h, working 3h 20m, stuck 1h" (partial when marked so). */
export function stateStripLabel(
  segments: readonly StripSegment[],
  windowLabel: string,
  partial = false,
): string {
  const parts = segments
    .filter((s) => s.seconds > 0)
    .map((s) => `${STRIP_WORD[s.state]} ${formatElapsed(s.seconds)}`);
  const body = parts.length ? parts.join(", ") : "no data";
  return `${windowLabel}: ${body}${partial ? " (partial)" : ""}`;
}

function hoursLabel(seconds: number): string {
  const h = seconds / 3600;
  return h >= 1 ? `−${Math.round(h)}h` : `−${Math.round(seconds / 60)}m`;
}

export interface StateStripProps {
  segments: readonly StripSegment[];
  /** Heading the label starts with ("Last 24 hours"). */
  windowLabel?: string;
  /** Only covers the time someone had the cockpit open. */
  partial?: boolean;
  /** Ticks under the strip (start, middle, now). */
  axis?: boolean;
  legend?: boolean;
  className?: string;
}

export function StateStrip({
  segments,
  windowLabel = "Last 24 hours",
  partial = false,
  axis = true,
  legend = true,
  className,
}: StateStripProps) {
  const shown = segments.filter((s) => s.seconds > 0);
  const total = shown.reduce((sum, s) => sum + s.seconds, 0);
  // Each segment's start offset: a stable, unique key.
  const starts = shown.map((_, i) => shown.slice(0, i).reduce((sum, s) => sum + s.seconds, 0));
  const present = new Set(shown.map((s) => s.state));
  return (
    <div className={cn("w-full", className)}>
      <div
        role="img"
        aria-label={stateStripLabel(segments, windowLabel, partial)}
        className="flex h-4 gap-px overflow-hidden rounded bg-status-gray/10"
      >
        {shown.map((s, i) => (
          <i
            key={starts[i]}
            data-state={s.state}
            title={`${STRIP_WORD[s.state]} for ${formatElapsed(s.seconds)}`}
            className="block h-full"
            style={{
              width: `${(s.seconds / (total || 1)) * 100}%`,
              background: STRIP_FILL[s.state],
            }}
          />
        ))}
      </div>
      {axis && total > 0 && (
        <div aria-hidden className="mt-1 flex justify-between text-sm text-muted-foreground">
          <span>{hoursLabel(total)}</span>
          <span>{hoursLabel(total / 2)}</span>
          <span>now</span>
        </div>
      )}
      {partial && (
        <p className="mt-1 text-sm text-muted-foreground">
          Partial: covers only the time someone had the cockpit open.
        </p>
      )}
      {legend && (
        <div
          aria-hidden
          className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground"
        >
          {STRIP_STATES.filter((s) => present.has(s) || s !== "dead").map((s) => (
            <span key={s} className="inline-flex items-center gap-1">
              <i
                className="inline-block size-[9px] rounded-[2px]"
                style={{ background: STRIP_FILL[s] }}
              />
              {STRIP_WORD[s]}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
