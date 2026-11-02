/**
 * An agent's last hours as one strip: working, waiting on checks or a
 * preview, waiting on a person, stuck (hatched) and idle, with hover text per
 * piece, an axis, a legend, and a written equivalent for screen readers.
 * Sparklines of tool calls and spend per bucket sit under it.
 */

import type { CSSProperties } from "react";
import type { StripPiece, StripState, TimelineBucket } from "@/lib/factoryTeamsApi";
import { STUCK_ORANGE } from "./MemberRing";
import { shortSpan } from "./agentSentence";

export const STRIP_WORD: Record<StripState, string> = {
  working: "working",
  checks: "waiting on checks or preview",
  waiting: "waiting on a person",
  stuck: "stuck",
  idle: "idle",
};

const HATCH = `repeating-linear-gradient(135deg, ${STUCK_ORANGE} 0 3px, transparent 3px 6px)`;

export const STRIP_STYLE: Record<StripState, CSSProperties> = {
  working: { background: "var(--status-green)" },
  checks: { background: "var(--status-blue)" },
  waiting: { background: "var(--status-yellow)" },
  stuck: { background: HATCH },
  idle: { background: "var(--muted)" },
};

const LEGEND: readonly StripState[] = ["working", "checks", "waiting", "stuck", "idle"];

/** `working 3 h, idle 20 h, stuck 1 h` (largest first; under a minute left out). */
export function stripSummary(totals: Partial<Record<StripState, number>>): string {
  const parts = Object.entries(totals)
    .filter(([, s]) => (s ?? 0) >= 60)
    .sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0))
    .map(([state, s]) => `${STRIP_WORD[state as StripState]} ${shortSpan(s ?? 0)}`);
  return parts.join(", ") || "nothing recorded";
}

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function AgentStateStrip({
  pieces,
  sinceS,
  untilS,
  windowLabel,
  totals,
}: {
  pieces: readonly StripPiece[];
  sinceS: number;
  untilS: number;
  windowLabel: string;
  totals: Partial<Record<StripState, number>>;
}) {
  const span = Math.max(1, (untilS - sinceS) * 1000);
  return (
    <div className="flex flex-col gap-1.5" data-testid="agent-state-strip">
      <div
        role="img"
        aria-label={`Last ${windowLabel}: ${stripSummary(totals)}`}
        className="flex h-3.5 w-full overflow-hidden rounded-sm bg-muted"
      >
        {pieces.map((piece) => (
          <span
            key={`${piece.state}-${piece.start}`}
            data-piece={piece.state}
            title={`${STRIP_WORD[piece.state]} · ${clock(piece.start)}–${clock(piece.end)} (${shortSpan((piece.end - piece.start) / 1000)})`}
            className="h-full"
            style={{
              ...STRIP_STYLE[piece.state],
              width: `${((piece.end - piece.start) / span) * 100}%`,
            }}
          />
        ))}
      </div>
      <div aria-hidden className="flex justify-between text-xs text-muted-foreground">
        <span>−{windowLabel}</span>
        <span>now</span>
      </div>
      <ul
        className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground"
        aria-label="Legend"
      >
        {LEGEND.map((state) => (
          <li key={state} className="inline-flex items-center gap-1">
            <span
              aria-hidden
              className="inline-block size-2.5 rounded-sm"
              style={STRIP_STYLE[state]}
            />
            {STRIP_WORD[state]}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A tiny area chart of one series (one data hue); its numbers are written beside it. */
export function Sparkline({
  values,
  label,
  className,
}: {
  values: readonly number[];
  label: string;
  className?: string;
}) {
  const max = Math.max(...values, 0);
  const n = values.length;
  const points = values.map((v, i) => {
    const x = n > 1 ? (i / (n - 1)) * 200 : 100;
    const y = 44 - (max > 0 ? (v / max) * 40 : 0);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const line = points.length ? `M${points.join(" L")}` : "";
  return (
    <svg
      viewBox="0 0 200 46"
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
      className={className ?? "h-10 w-full"}
    >
      {line && (
        <>
          <path d={`${line} L200,46 L0,46 Z`} fill="var(--status-blue)" opacity={0.12} />
          <path
            d={line}
            fill="none"
            stroke="var(--status-blue)"
            strokeWidth={1.6}
            vectorEffect="non-scaling-stroke"
          />
        </>
      )}
    </svg>
  );
}

/** `42 tool calls · $3.10 spent` over the series. */
export function seriesSummary(series: readonly TimelineBucket[]): {
  calls: number;
  usd: number;
} {
  return {
    calls: series.reduce((a, b) => a + b.tool_calls, 0),
    usd: series.reduce((a, b) => a + b.cost_usd, 0),
  };
}
