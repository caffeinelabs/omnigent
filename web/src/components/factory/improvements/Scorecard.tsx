/**
 * A trial's scorecard: the target metric and every guard over the trial
 * window against the same-length baseline before it, with n, the allowed
 * change and the verdict the server would reach now. Small n reads as
 * inconclusive; a regressed guard is the one red mark.
 */

import { CheckIcon, MinusIcon, XIcon } from "lucide-react";
import type { ScorecardView } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { TONE_CHIP, TONE_TEXT } from "../factoryFormat";
import { metricLabel } from "../insights/insightsModel";
import { metricValue, nCounts, targetTone, verdictText } from "./improvementsModel";

const GUARD_LABEL: Record<string, string> = {
  merge_acceptance: "acceptance",
  cost_per_merged_pr: "$/PR",
  incidents: "incidents",
  human_prompts_per_day: "prompts/day",
  loop_exhaustion_rate: "exhaustion",
};

function GuardIcon({ status }: { status: string }) {
  if (status === "ok") return <CheckIcon aria-label="holds" className="size-3.5 text-success" />;
  if (status === "regressed")
    return <XIcon aria-label="regressed" className="size-3.5 text-destructive" />;
  return <MinusIcon aria-label="no data" className="size-3.5 text-muted-foreground" />;
}

/** The one-line summary: guards as ✓/✗/– chips plus the target. */
export function ScorecardLine({ card, testId }: { card: ScorecardView; testId?: string }) {
  const tone = targetTone(card);
  return (
    <div
      data-testid={testId}
      className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm"
    >
      <span className="text-muted-foreground">guards:</span>
      {card.guards.map((g) => (
        <span
          key={g.metric}
          data-testid={testId ? `${testId}-guard-${g.metric}` : undefined}
          data-status={g.status}
          title={`${metricLabel(g.metric)}: ${metricValue(g.metric, g.baseline)} → ${metricValue(g.metric, g.trial)} (allowed ${g.allowed})`}
          className={cn(
            "inline-flex items-center gap-0.5 whitespace-nowrap",
            g.status === "regressed" && "font-medium text-destructive",
          )}
        >
          {GUARD_LABEL[g.metric] ?? metricLabel(g.metric)}
          <GuardIcon status={g.status} />
        </span>
      ))}
      {card.target.metric && (
        <span className={cn("whitespace-nowrap", TONE_TEXT[tone])}>
          target {metricLabel(card.target.metric)}{" "}
          {metricValue(card.target.metric, card.target.baseline)} →{" "}
          {metricValue(card.target.metric, card.target.trial)}
        </span>
      )}
    </div>
  );
}

/** Baseline vs trial per metric, with n and the allowed change. */
export function ScorecardTable({ card, testId }: { card: ScorecardView; testId?: string }) {
  const rows = [
    ...(card.target.metric
      ? [
          {
            key: "target",
            label: `${metricLabel(card.target.metric)} (target ${card.target.direction === "up" ? "↑" : "↓"})`,
            base: metricValue(card.target.metric, card.target.baseline),
            trial: metricValue(card.target.metric, card.target.trial),
            n: null as string | null,
            allowed: card.target.met ? "met" : "not met yet",
            status: card.target.met ? "ok" : "no_data",
          },
        ]
      : []),
    ...card.guards.map((g) => ({
      key: g.metric,
      label: metricLabel(g.metric),
      base: metricValue(g.metric, g.baseline),
      trial: metricValue(g.metric, g.trial),
      n: `${g.n_baseline} / ${g.n_trial}`,
      allowed: g.allowed,
      status: g.status,
    })),
  ];
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <p className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
        <span
          data-testid={testId ? `${testId}-verdict` : undefined}
          data-outcome={card.outcome}
          className={cn(
            "rounded-md px-1.5 py-0.5 text-xs",
            card.outcome === "kept"
              ? TONE_CHIP.pass
              : card.outcome === "reverted"
                ? TONE_CHIP.fail
                : TONE_CHIP.muted,
          )}
        >
          {verdictText(card)}
        </span>
        <span className="text-xs text-muted-foreground">
          kept once n reaches {card.min_n} {nCounts(card)}, the target is met and every guard holds
        </span>
      </p>
      <ul className="flex min-w-0 flex-col divide-y divide-border sm:hidden">
        {rows.map((row) => (
          <li key={row.key} className="flex min-w-0 flex-col py-1 text-sm">
            <span className="inline-flex min-w-0 items-center gap-1">
              <GuardIcon status={row.status} />
              <span className="truncate">{row.label}</span>
              <span className="ml-auto shrink-0 tabular-nums">
                {row.base} → {row.trial}
              </span>
            </span>
            <span className="text-xs text-muted-foreground">
              {row.allowed}
              {row.n ? ` · n ${row.n}` : ""}
            </span>
          </li>
        ))}
      </ul>
      <div className="min-w-0 overflow-x-auto max-sm:hidden">
        <table data-testid={testId} className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted-foreground">
              <th className="py-1 pr-3 font-medium">Metric</th>
              <th className="py-1 pr-3 text-right font-medium">Baseline</th>
              <th className="py-1 pr-3 text-right font-medium">Trial</th>
              <th className="py-1 pr-3 text-right font-medium">n</th>
              <th className="py-1 font-medium">Allowed</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-b border-border/60 last:border-0">
                <td className="py-1 pr-3">
                  <span className="inline-flex items-center gap-1">
                    <GuardIcon status={row.status} />
                    {row.label}
                  </span>
                </td>
                <td className="py-1 pr-3 text-right tabular-nums">{row.base}</td>
                <td className="py-1 pr-3 text-right tabular-nums">{row.trial}</td>
                <td className="py-1 pr-3 text-right text-muted-foreground tabular-nums">
                  {row.n ?? "—"}
                </td>
                <td className="py-1 text-xs text-muted-foreground">{row.allowed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
