/**
 * Alerts (`/factory/:factoryId/alerts`): incidents (live, acknowledged,
 * assigned, snoozed, resolved) and findings (slower process gaps), each
 * with its lifecycle levers and its rule's runbook. `?class=finding` and
 * `?status=` pick the list; the rules and where alerts are routed fold
 * open below the header. A ref opens the incident page.
 */

import { useMemo, useState } from "react";
import { BellOffIcon, ChevronRightIcon } from "lucide-react";
import { useAlertRouting, useFactoryIssues } from "@/components/factory/agents/opsQueries";
import { ALERT_RULES } from "@/components/factory/alerts/alertRules";
import { IssueCard } from "@/components/factory/alerts/IssueCard";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import type { IssueClass, IssueStatus, IssueView } from "@/lib/factoryApi";
import { useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";

type StatusTab = "open" | "acked" | "snoozed" | "resolved";
const TABS: { key: StatusTab; label: string; statuses: IssueStatus[] }[] = [
  { key: "open", label: "Open", statuses: ["open"] },
  { key: "acked", label: "Acked", statuses: ["acked"] },
  { key: "snoozed", label: "Snoozed", statuses: ["snoozed"] },
  { key: "resolved", label: "Resolved", statuses: ["resolved", "wontfix"] },
];

const SEVERITY_RANK: Record<IssueView["severity"], number> = {
  critical: 0,
  warning: 1,
  notice: 2,
  info: 3,
};

function startOfDayS(nowS: number): number {
  const d = new Date(nowS * 1000);
  d.setHours(0, 0, 0, 0);
  return d.getTime() / 1000;
}

function Rules() {
  const [open, setOpen] = useState(false);
  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        data-testid="alerts-rules-toggle"
        className="inline-flex cursor-pointer items-center gap-1 rounded-sm text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronRightIcon
          aria-hidden
          className={cn("size-3.5 transition-transform", open && "rotate-90")}
        />
        Rules ({ALERT_RULES.length})
      </button>
      {open && (
        // A phone stacks each rule as a card; wider screens get the table.
        <ul
          data-testid="alerts-rules-cards"
          className="mt-2 flex min-w-0 flex-col divide-y divide-border rounded-xl border border-border bg-card sm:hidden"
        >
          {ALERT_RULES.map((rule) => (
            <li key={rule.rule} className="flex min-w-0 flex-col gap-0.5 px-3 py-2 text-sm">
              <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
                <span className="font-mono text-xs break-all">{rule.rule}</span>
                <span className="text-xs text-muted-foreground">
                  {rule.class} · {rule.severity}
                </span>
              </span>
              <span className="break-words">{rule.trigger}</span>
              <span className="text-xs text-muted-foreground">
                Runbook: {rule.runbook.map((a) => a.replaceAll("_", " ")).join(" · ") || "—"}
              </span>
            </li>
          ))}
        </ul>
      )}
      {open && (
        <div className="mt-2 min-w-0 overflow-x-auto rounded-xl border border-border bg-card max-sm:hidden">
          <table data-testid="alerts-rules" className="w-full min-w-[36rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="px-3 py-2 font-medium">Rule</th>
                <th className="px-3 py-2 font-medium">Class</th>
                <th className="px-3 py-2 font-medium">Fires when</th>
                <th className="px-3 py-2 font-medium">Runbook</th>
              </tr>
            </thead>
            <tbody>
              {ALERT_RULES.map((rule) => (
                <tr key={rule.rule} className="border-b border-border/60 last:border-0">
                  <td className="px-3 py-1.5 font-mono text-xs">{rule.rule}</td>
                  <td className="px-3 py-1.5 text-muted-foreground">
                    {rule.class} · {rule.severity}
                  </td>
                  <td className="px-3 py-1.5">{rule.trigger}</td>
                  <td className="px-3 py-1.5 text-xs text-muted-foreground">
                    {rule.runbook.map((a) => a.replaceAll("_", " ")).join(" · ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function AlertsPage() {
  const { snapshot, nowS } = useFactoryShell();
  const fid = snapshot.factory.id;
  const [params, setParams] = useSearchParams();
  const cls: IssueClass = params.get("class") === "finding" ? "finding" : "incident";
  const tab = (TABS.find((t) => t.key === params.get("status"))?.key ?? "open") as StatusTab;
  const issues = useFactoryIssues(fid, { class: cls, limit: 500 });
  const routing = useAlertRouting(fid);
  const all = useMemo(() => issues.data?.data ?? [], [issues.data]);
  const today = startOfDayS(nowS);
  const counts = useMemo(
    () => ({
      open: all.filter((i) => i.status === "open").length,
      acked: all.filter((i) => i.status === "acked").length,
      snoozed: all.filter((i) => i.status === "snoozed").length,
      resolved: all.filter((i) => i.status === "resolved" || i.status === "wontfix").length,
      resolvedToday: all.filter(
        (i) => (i.status === "resolved" || i.status === "wontfix") && (i.resolved_at ?? 0) >= today,
      ).length,
    }),
    [all, today],
  );
  const shown = useMemo(() => {
    const statuses = TABS.find((t) => t.key === tab)!.statuses;
    return all
      .filter((i) => statuses.includes(i.status))
      .sort((a, b) =>
        tab === "resolved"
          ? (b.resolved_at ?? 0) - (a.resolved_at ?? 0)
          : SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || b.last_seen - a.last_seen,
      );
  }, [all, tab]);
  const set = (key: string, value: string | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );
  const dot = (on: boolean) => (
    <span className={on ? "text-success" : "text-muted-foreground"}>{on ? "●" : "○"}</span>
  );
  return (
    <div data-testid="factory-alerts-page" className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-base font-semibold">Alerts</h1>
        <span data-testid="alerts-counts" className="text-sm text-muted-foreground tabular-nums">
          Open {counts.open} · Acked {counts.acked} · Snoozed {counts.snoozed} · Resolved today{" "}
          {counts.resolvedToday}
        </span>
        {routing.data && (
          <span data-testid="alerts-routing" className="ml-auto text-xs text-muted-foreground">
            Routing: cockpit {dot(routing.data.cockpit)} browser {dot(routing.data.browser)} Slack{" "}
            {dot(routing.data.slack)}
          </span>
        )}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <div role="tablist" aria-label="Class" className="inline-flex rounded-lg bg-muted p-[3px]">
          {(["incident", "finding"] as const).map((c) => (
            <button
              key={c}
              type="button"
              role="tab"
              aria-selected={cls === c}
              data-testid={`alerts-class-${c}`}
              onClick={() => set("class", c === "incident" ? null : c)}
              className={cn(
                "h-6 cursor-pointer rounded-md px-2 text-sm font-medium focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
                cls === c
                  ? "bg-background text-foreground shadow-sm dark:bg-input/30"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {c === "incident" ? "Incidents" : "Findings"}
            </button>
          ))}
        </div>
        <div
          role="tablist"
          aria-label="Status"
          className="flex min-w-0 items-center gap-3 overflow-x-auto"
        >
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              data-testid={`alerts-tab-${t.key}`}
              onClick={() => set("status", t.key === "open" ? null : t.key)}
              className={cn(
                "inline-flex h-7 shrink-0 cursor-pointer items-center gap-1 border-b-2 px-0.5 text-sm font-medium",
                tab === t.key
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {t.label}
              <span className="text-xs font-normal text-muted-foreground tabular-nums">
                {counts[t.key]}
              </span>
            </button>
          ))}
        </div>
      </div>
      <Rules />
      {issues.isLoading && !issues.data ? (
        <div
          aria-busy="true"
          aria-label="Loading alerts"
          data-testid="alerts-loading"
          className="flex flex-col gap-2 rounded-xl border border-border p-3"
        >
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-14 animate-pulse rounded-lg bg-muted/70" />
          ))}
        </div>
      ) : issues.error && !issues.data ? (
        <FactoryLoadError
          error={issues.error}
          updatedAt={0}
          onRetry={() => void issues.refetch()}
          what="the alerts"
          testId="alerts-error"
        />
      ) : shown.length === 0 ? (
        <EmptyState
          testId="alerts-empty"
          icon={<BellOffIcon aria-hidden className="size-6 text-muted-foreground/50" />}
          title={
            tab === "open"
              ? `No open ${cls === "incident" ? "incidents" : "findings"}`
              : `Nothing ${tab}`
          }
          body={
            cls === "incident"
              ? "Detectors open an incident when an agent dies, repeats a failing step, a loop runs out, spend spikes or a ready PR waits."
              : "Findings are slower process gaps: flaky CI, ping-pong mail, unneeded questions, wasted heartbeats."
          }
        />
      ) : (
        <ul data-testid="alerts-list" className="min-w-0 rounded-xl border border-border bg-card">
          {shown.map((issue) => (
            <IssueCard key={issue.id} snapshot={snapshot} issue={issue} nowS={nowS} />
          ))}
        </ul>
      )}
    </div>
  );
}
