/**
 * The Insights page's charts. Day series are recharts bars in the usage
 * page's style (no grid, muted ticks, primary fill, a popover tooltip) with
 * version markers as dashed reference lines; breakdowns are dense meter
 * rows (label, bar, value, n) and stacked rows, which stay readable at
 * phone width where an axis chart would not.
 */

import type { ReactNode } from "react";
import { Bar, BarChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Payload } from "recharts/types/component/DefaultTooltipContent";
import { cn } from "@/lib/utils";
import { formatValue, humanize } from "./insightsModel";

function dayLabel(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  return `${d.toLocaleString(undefined, { month: "short", timeZone: "UTC" })} ${d.getUTCDate()}`;
}

function DayTooltip({
  active,
  payload,
  label,
  unit,
}: {
  active?: boolean;
  payload?: readonly Payload<number, string>[];
  label?: string;
  unit: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-1.5 text-sm shadow-md">
      <p className="text-muted-foreground">{label}</p>
      <p className="font-medium tabular-nums">{formatValue(payload[0]!.value ?? null, unit)}</p>
    </div>
  );
}

export interface DayMark {
  key: string;
  day: string;
  label: string;
}

const NO_DAY_MARKS: readonly DayMark[] = [];

/** One value per UTC day, with version markers on the days they started. */
export function DayBarChart({
  days,
  values,
  unit,
  marks = NO_DAY_MARKS,
  height = 160,
  animate = true,
  testId,
  empty = "No data in this window",
}: {
  days: readonly string[];
  values: readonly (number | null)[];
  unit: string;
  marks?: readonly DayMark[];
  height?: number;
  animate?: boolean;
  testId?: string;
  empty?: string;
}) {
  const data = days.map((day, i) => ({ label: dayLabel(day), day, value: values[i] ?? 0 }));
  if (!values.some((v) => v != null && v !== 0)) {
    return (
      <div
        data-testid={testId}
        data-empty="true"
        className="flex items-center justify-center text-sm text-muted-foreground"
        style={{ height: Math.min(height, 96) }}
      >
        {empty}
      </div>
    );
  }
  return (
    <div data-testid={testId} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 18, right: 8, bottom: 0, left: 0 }}>
          <XAxis
            dataKey="label"
            tick={{ fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
            className="fill-muted-foreground"
          />
          <YAxis
            tick={{ fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v: number) => formatValue(v, unit)}
            width={52}
            className="fill-muted-foreground"
          />
          <Tooltip
            content={<DayTooltip unit={unit} />}
            cursor={{ fill: "var(--accent)", opacity: 0.3 }}
          />
          {marks.map((mark) => (
            <ReferenceLine
              key={mark.key}
              x={dayLabel(mark.day)}
              stroke="var(--muted-foreground)"
              strokeDasharray="3 3"
              strokeOpacity={0.6}
              label={{
                value: mark.label,
                position: "top",
                fontSize: 10,
                fill: "var(--muted-foreground)",
              }}
            />
          ))}
          <Bar
            dataKey="value"
            fill="var(--chart-1)"
            radius={[3, 3, 0, 0]}
            isAnimationActive={animate}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// One grid per list (rows are subgrids): every track starts and ends at the same
// x, so equal values draw equal bars whatever the width of their value labels.
const LIST_GRID =
  "grid min-w-0 grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1 text-sm max-sm:grid-cols-[minmax(0,6.5rem)_minmax(0,1fr)_auto]";
const ROW_SUBGRID = "col-span-3 grid min-w-0 grid-cols-subgrid items-center";

export interface MeterRow {
  key: string;
  label?: ReactNode;
  value: number | null;
  /** A second number after the value, e.g. p90. */
  extra?: string | null;
  n?: number | null;
}

/**
 * Label, bar, value and n per row. Shares fill against 100 %; other units
 * against the largest row.
 */
export function MeterList({
  rows,
  unit,
  testId,
  empty = "No data",
  tone = "bg-chart-1",
}: {
  rows: readonly MeterRow[];
  unit: string;
  testId?: string;
  empty?: string;
  tone?: string;
}) {
  if (rows.length === 0) {
    return (
      <p data-testid={testId} data-empty="true" className="text-sm text-muted-foreground">
        {empty}
      </p>
    );
  }
  const max = unit === "share" ? 1 : Math.max(...rows.map((r) => r.value ?? 0), 0) || 1;
  return (
    <ul data-testid={testId} className={LIST_GRID}>
      {rows.map((row) => (
        <li
          key={row.key}
          data-testid={testId ? `${testId}-${row.key}` : undefined}
          className={ROW_SUBGRID}
        >
          <span className="min-w-0 truncate text-muted-foreground" title={row.key}>
            {row.label ?? humanize(row.key)}
          </span>
          <span aria-hidden className="h-1.5 min-w-0 overflow-hidden rounded-full bg-muted">
            <span
              className={cn("block h-full rounded-full", tone)}
              style={{ width: `${Math.max(2, ((row.value ?? 0) / max) * 100)}%` }}
            />
          </span>
          <span className="text-right whitespace-nowrap tabular-nums">
            {formatValue(row.value, unit)}
            {row.extra && <span className="text-muted-foreground"> / {row.extra}</span>}
            {row.n != null && (
              <span className="ml-1.5 text-xs text-muted-foreground">n={row.n}</span>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

export interface StackPart {
  key: string;
  label: string;
  /** A background utility, e.g. `bg-chart-1`. */
  tone: string;
}

/** One stacked bar per row (where a project's time went), with a legend. */
export function StackedRows({
  rows,
  parts,
  unit,
  testId,
  empty = "No data",
}: {
  rows: readonly { key: string; label: string; values: Record<string, number | null> }[];
  parts: readonly StackPart[];
  unit: string;
  testId?: string;
  empty?: string;
}) {
  if (rows.length === 0) {
    return (
      <p data-testid={testId} data-empty="true" className="text-sm text-muted-foreground">
        {empty}
      </p>
    );
  }
  const total = (r: (typeof rows)[number]) =>
    parts.reduce((sum, p) => sum + (r.values[p.key] ?? 0), 0);
  const max = Math.max(...rows.map(total), 0) || 1;
  return (
    <div data-testid={testId} className="flex min-w-0 flex-col gap-2">
      <ul className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
        {parts.map((p) => (
          <li key={p.key} className="inline-flex items-center gap-1">
            <span aria-hidden className={cn("size-2 rounded-sm", p.tone)} />
            {p.label}
          </li>
        ))}
      </ul>
      <ul className={LIST_GRID}>
        {rows.map((row) => (
          <li
            key={row.key}
            data-testid={testId ? `${testId}-${row.key}` : undefined}
            className={ROW_SUBGRID}
          >
            <span className="min-w-0 truncate text-muted-foreground" title={row.label}>
              {row.label}
            </span>
            <span
              className="flex h-2 min-w-0 overflow-hidden rounded-full bg-muted"
              title={parts
                .map((p) => `${p.label} ${formatValue(row.values[p.key] ?? null, unit)}`)
                .join(" · ")}
            >
              {parts.map((p) => (
                <span
                  key={p.key}
                  className={cn("h-full", p.tone)}
                  style={{ width: `${((row.values[p.key] ?? 0) / max) * 100}%` }}
                />
              ))}
            </span>
            <span className="text-right whitespace-nowrap tabular-nums">
              {formatValue(total(row), unit)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A compact label → number pair in a section's stat grid. */
export function Stat({
  label,
  value,
  hint,
  testId,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  testId?: string;
  tone?: string;
}) {
  return (
    <div data-testid={testId} className="flex min-w-0 flex-col">
      <span className="truncate text-xs text-muted-foreground">{label}</span>
      <span className={cn("text-ui font-medium tabular-nums", tone)}>{value}</span>
      {hint && <span className="truncate text-xs text-muted-foreground">{hint}</span>}
    </div>
  );
}
