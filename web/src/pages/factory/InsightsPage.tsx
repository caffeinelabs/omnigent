/**
 * Insights (`/factory/:factoryId/insights`): how the factory is doing. KPI
 * tiles with n, the change against the previous window and a sparkline;
 * definition and platform version markers (click a definition version to
 * measure only its window); a group-by table; then Flow, Loops, Quality,
 * People (per-person numbers for owners only), Cost, Reliability, Findings
 * and retros, and Experiments. `?window=`, `?group=`, `?project=` and `?def=`
 * keep the view in the URL. Export streams the telemetry (never transcripts).
 */

import { type ReactNode, useMemo, useState } from "react";
import { DownloadIcon, LightbulbIcon, TrendingUpIcon } from "lucide-react";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import {
  type InsightsData,
  CostSection,
  ExperimentsSection,
  FindingsSection,
  FlowSection,
  LoopsSection,
  PeopleSection,
  QualitySection,
  ReliabilitySection,
} from "@/components/factory/insights/InsightsSections";
import { KpiTile, kpiView } from "@/components/factory/insights/KpiTile";
import {
  DEFAULT_WINDOW,
  GROUP_OPTIONS,
  KPI_SPECS,
  WINDOWS,
  daySeries,
  formatValue,
  headline,
  markPosition,
  nState,
  nText,
  versionMarks,
  windowDays,
  windowSeconds,
} from "@/components/factory/insights/insightsModel";
import {
  useFactoryInsights,
  useFactoryMetrics,
  useFactoryVersions,
} from "@/components/factory/insights/learnQueries";
import {
  PROPOSE_REASON,
  ProposeChangeDialog,
  type ProposeSource,
} from "@/components/factory/improvements/ProposeChangeDialog";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { ReasonGated, useViewerCan } from "@/components/factory/shell/viewerCan";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { showToast } from "@/components/ui/toast";
import {
  type IssueView,
  type MetricAggregate,
  type MetricGroupBy,
  type MetricResult,
  fetchFactoryExport,
} from "@/lib/factoryApi";
import { useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";

/** The metric each tile's "Propose change" starts from, and the setting it suggests. */
const TILE_PROPOSALS: Record<string, { path: string }> = {
  merge_acceptance: { path: "loops.review.rounds" },
  lead_time: { path: "loops.implement.attempts" },
  human_wait: { path: "decisions.escalate_after_m" },
  human_prompts_per_day: { path: "autonomy.projects" },
  cost_per_merged_pr: { path: "loops.implement.usd" },
  incidents: { path: "alerts.repeat_tool_n" },
  engine_wake_efficiency: { path: "cadence.project_lead" },
};

function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
  testId,
}: {
  label: string;
  options: readonly T[];
  value: T;
  onChange: (value: T) => void;
  testId: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      data-testid={testId}
      className="inline-flex rounded-lg bg-muted p-[3px]"
    >
      {options.map((option) => (
        <button
          key={option}
          type="button"
          role="radio"
          aria-checked={value === option}
          data-testid={`${testId}-${option}`}
          onClick={() => onChange(option)}
          className={cn(
            "h-6 cursor-pointer rounded-md px-2 text-sm font-medium tabular-nums focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
            value === option
              ? "bg-background text-foreground shadow-sm dark:bg-input/30"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {option}
        </button>
      ))}
    </div>
  );
}

/** One grouped value with its n; below n=3 it is muted and called inconclusive. */
function GroupValue({ agg, unit }: { agg: MetricAggregate | undefined; unit: string }) {
  if (!agg) return <span className="text-muted-foreground">—</span>;
  const small = nState(agg.n) !== "ok";
  return (
    <span
      data-small-n={small ? "true" : undefined}
      title={small ? nText(agg.n) : undefined}
      className="inline-flex items-baseline gap-1 tabular-nums"
    >
      <span className={small ? "text-muted-foreground" : undefined}>
        {formatValue(headline(agg), unit)}
      </span>
      <span className={cn("text-xs", small ? "text-warning" : "text-muted-foreground")}>
        n={agg.n}
      </span>
    </span>
  );
}

function GroupTable({
  metrics,
  group,
}: {
  metrics: Record<string, MetricResult> | undefined;
  group: string;
}) {
  const keys = useMemo(() => {
    const all = new Set<string>();
    for (const m of Object.values(metrics ?? {}))
      for (const k of Object.keys(m.groups ?? {})) all.add(k);
    return [...all].sort();
  }, [metrics]);
  if (!metrics) return <div className="h-24 animate-pulse rounded-xl bg-muted/70" />;
  if (keys.length === 0) {
    return <p className="text-sm text-muted-foreground">No samples to group in this window.</p>;
  }
  return (
    <>
      <ul
        data-testid="insights-group-cards"
        className="flex min-w-0 flex-col divide-y divide-border rounded-xl border border-border bg-card sm:hidden"
      >
        {keys.map((key) => (
          <li key={key} className="flex min-w-0 flex-col gap-1 px-3 py-2 text-sm">
            <span className="truncate font-medium">{key || "—"}</span>
            <span className="grid grid-cols-1 gap-y-0.5 text-xs">
              {KPI_SPECS.map((spec) => {
                const metric = metrics[spec.name];
                return (
                  <span key={spec.name} className="flex min-w-0 justify-between gap-2">
                    <span className="truncate text-muted-foreground">{spec.title}</span>
                    <span className="shrink-0">
                      <GroupValue agg={metric?.groups?.[key]} unit={metric?.unit ?? ""} />
                    </span>
                  </span>
                );
              })}
            </span>
          </li>
        ))}
      </ul>
      <div className="min-w-0 overflow-x-auto rounded-xl border border-border bg-card max-sm:hidden">
        <table data-testid="insights-group-table" className="w-full min-w-[46rem] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted-foreground">
              <th className="px-3 py-2 font-medium">
                {GROUP_OPTIONS.find((g) => g.key === group)?.label}
              </th>
              {KPI_SPECS.map((spec) => (
                <th key={spec.name} className="px-3 py-2 text-right font-medium">
                  {spec.title}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {keys.map((key) => (
              <tr
                key={key}
                data-testid={`insights-group-${key || "none"}`}
                className="border-b border-border/60 last:border-0"
              >
                <td className="max-w-[12rem] truncate px-3 py-1.5">{key || "—"}</td>
                {KPI_SPECS.map((spec) => {
                  const metric = metrics[spec.name];
                  return (
                    <td key={spec.name} className="px-3 py-1.5 text-right">
                      <GroupValue agg={metric?.groups?.[key]} unit={metric?.unit ?? ""} />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">
        Values with n below 3 are shown muted: too few samples to compare.
      </p>
    </>
  );
}

// Seven tiles: all in a row when the column is wide, else 4 + 3 (never 5 + 2).
const KPI_GRID =
  "grid min-w-0 grid-cols-2 gap-2 @xl/insights:grid-cols-3 @3xl/insights:grid-cols-4 @7xl/insights:grid-cols-7";

function Loading() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading insights"
      data-testid="insights-loading"
      className="flex flex-col gap-3"
    >
      <div className={KPI_GRID}>
        {KPI_SPECS.map((spec) => (
          <div key={spec.name} className="h-28 animate-pulse rounded-xl bg-muted/70" />
        ))}
      </div>
      {[0, 1].map((i) => (
        <div key={i} className="h-48 animate-pulse rounded-xl bg-muted/70" />
      ))}
    </div>
  );
}

export function InsightsPage() {
  const { snapshot, nowS } = useFactoryShell();
  const fid = snapshot.factory.id;
  const { can } = useViewerCan();
  const owner = snapshot.viewer ? snapshot.viewer.level >= 3 : true;
  const [params, setParams] = useSearchParams();
  const windowKey = (WINDOWS as readonly string[]).includes(params.get("window") ?? "")
    ? (params.get("window") as (typeof WINDOWS)[number])
    : DEFAULT_WINDOW;
  const groupParam = params.get("group") ?? "factory";
  const group = GROUP_OPTIONS.some((g) => g.key === groupParam && (!g.ownersOnly || owner))
    ? groupParam
    : "factory";
  const project = params.get("project") || null;
  const defVersion = params.get("def") ? Number(params.get("def")) : null;
  const filters = { window: windowKey, project, def_version: defVersion };
  const [proposing, setProposing] = useState<ProposeSource | null>(null);

  const insights = useFactoryInsights(fid, filters);
  const all = useFactoryMetrics(fid, filters);
  const byDay = useFactoryMetrics(fid, {
    ...filters,
    names: [...KPI_SPECS.map((k) => k.name), "cost_usd", "throughput"],
    groupBy: "day",
  });
  const waitByProject = useFactoryMetrics(fid, {
    ...filters,
    names: ["human_wait"],
    groupBy: "project",
  });
  const costByRole = useFactoryMetrics(fid, { ...filters, names: ["cost_usd"], groupBy: "role" });
  const grouped = useFactoryMetrics(
    fid,
    { ...filters, names: KPI_SPECS.map((k) => k.name), groupBy: group as MetricGroupBy },
    group !== "factory",
  );
  const versions = useFactoryVersions(fid);

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

  const win = insights.data?.window ?? null;
  const days = useMemo(() => (win ? windowDays(win.since, win.until) : []), [win]);
  // Without the insights window (still loading or failed), mark versions over the picked window.
  const since = win?.since ?? Math.floor(nowS / 3600) * 3600 - windowSeconds(windowKey);
  const marks = useMemo(
    () =>
      versions.data ? versionMarks(versions.data.definition, versions.data.platform, since) : [],
    [since, versions.data],
  );
  const sparkMarks = win
    ? marks
        .filter((m) => m.at >= win.since && m.kind === "def")
        .map((m) => ({ key: m.key, at: markPosition(m.at, win.since, win.until), title: m.title }))
    : [];
  const dayMarks = win
    ? marks
        .filter((m) => m.at >= win.since && m.kind === "def")
        .map((m) => ({
          key: m.key,
          day: new Date(m.at * 1000).toISOString().slice(0, 10),
          label: m.label,
        }))
    : [];

  const exportAs = (format: "ndjson" | "csv") => {
    fetchFactoryExport(fid, format).then(
      (blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `factory-${fid.slice(0, 8)}-telemetry.${format}`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      },
      (error: unknown) =>
        showToast(`Export failed: ${error instanceof Error ? error.message : String(error)}`),
    );
  };

  const proposeReason = can.settings || can.apply_projects ? null : PROPOSE_REASON;
  const proposeButton = (source: ProposeSource, testId: string, label: ReactNode = null) => (
    <ReasonGated reason={proposeReason} testId={testId}>
      <Button
        size="icon-xs"
        variant="ghost"
        aria-label="Propose change"
        title="Propose change"
        onClick={() => setProposing(source)}
        data-testid={testId}
        componentId="factory.insights.propose"
        className="text-muted-foreground"
      >
        <LightbulbIcon />
        {label}
      </Button>
    </ReasonGated>
  );

  const data: InsightsData | null =
    insights.data && all.data
      ? {
          all: all.data.metrics,
          byDay: byDay.data?.metrics ?? {},
          waitByProject: waitByProject.data?.metrics.human_wait,
          costByRole: costByRole.data?.metrics.cost_usd,
          insights: insights.data,
          days,
          dayMarks,
        }
      : null;
  const projectTitle = (slug: string) =>
    snapshot.projects.find((p) => p.slug === slug)?.title ?? slug;
  const noData = data ? KPI_SPECS.every((k) => !(data.insights.kpis[k.name]?.n ?? 0)) : false;
  const defRows = versions.data?.definition ?? [];
  const defSince = defRows.find((d) => d.version === defVersion)?.created_at ?? null;
  const defUntil =
    defVersion != null ? (defRows.find((d) => d.version > defVersion)?.created_at ?? null) : null;
  const experiments = data?.insights.experiments;
  const hasFindings = data
    ? data.insights.findings.length > 0 || data.insights.retros.length > 0
    : false;
  const hasExperiments = experiments
    ? experiments.on_trial + experiments.kept + experiments.reverted > 0
    : false;

  return (
    <div
      data-testid="factory-insights-page"
      className="@container/insights flex min-w-0 flex-col gap-3"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-base font-semibold">Insights</h1>
        <Segmented
          label="Window"
          options={WINDOWS}
          value={windowKey}
          onChange={(w) => set("window", w === DEFAULT_WINDOW ? null : w)}
          testId="insights-window"
        />
        <span className="text-sm text-muted-foreground">vs previous {windowKey}</span>
        <Select
          value={group}
          onValueChange={(g) => set("group", g === "factory" ? null : g)}
          componentId="factory.insights.group"
          valueHasNoPii
        >
          <SelectTrigger
            size="sm"
            aria-label="Group by"
            data-testid="insights-group"
            className="w-44"
          >
            <span className="text-muted-foreground">group</span>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {GROUP_OPTIONS.filter((g) => !g.ownersOnly || owner).map((g) => (
              <SelectItem key={g.key} value={g.key}>
                {g.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={project ?? "all"}
          onValueChange={(p) => set("project", p === "all" ? null : p)}
          componentId="factory.insights.project"
        >
          <SelectTrigger
            size="sm"
            aria-label="Project"
            data-testid="insights-project"
            className="w-44"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All projects</SelectItem>
            {snapshot.projects.map((p) => (
              <SelectItem key={p.slug} value={p.slug}>
                {p.title || p.slug}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="sm"
              variant="outline"
              data-testid="insights-export"
              componentId="factory.insights.export"
              className="ml-auto"
            >
              <DownloadIcon />
              Export
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              onSelect={() => exportAs("ndjson")}
              data-testid="insights-export-ndjson"
            >
              NDJSON (spans, cost, decisions, PRs, demos, events)
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => exportAs("csv")} data-testid="insights-export-csv">
              CSV
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <div
        data-testid="insights-versions"
        className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs"
      >
        <span className="text-muted-foreground">versions</span>
        {versions.isLoading ? (
          <span className="h-5 w-40 animate-pulse rounded-md bg-muted" />
        ) : versions.error && !versions.data ? (
          <span data-testid="insights-versions-error" className="text-muted-foreground">
            couldn’t load them ·{" "}
            <button
              type="button"
              onClick={() => void versions.refetch()}
              className="cursor-pointer underline-offset-2 hover:text-foreground hover:underline"
            >
              Retry
            </button>
          </span>
        ) : marks.length === 0 ? (
          <span className="text-muted-foreground">none recorded yet</span>
        ) : (
          marks.map((mark) => {
            const active = mark.version != null && mark.version === defVersion;
            return (
              <button
                key={mark.key}
                type="button"
                disabled={mark.version == null}
                aria-pressed={mark.version != null ? active : undefined}
                title={`${mark.title}${mark.version != null ? (active ? " · showing only this version" : " · click to measure only this version") : ""}`}
                data-testid={`insights-mark-${mark.key}`}
                onClick={() =>
                  set("def", active || mark.version == null ? null : String(mark.version))
                }
                className={cn(
                  "inline-flex h-5 items-center gap-1 rounded-md border border-dashed px-1.5 font-mono",
                  mark.version != null ? "cursor-pointer hover:text-foreground" : "cursor-default",
                  active
                    ? "border-foreground/40 bg-muted text-foreground"
                    : "border-border text-muted-foreground",
                )}
              >
                {mark.label}
                {mark.current && (
                  <span className="size-1.5 rounded-full bg-success" aria-label="in force" />
                )}
              </button>
            );
          })
        )}
      </div>
      {defVersion != null && (
        <p
          data-testid="insights-def-filter"
          className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-border bg-muted/50 px-3 py-1.5 text-sm"
        >
          <span className="font-medium">Measuring definition v{defVersion} only</span>
          {defSince != null && (
            <span className="text-muted-foreground">
              since {new Date(defSince * 1000).toLocaleString()}
              {defUntil != null ? ` until v${defVersion + 1}` : " (in force)"}
            </span>
          )}
          <Button
            size="xs"
            variant="ghost"
            onClick={() => set("def", null)}
            data-testid="insights-def-clear"
            componentId="factory.insights.def_clear"
            className="ml-auto"
          >
            Clear
          </Button>
        </p>
      )}
      {insights.isLoading && !insights.data ? (
        <Loading />
      ) : insights.error && !insights.data ? (
        <FactoryLoadError
          error={insights.error}
          updatedAt={0}
          onRetry={() => void insights.refetch()}
          what="insights"
          testId="insights-error"
        />
      ) : (
        <>
          <div data-testid="insights-kpis" className={KPI_GRID}>
            {KPI_SPECS.map((spec) => {
              const metric = insights.data?.kpis[spec.name];
              const series = daySeries(byDay.data?.metrics[spec.name]?.groups, days);
              const view = kpiView(spec, metric);
              return (
                <KpiTile
                  key={spec.name}
                  spec={spec}
                  metric={metric}
                  series={
                    spec.name === "human_wait"
                      ? days.map((d) =>
                          headline(
                            byDay.data?.metrics.human_wait?.groups?.[d]?.by?.human_merge ?? null,
                          ),
                        )
                      : series
                  }
                  marks={sparkMarks}
                  action={proposeButton(
                    {
                      metric: spec.name,
                      path: TILE_PROPOSALS[spec.name]?.path,
                      why: `${spec.title}: ${view.primary} (${nText(metric?.n)}) over ${windowKey}.`,
                    },
                    `kpi-${spec.name}-propose`,
                  )}
                />
              );
            })}
          </div>
          {noData && (
            <EmptyState
              testId="insights-empty"
              icon={<TrendingUpIcon aria-hidden className="size-6 text-muted-foreground/50" />}
              title="Nothing measured in this window yet"
              body="Numbers fill in as projects open PRs, gates run and people answer decisions. Try a longer window."
            />
          )}
          {group !== "factory" && <GroupTable metrics={grouped.data?.metrics} group={group} />}
          {data ? (
            <>
              {/* With nothing measured, the sections would be dashes only: just the empty state. */}
              {!noData && (
                <>
                  <FlowSection data={data} projectTitle={projectTitle} />
                  <LoopsSection data={data} />
                  <QualitySection data={data} />
                  <PeopleSection data={data} owner={owner} />
                  <CostSection data={data} />
                  <ReliabilitySection data={data} />
                </>
              )}
              {(!noData || hasFindings) && (
                <FindingsSection
                  factoryId={fid}
                  insights={data.insights}
                  proposeFor={(issue: IssueView) =>
                    proposeButton(
                      {
                        evidence: [issue.ref ?? issue.id],
                        why: issue.summary,
                      },
                      `insights-finding-propose-${issue.ref ?? issue.id}`,
                    )
                  }
                />
              )}
              {(!noData || hasExperiments) && <ExperimentsSection factoryId={fid} data={data} />}
            </>
          ) : all.error ? (
            <FactoryLoadError
              error={all.error}
              updatedAt={0}
              onRetry={() => void all.refetch()}
              what="the metrics"
              testId="insights-metrics-error"
            />
          ) : (
            <div className="h-48 animate-pulse rounded-xl bg-muted/70" />
          )}
          <p className="text-xs text-muted-foreground">
            Computed on demand from spans, the cost ledger and the tables (cached 5 min) at{" "}
            {new Date(nowS * 1000).toLocaleTimeString()}. Every number shows n; below n=3 it is
            inconclusive.
          </p>
        </>
      )}
      {proposing && (
        <ProposeChangeDialog
          factoryId={fid}
          open={proposing != null}
          onOpenChange={(open) => !open && setProposing(null)}
          source={proposing}
        />
      )}
    </div>
  );
}
