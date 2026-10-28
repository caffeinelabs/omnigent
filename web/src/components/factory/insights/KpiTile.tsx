/**
 * One Insights header tile: the KPI's number, its change against the
 * previous window, n, a sparkline over the window's days and the version
 * markers inside it. With n below 3 the number stays but reads "n=2 —
 * inconclusive" and the line is drawn faint: small samples never look like
 * a trend. Operators can "Propose change" from a tile.
 */

import type { ReactNode } from "react";
import type { MetricResult } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { TONE_TEXT } from "../factoryFormat";
import {
  type KpiSpec,
  deltaTone,
  formatDelta,
  formatValue,
  headline,
  humanize,
  nState,
  nText,
  promptBand,
  splitText,
} from "./insightsModel";
import { type SparkMark, Sparkline } from "./Sparkline";

export interface KpiView {
  primary: string;
  /** A second line under the number, e.g. the band or the closure mix. */
  sub: string | null;
  /** The number the delta and the sparkline follow. */
  value: number | null;
  /** The unit the delta formats in. */
  unit: string;
}

function byValue(metric: MetricResult, key: string): number | null {
  return headline(metric.by?.[key] ?? null);
}

/** What a tile shows for `metric`, per KPI. */
export function kpiView(spec: KpiSpec, metric: MetricResult | undefined): KpiView {
  if (!metric || metric.error) {
    return { primary: "—", sub: metric?.error ? "unavailable" : null, value: null, unit: "" };
  }
  const value = headline(metric);
  switch (spec.name) {
    case "merge_acceptance": {
      const merged = value != null ? Math.round(value * metric.n) : null;
      const closures = Object.entries((metric.closure_reasons as Record<string, number>) ?? {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 2)
        .map(([k, n]) => `${humanize(k)} ${n}`);
      return {
        primary: merged != null ? `${merged} · ${formatValue(value, "share")}` : "—",
        sub: closures.length ? `closed: ${closures.join(", ")}` : null,
        value,
        unit: "share",
      };
    }
    case "lead_time":
      return {
        primary:
          metric.p50 != null
            ? `${formatValue(metric.p50, "s")} / ${formatValue(metric.p90 ?? null, "s")}`
            : "—",
        sub: null,
        value,
        unit: "s",
      };
    case "human_wait": {
      const merge = byValue(metric, "human_merge");
      const decisions = byValue(metric, "decision");
      const agent = byValue(metric, "agent");
      return {
        primary: merge != null ? formatValue(merge, "s") : "—",
        sub: [
          "ready→merge",
          decisions != null ? `decisions ${formatValue(decisions, "s")}` : null,
          agent != null ? `agent ${formatValue(agent, "s")}` : null,
        ]
          .filter(Boolean)
          .join(" · "),
        value: merge,
        unit: "s",
      };
    }
    case "human_prompts_per_day": {
      const band = promptBand(metric);
      const split = splitText(band.split);
      return {
        primary: band.perDay != null ? `${formatValue(band.perDay, "per_day")}` : "—",
        sub: `band ${band.band[0]}–${band.band[1]}${split ? ` · ${split}` : ""}`,
        value: band.perDay,
        unit: "per_day",
      };
    }
    case "cost_per_merged_pr":
      return { primary: formatValue(value, "usd"), sub: null, value, unit: "usd" };
    case "incidents": {
      const mttr = typeof metric.mttr_p50_s === "number" ? metric.mttr_p50_s : null;
      return {
        primary: `${formatValue(value, "count")}${mttr != null ? ` · ${formatValue(mttr, "s")}` : ""}`,
        sub: mttr != null ? "count · MTTR p50" : "no resolved incidents",
        value,
        unit: "count",
      };
    }
    case "engine_wake_efficiency":
      return {
        primary: value != null ? `NEXT ${formatValue(value, "share")}` : "—",
        sub: "of wakes (vs heartbeats)",
        value,
        unit: "share",
      };
    default:
      return { primary: formatValue(value, metric.unit), sub: null, value, unit: metric.unit };
  }
}

/** The delta: the server's, else computed from `previous` (per-key metrics have none). */
export function kpiDelta(view: KpiView, metric: MetricResult | undefined, spec: KpiSpec) {
  if (!metric) return null;
  let delta = metric.delta ?? null;
  if (delta == null && metric.previous && view.value != null) {
    const before =
      spec.name === "human_wait"
        ? headline(metric.previous.by?.human_merge ?? null)
        : headline(metric.previous);
    delta = before != null ? view.value - before : null;
  }
  return delta;
}

const NO_MARKS: readonly SparkMark[] = [];

export function KpiTile({
  spec,
  metric,
  series,
  marks = NO_MARKS,
  action,
}: {
  spec: KpiSpec;
  metric: MetricResult | undefined;
  series: readonly (number | null)[];
  marks?: readonly SparkMark[];
  /** e.g. the tile's "Propose change". */
  action?: ReactNode;
}) {
  const view = kpiView(spec, metric);
  const n = metric?.n ?? 0;
  const state = nState(n);
  const delta = state === "ok" ? kpiDelta(view, metric, spec) : null;
  const deltaLabel = formatDelta(delta, view.unit);
  const band = spec.name === "human_prompts_per_day" ? promptBand(metric) : null;
  return (
    <article
      data-testid={`kpi-${spec.name}`}
      data-n-state={state}
      title={metric?.definition}
      className="flex min-w-0 flex-col gap-1 rounded-xl border border-border bg-card px-3 py-2.5"
    >
      <div className="flex min-w-0 items-baseline gap-2">
        <h3 className="min-w-0 truncate text-xs font-medium text-muted-foreground">{spec.title}</h3>
        <span
          data-testid={`kpi-${spec.name}-n`}
          className={cn(
            "ml-auto shrink-0 text-xs tabular-nums",
            state === "ok" ? "text-muted-foreground" : "text-warning",
          )}
        >
          {state === "ok" ? `n=${n}` : state === "small" ? `n=${n}` : "n=0"}
        </span>
      </div>
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
        <span
          data-testid={`kpi-${spec.name}-value`}
          className={cn(
            "text-lg leading-7 font-semibold tabular-nums",
            state === "ok" ? "text-foreground" : "text-muted-foreground",
          )}
        >
          {view.primary}
        </span>
        {deltaLabel && (
          <span
            data-testid={`kpi-${spec.name}-delta`}
            title="Against the previous window of the same length"
            className={cn("text-xs tabular-nums", TONE_TEXT[deltaTone(delta, spec.good)])}
          >
            {deltaLabel}
          </span>
        )}
      </div>
      {state !== "ok" ? (
        <p data-testid={`kpi-${spec.name}-small-n`} className="text-xs text-warning">
          {nText(n)}
        </p>
      ) : (
        view.sub && <p className="min-w-0 truncate text-xs text-muted-foreground">{view.sub}</p>
      )}
      {band && band.perDay != null && state === "ok" && (
        <BandMeter perDay={band.perDay} band={band.band} />
      )}
      <div className="mt-auto flex min-w-0 items-end gap-2 pt-1">
        <Sparkline
          series={series}
          marks={marks}
          width={120}
          height={22}
          testId={`kpi-${spec.name}-spark`}
          className={cn(
            "min-w-0 flex-1",
            state === "ok" ? "text-chart-1" : "text-muted-foreground/50",
          )}
        />
        {action}
      </div>
    </article>
  );
}

/** Prompts per day against the 30–50 band: a bar with the band shaded. */
function BandMeter({ perDay, band }: { perDay: number; band: [number, number] }) {
  const max = Math.max(band[1] * 1.4, perDay);
  const pct = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  const inBand = perDay >= band[0] && perDay <= band[1];
  return (
    <div
      aria-hidden
      data-testid="kpi-band-meter"
      className="relative h-1.5 w-full overflow-hidden rounded-full bg-muted"
    >
      <span
        className="absolute inset-y-0 bg-success/25"
        style={{ left: pct(band[0]), width: `calc(${pct(band[1])} - ${pct(band[0])})` }}
      />
      <span
        className={cn(
          "absolute inset-y-0 left-0 rounded-full",
          inBand ? "bg-success" : "bg-warning",
        )}
        style={{ width: pct(perDay) }}
      />
    </div>
  );
}
