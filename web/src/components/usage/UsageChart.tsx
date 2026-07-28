// Spend-over-time bar chart for the Usage section. A single series (USD cost
// per bucket), so it follows the single-series rules: one hue (the app's
// `primary` token, already theme-validated), thin bars with rounded tops
// anchored to the baseline, a recessive baseline + sparse axis labels, and a
// per-bar hover tooltip. No legend (the heading names the series). The groups
// table beside it is the accessible data view.

import { useMemo } from "react";
import {
  formatBucketLabel,
  formatTokens,
  formatUsd,
  type UsageBucketEntry,
  type UsagePeriod,
} from "@/lib/usageApi";
import { cn } from "@/lib/utils";

interface UsageChartProps {
  buckets: UsageBucketEntry[];
  period: UsagePeriod;
}

// Cap how many x-axis labels render so a 30-day / 24-hour axis doesn't crowd.
const MAX_AXIS_LABELS = 6;

export function UsageChart({ buckets, period }: UsageChartProps) {
  const maxCost = useMemo(
    () => buckets.reduce((max, b) => Math.max(max, b.total_cost_usd), 0),
    [buckets],
  );

  // Show at most MAX_AXIS_LABELS evenly-spaced labels (always the last one).
  const labelStep = Math.max(1, Math.ceil(buckets.length / MAX_AXIS_LABELS));

  return (
    <div className="flex flex-col gap-2">
      <div
        role="img"
        aria-label={`Spend over time, peak ${formatUsd(maxCost)} in a single bucket`}
        className="flex h-48 items-end gap-[2px] border-b border-border"
      >
        {buckets.map((bucket) => {
          // Scale to the peak bucket; keep a sliver visible for any non-zero
          // spend so a small-but-present bucket never reads as empty.
          const ratio = maxCost > 0 ? bucket.total_cost_usd / maxCost : 0;
          const heightPct = bucket.total_cost_usd > 0 ? Math.max(ratio * 100, 1.5) : 0;
          return (
            <div key={bucket.bucket_start} className="group relative flex h-full flex-1 items-end">
              <div
                className={cn(
                  "w-full rounded-t-[4px] bg-primary/80 transition-colors group-hover:bg-primary",
                  bucket.total_cost_usd === 0 && "bg-transparent group-hover:bg-muted",
                )}
                style={{ height: `${heightPct}%` }}
              />
              {/* Per-bar tooltip (CSS-only via group-hover). */}
              <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-2 hidden -translate-x-1/2 whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md group-hover:block">
                <div className="font-medium">{formatBucketLabel(bucket.bucket_start, period)}</div>
                <div className="text-muted-foreground">
                  {formatUsd(bucket.total_cost_usd)} · {formatTokens(bucket.input_tokens)} in ·{" "}
                  {formatTokens(bucket.output_tokens)} out
                </div>
              </div>
            </div>
          );
        })}
      </div>
      {/* Sparse x-axis: every labelStep-th bucket, laid out on the same flex
          grid so labels sit under their bars. */}
      <div className="flex gap-[2px] text-xs text-muted-foreground">
        {buckets.map((bucket, i) => (
          <div key={bucket.bucket_start} className="flex-1 text-center">
            {i % labelStep === 0 ? formatBucketLabel(bucket.bucket_start, period) : ""}
          </div>
        ))}
      </div>
    </div>
  );
}
