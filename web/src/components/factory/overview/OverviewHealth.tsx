/**
 * Is the machine alive, and what is it costing: the health strip (supervisor,
 * mail, GitHub, previews, demo storage, drift) and, when the server reports
 * them, loops across projects, spend per hour, open alerts, pending proposals
 * and changes on trial, then when the next heartbeats check in.
 */

import type { ReactNode } from "react";
import { HeartPulseIcon } from "lucide-react";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { nextChecks, projectLeadIds, untilText } from "../board/boardModel";
import { HealthStrip } from "../board/HealthStrip";
import { formatUsd } from "../factoryFormat";

/** Loops across every project (absent until the server sends loop summaries). */
export function loopTotals(snapshot: FactorySnapshot): {
  running: number;
  exhausted: number;
  stalled: number;
} | null {
  const summaries = snapshot.projects.flatMap((p) => (p.loops_summary ? [p.loops_summary] : []));
  if (summaries.length === 0) return null;
  return summaries.reduce(
    (acc, s) => ({
      running: acc.running + s.running,
      exhausted: acc.exhausted + s.exhausted + s.escalated,
      stalled: acc.stalled + s.stalled + s.thrashing,
    }),
    { running: 0, exhausted: 0, stalled: 0 },
  );
}

function Chip({
  testId,
  tone = "muted",
  title,
  children,
}: {
  testId: string;
  tone?: "muted" | "warning";
  title?: string;
  children: ReactNode;
}) {
  return (
    <li
      data-testid={testId}
      title={title}
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap",
        tone === "warning" ? "text-warning" : "text-muted-foreground",
      )}
    >
      {children}
    </li>
  );
}

export function OverviewHealth({ snapshot, nowS }: { snapshot: FactorySnapshot; nowS: number }) {
  const loops = loopTotals(snapshot);
  const counts = snapshot.counts ?? null;
  const perHour = snapshot.totals.usd_per_h;
  const { factory } = snapshot;
  const checks =
    factory.state === "active" ? nextChecks(factory, nowS, projectLeadIds(snapshot)) : null;
  const checkParts = [
    checks?.lead ? `${checks.lead.title} ${untilText(checks.lead.in)}` : null,
    checks?.pls != null ? `PLs ${untilText(checks.pls)}` : null,
  ].filter(Boolean);
  const extras =
    loops != null || perHour != null || (counts && (counts.issues_open || counts.on_trial));
  return (
    <section
      aria-label="Health"
      data-testid="overview-health"
      className="flex min-w-0 flex-col gap-1.5 rounded-xl border border-border bg-card px-4 py-3 max-md:px-3"
    >
      <HealthStrip snapshot={snapshot} nowS={nowS} />
      {(extras || checkParts.length > 0) && (
        <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          {loops && (
            <Chip
              testId="health-loops"
              tone={loops.exhausted > 0 ? "warning" : "muted"}
              title="Loops across projects: running · exhausted or escalated · stalled or thrashing"
            >
              Loops <span className="font-mono tabular-nums">{loops.running}▶</span>
              {loops.exhausted > 0 && (
                <span className="font-mono tabular-nums">{loops.exhausted}⛔</span>
              )}
              {loops.stalled > 0 && (
                <span className="font-mono tabular-nums">{loops.stalled}⟳</span>
              )}
            </Chip>
          )}
          {perHour != null && (
            <Chip testId="health-spend" title="Spend over the last hour, all agents">
              Spend{" "}
              <span className="font-mono text-foreground tabular-nums">{formatUsd(perHour)}/h</span>
            </Chip>
          )}
          {counts && counts.issues_open > 0 && (
            <Chip testId="health-alerts" tone="warning">
              Alerts <span className="tabular-nums">{counts.issues_open}</span>
            </Chip>
          )}
          {counts && counts.proposals_pending > 0 && (
            <Chip testId="health-proposals">
              Proposals <span className="tabular-nums">{counts.proposals_pending}</span>
            </Chip>
          )}
          {counts && counts.on_trial > 0 && (
            <Chip testId="health-trial" title="Definition changes being trialled">
              On trial <span className="tabular-nums">{counts.on_trial}</span>
            </Chip>
          )}
          {checkParts.length > 0 && (
            <Chip
              testId="health-next-check"
              title="Heartbeats check in no later than this; quiet beats are skipped when nothing changed"
            >
              <HeartPulseIcon aria-hidden className="size-3.5" />
              next check ≤ <span className="tabular-nums">{checkParts.join(" · ")}</span>
            </Chip>
          )}
        </ul>
      )}
    </section>
  );
}
