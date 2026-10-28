/**
 * A tiny trend line in the current text color: a line over a faint area,
 * gaps where a bucket has no data, and optional version markers as dashed
 * ticks. Decorative next to its number, so it is hidden from screen readers
 * unless it has a label.
 */

import { cn } from "@/lib/utils";

export interface SparkMark {
  key: string;
  /** 0..1 along the series. */
  at: number;
  title?: string;
}

function points(series: readonly (number | null)[], width: number, height: number) {
  const values = series.filter((v): v is number => v != null);
  const max = Math.max(...values, 0);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const step = series.length > 1 ? width / (series.length - 1) : 0;
  return series.map((v, i) =>
    v == null ? null : { x: i * step, y: height - 1 - ((v - min) / span) * (height - 2) },
  );
}

const NO_MARKS: readonly SparkMark[] = [];

export function Sparkline({
  series,
  marks = NO_MARKS,
  width = 96,
  height = 24,
  label,
  className,
  testId,
}: {
  series: readonly (number | null)[];
  marks?: readonly SparkMark[];
  width?: number;
  height?: number;
  label?: string;
  className?: string;
  testId?: string;
}) {
  const pts = points(series, width, height);
  const known = pts.filter((p): p is { x: number; y: number } => p != null);
  // Runs of consecutive known points; a null bucket breaks the line.
  const runs: { x: number; y: number }[][] = [];
  let run: { x: number; y: number }[] = [];
  for (const p of pts) {
    if (p) run.push(p);
    else if (run.length) {
      runs.push(run);
      run = [];
    }
  }
  if (run.length) runs.push(run);
  return (
    <svg
      data-testid={testId}
      data-points={known.length}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      preserveAspectRatio="none"
      className={cn("shrink-0 overflow-visible", className)}
    >
      {marks.map((mark) => (
        <line
          key={mark.key}
          x1={mark.at * width}
          x2={mark.at * width}
          y1={0}
          y2={height}
          stroke="currentColor"
          strokeOpacity={0.35}
          strokeDasharray="2 2"
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        >
          {mark.title && <title>{mark.title}</title>}
        </line>
      ))}
      {runs.map((r) =>
        r.length === 1 ? (
          <circle key={`dot-${r[0]!.x}`} cx={r[0]!.x} cy={r[0]!.y} r={1.5} fill="currentColor" />
        ) : (
          <g key={`run-${r[0]!.x}`}>
            <path
              d={`M${r[0]!.x},${height} ${r.map((p) => `L${p.x},${p.y}`).join(" ")} L${r[r.length - 1]!.x},${height} Z`}
              fill="currentColor"
              fillOpacity={0.1}
            />
            <polyline
              points={r.map((p) => `${p.x},${p.y}`).join(" ")}
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
              strokeLinejoin="round"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />
          </g>
        ),
      )}
    </svg>
  );
}

/** The known total of a series (null buckets left out). */
export function sparkTotal(series: readonly (number | null)[]): number {
  return series.reduce<number>((sum, v) => sum + (v ?? 0), 0);
}

/**
 * Tiny bars, one per bucket (the health strip's hours): a busy hour stands
 * out, a quiet one stays empty, and an unknown one (`null`) is a faint stub.
 */
export function SparkBars({
  series,
  width = 36,
  height = 12,
  label,
  className,
  testId,
}: {
  series: readonly (number | null)[];
  width?: number;
  height?: number;
  label?: string;
  className?: string;
  testId?: string;
}) {
  const max = Math.max(...series.map((v) => v ?? 0), 0) || 1;
  const step = series.length ? width / series.length : width;
  const bar = Math.max(1, step - 0.5);
  return (
    <svg
      data-testid={testId}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={cn("shrink-0", className)}
    >
      {/* A faint box, so the day's frame reads as a chart even with one busy hour. */}
      <rect
        x={0}
        y={0}
        width={width}
        height={height}
        rx={2}
        fill="currentColor"
        fillOpacity={0.08}
      />
      {series
        .map((v, i) => ({ v, x: i * step }))
        .map(({ v, x }) => {
          if (v == null) {
            return (
              <rect
                key={x}
                x={x}
                y={height - 2}
                width={bar}
                height={2}
                fill="currentColor"
                fillOpacity={0.15}
              />
            );
          }
          if (v <= 0) return null;
          // At least 2px, so one event in an hour is still visible.
          const h = Math.max(2, (v / max) * height);
          return <rect key={x} x={x} y={height - h} width={bar} height={h} fill="currentColor" />;
        })}
    </svg>
  );
}
