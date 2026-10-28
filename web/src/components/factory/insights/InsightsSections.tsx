/**
 * The Insights page below the tiles: Flow (stage funnel, cumulative flow,
 * where the time went), Loops, Quality, People (per-person numbers for
 * owners only), Cost, Reliability, Findings and retros, and Experiments.
 * Every number carries n; a section without samples says so instead of
 * drawing an empty chart.
 */

import type { ReactNode } from "react";
import { FlaskConicalIcon } from "lucide-react";
import {
  FACTORY_STAGES,
  type InsightsResponse,
  type IssueView,
  type MetricResult,
} from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { STAGE_LABEL } from "../factoryFormat";
import { alertPageHref, improvementsPageHref } from "../factoryRoutes";
import { SectionCard } from "../project/projectParts";
import { type DayMark, DayBarChart, MeterList, Stat, StackedRows } from "./InsightsCharts";
import {
  byRows,
  countRows,
  cumulative,
  formatValue,
  headline,
  humanize,
  mixRows,
  nText,
} from "./insightsModel";

export interface InsightsData {
  /** Every catalog metric over the window (ungrouped). */
  all: Record<string, MetricResult>;
  /** Grouped by UTC day. */
  byDay: Record<string, MetricResult>;
  /** `human_wait` per project. */
  waitByProject: MetricResult | undefined;
  /** `cost_usd` per role. */
  costByRole: MetricResult | undefined;
  insights: InsightsResponse;
  days: readonly string[];
  dayMarks: readonly DayMark[];
}

function n(metric: MetricResult | undefined): string {
  return nText(metric?.n ?? 0);
}

function val(metric: MetricResult | undefined, unit?: string): string {
  return formatValue(headline(metric), unit ?? metric?.unit ?? "");
}

function p5090(metric: MetricResult | undefined): string {
  if (!metric || metric.p50 == null) return "—";
  return `${formatValue(metric.p50, metric.unit)} / ${formatValue(metric.p90 ?? null, metric.unit)}`;
}

function share(value: unknown): string {
  return typeof value === "number" ? formatValue(value, "share") : "—";
}

function SubHead({ children, meta }: { children: ReactNode; meta?: ReactNode }) {
  return (
    <h3 className="flex min-w-0 items-baseline gap-2 text-sm font-medium">
      {children}
      {meta && <span className="text-xs font-normal text-muted-foreground">{meta}</span>}
    </h3>
  );
}

function Grid({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("grid min-w-0 gap-4 md:grid-cols-2", className)}>{children}</div>;
}

function Stats({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-w-0 grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3 lg:grid-cols-6">
      {children}
    </div>
  );
}

function Block({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <div data-testid={testId} className="flex min-w-0 flex-col gap-2">
      {children}
    </div>
  );
}

// ── Flow ───────────────────────────────────────────────────────

export function FlowSection({
  data,
  projectTitle,
}: {
  data: InsightsData;
  projectTitle: (slug: string) => string;
}) {
  const { all, byDay, days, dayMarks, waitByProject } = data;
  const throughput = all.throughput;
  const byStage = (throughput?.projects_by_stage as Record<string, number> | undefined) ?? {};
  const funnel = FACTORY_STAGES.filter((s) => s !== "done").map((stage) => ({
    key: stage,
    label: STAGE_LABEL[stage],
    value: byStage[stage] ?? 0,
  }));
  const merged = days.map((day) => {
    const group = byDay.throughput?.groups?.[day];
    return typeof group?.total === "number" ? group.total : null;
  });
  const where = Object.entries(waitByProject?.groups ?? {})
    .filter(([slug]) => slug)
    .map(([slug, agg]) => ({
      key: slug,
      label: projectTitle(slug),
      values: {
        agent: headline(agg.by?.agent ?? null),
        decision: headline(agg.by?.decision ?? null),
        human_merge: headline(agg.by?.human_merge ?? null),
      },
    }))
    .sort(
      (a, b) =>
        (b.values.agent ?? 0) +
        (b.values.decision ?? 0) +
        (b.values.human_merge ?? 0) -
        ((a.values.agent ?? 0) + (a.values.decision ?? 0) + (a.values.human_merge ?? 0)),
    );
  const parallel = all.parallelism;
  return (
    <SectionCard title="Flow" meta={n(all.lead_time)} testId="insights-flow">
      <Stats>
        <Stat label="Lead time p50 / p90" value={p5090(all.lead_time)} hint={n(all.lead_time)} />
        <Stat
          label="Time to first PR"
          value={p5090(all.time_to_first_pr)}
          hint={n(all.time_to_first_pr)}
        />
        <Stat
          label="Task cycle p50 / p90"
          value={p5090(all.task_cycle_time)}
          hint={n(all.task_cycle_time)}
        />
        <Stat
          label="Merged per day"
          value={val(throughput, "per_day")}
          hint={`open PRs ${String(throughput?.open_prs ?? "—")} · previews ${String(throughput?.live_previews ?? "—")}`}
        />
        <Stat
          label="Parallel tasks"
          value={parallel?.peak != null ? `peak ${parallel.peak}` : "—"}
          hint={
            parallel?.mean != null
              ? `mean ${parallel.mean.toFixed(1)} · ${n(parallel)}`
              : n(parallel)
          }
        />
        <Stat
          label="Critical-path accuracy"
          value={formatValue(all.critical_path_accuracy?.p50 ?? null, "ratio")}
          hint={n(all.critical_path_accuracy)}
        />
      </Stats>
      <Grid>
        <Block testId="insights-funnel">
          <SubHead meta="active projects now">Stage funnel</SubHead>
          <MeterList
            rows={funnel}
            unit="count"
            testId="insights-funnel-rows"
            empty="No active projects"
          />
        </Block>
        <Block testId="insights-dwell">
          <SubHead meta="p50 / p90">Stage dwell</SubHead>
          <MeterList
            rows={byRows(all.stage_dwell).map((r) => ({
              ...r,
              label: STAGE_LABEL[r.key as keyof typeof STAGE_LABEL] ?? humanize(r.key),
              extra: formatValue(r.p90, "s"),
            }))}
            unit="s"
            testId="insights-dwell-rows"
            empty="No closed stages yet"
          />
        </Block>
      </Grid>
      <Grid>
        <Block testId="insights-cumulative">
          <SubHead meta="merged PRs, running total">Cumulative flow</SubHead>
          <DayBarChart
            days={days}
            values={merged.some((v) => v != null) ? cumulative(merged) : merged}
            unit="count"
            marks={dayMarks}
            height={150}
            testId="insights-cumulative-chart"
            empty="Nothing merged in this window"
          />
        </Block>
        <Block testId="insights-where">
          <SubHead meta="per project, summed">Where the time went</SubHead>
          <StackedRows
            rows={where}
            parts={[
              { key: "agent", label: "agents (open → ready)", tone: "bg-chart-1" },
              { key: "decision", label: "human decisions", tone: "bg-chart-4" },
              { key: "human_merge", label: "human merge (ready → merged)", tone: "bg-chart-5" },
            ]}
            unit="s"
            testId="insights-where-rows"
            empty="No merged PRs or answered blocking decisions yet"
          />
        </Block>
      </Grid>
    </SectionCard>
  );
}

// ── Loops ──────────────────────────────────────────────────────

export function LoopsSection({ data }: { data: InsightsData }) {
  const { all } = data;
  return (
    <SectionCard title="Loops" meta={n(all.loop_exit_mix)} testId="insights-loops">
      <Stats>
        <Stat
          label="Exhaustion rate"
          value={val(all.loop_exhaustion_rate)}
          hint={n(all.loop_exhaustion_rate)}
        />
        <Stat
          label="Review rounds p50 / p90"
          value={p5090(all.review_rounds)}
          hint={n(all.review_rounds)}
        />
        <Stat
          label="Budget extensions"
          value={formatValue(
            byRows(all.budget_extensions).reduce((s, r) => s + (r.value ?? 0), 0),
            "count",
          )}
          hint={n(all.budget_extensions)}
        />
      </Stats>
      <Grid className="lg:grid-cols-3">
        <Block testId="insights-iterations">
          <SubHead meta="converged loops, p50 / p90">Iterations to converge</SubHead>
          <MeterList
            rows={byRows(all.loop_iterations).map((r) => ({
              ...r,
              extra: r.p90 != null ? formatValue(r.p90, "iterations") : null,
            }))}
            unit="iterations"
            testId="insights-iterations-rows"
            empty="No converged loops yet"
          />
        </Block>
        <Block testId="insights-exits">
          <SubHead meta="loops that ended">Exit reasons</SubHead>
          <MeterList
            rows={mixRows(all.loop_exit_mix).map((r) => ({
              key: r.key,
              value: r.share,
              n: r.count,
            }))}
            unit="share"
            testId="insights-exits-rows"
            empty="No loop ended yet"
          />
        </Block>
        <Block testId="insights-first-pass">
          <SubHead meta="heads passing on the first run">First-pass yield</SubHead>
          <MeterList
            rows={byRows(all.first_pass_yield)}
            unit="share"
            testId="insights-first-pass-rows"
            tone="bg-success"
            empty="No gate runs yet"
          />
        </Block>
      </Grid>
    </SectionCard>
  );
}

// ── Quality ────────────────────────────────────────────────────

export function QualitySection({ data }: { data: InsightsData }) {
  const { all } = data;
  const ratings = all.human_ratings;
  const distribution = ["1", "2", "3", "4", "5"].map((stars) => ({
    key: stars,
    label: `${stars}★`,
    value: ((ratings?.distribution as Record<string, number> | undefined) ?? {})[stars] ?? 0,
  }));
  const closures = countRows(all.merge_acceptance?.closure_reasons);
  return (
    <SectionCard title="Quality" meta={n(all.merge_acceptance)} testId="insights-quality">
      <Stats>
        <Stat
          label="Merge acceptance"
          value={val(all.merge_acceptance)}
          hint={n(all.merge_acceptance)}
        />
        <Stat label="Escape rate" value={val(all.escape_rate)} hint={n(all.escape_rate)} />
        <Stat
          label="Sentinel agreement"
          value={val(all.sentinel_agreement)}
          hint={n(all.sentinel_agreement)}
        />
        <Stat
          label="Claim mismatches"
          value={val(all.claim_mismatch_rate)}
          hint={n(all.claim_mismatch_rate)}
        />
        <Stat label="PR rating" value={val(ratings, "rating")} hint={n(ratings)} />
        <Stat label="Demo useful 👍" value={share(ratings?.demo_useful)} />
      </Stats>
      <Grid>
        <Block testId="insights-closures">
          <SubHead meta="closed without merge">Closure reasons</SubHead>
          <MeterList
            rows={closures.map((r) => ({ key: r.key, value: r.count }))}
            unit="count"
            testId="insights-closure-rows"
            tone="bg-chart-4"
            empty="No closure reasons recorded"
          />
        </Block>
        <Block testId="insights-ratings">
          <SubHead meta="human PR ratings">Ratings</SubHead>
          <MeterList
            rows={distribution}
            unit="count"
            testId="insights-rating-rows"
            empty="No ratings yet"
          />
        </Block>
      </Grid>
    </SectionCard>
  );
}

// ── People ─────────────────────────────────────────────────────

export function PeopleSection({ data, owner }: { data: InsightsData; owner: boolean }) {
  const { all, insights } = data;
  const people = Object.entries(insights.people ?? {});
  const latency = all.decision_latency;
  return (
    <SectionCard title="People" meta="human load and response" testId="insights-people">
      <Stats>
        <Stat
          label="Decision latency p50 / p90"
          value={p5090(latency)}
          hint={
            typeof latency?.escalation_rate === "number"
              ? `escalated ${formatValue(latency.escalation_rate, "share")} · ${n(latency)}`
              : n(latency)
          }
        />
        <Stat
          label="Unnecessary questions"
          value={val(all.unnecessary_questions)}
          hint={n(all.unnecessary_questions)}
        />
        <Stat
          label="Chat reply p50"
          value={formatValue(all.chat_reply_time?.p50 ?? null, "s")}
          hint={
            typeof all.chat_reply_time?.auto_reply_share === "number"
              ? `auto ${formatValue(all.chat_reply_time.auto_reply_share, "share")} · ${n(all.chat_reply_time)}`
              : n(all.chat_reply_time)
          }
        />
        <Stat
          label="Interventions / merged PR"
          value={formatValue(
            typeof all.interventions_per_merged_pr?.value === "number"
              ? all.interventions_per_merged_pr.value
              : null,
            "acts",
          )}
          hint={n(all.interventions_per_merged_pr)}
        />
        <Stat
          label="Prompts per day"
          value={val(all.human_prompts_per_day, "per_day")}
          hint="band 30–50"
        />
      </Stats>
      {owner ? (
        people.length === 0 ? (
          <p data-testid="insights-people-empty" className="text-sm text-muted-foreground">
            Nobody answered, posted or acted in this window.
          </p>
        ) : (
          <div className="min-w-0 overflow-x-auto">
            <ul
              data-testid="insights-people-cards"
              className="flex min-w-0 flex-col divide-y divide-border sm:hidden"
            >
              {people.map(([person, row]) => (
                <li key={person} className="flex min-w-0 flex-col py-1.5 text-sm">
                  <span className="truncate font-medium">{person}</span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {row.decisions_answered} decisions · p50 {formatValue(row.decision_p50_s, "s")}{" "}
                    · {row.chat_posts} posts · {row.interventions} interventions
                    {row.escalated_away ? ` · ${row.escalated_away} escalated away` : ""}
                  </span>
                </li>
              ))}
            </ul>
            <table data-testid="insights-people-table" className="w-full text-sm max-sm:hidden">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="py-1.5 pr-3 font-medium">Person</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Decisions</th>
                  <th className="py-1.5 pr-3 text-right font-medium">p50 latency</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Escalated away</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Chat posts</th>
                  <th className="py-1.5 text-right font-medium">Interventions</th>
                </tr>
              </thead>
              <tbody>
                {people.map(([person, row]) => (
                  <tr key={person} className="border-b border-border/60 last:border-0">
                    <td className="max-w-[14rem] truncate py-1.5 pr-3">{person}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">
                      {row.decisions_answered}
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">
                      {formatValue(row.decision_p50_s, "s")}
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{row.escalated_away}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{row.chat_posts}</td>
                    <td className="py-1.5 text-right tabular-nums">{row.interventions}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : (
        <p data-testid="insights-people-owners-only" className="text-sm text-muted-foreground">
          Per-person numbers are visible to owners only.
        </p>
      )}
    </SectionCard>
  );
}

// ── Cost ───────────────────────────────────────────────────────

export function CostSection({ data }: { data: InsightsData }) {
  const { all, byDay, days, dayMarks, costByRole } = data;
  const perDay = days.map((day) => headline(byDay.cost_usd?.groups?.[day] ?? null));
  const roles = Object.entries(costByRole?.groups ?? {})
    .map(([role, agg]) => ({ key: role || "leads", value: headline(agg), n: agg.n }))
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  const models = Object.entries((all.cost_usd?.by_model as Record<string, number>) ?? {})
    .map(([model, usd]) => ({ key: model, label: model, value: usd }))
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  const heartbeat = all.heartbeat_share;
  return (
    <SectionCard
      title="Cost"
      meta={`${val(all.cost_usd, "usd")} in this window`}
      testId="insights-cost"
    >
      <Stats>
        <Stat label="Spent" value={val(all.cost_usd, "usd")} hint={n(all.cost_usd)} />
        <Stat
          label="$ / merged PR"
          value={val(all.cost_per_merged_pr, "usd")}
          hint={n(all.cost_per_merged_pr)}
        />
        <Stat
          label="Heartbeat & idle share"
          value={share(heartbeat?.value)}
          hint={
            typeof heartbeat?.usd_estimate === "number"
              ? `≈ ${formatValue(heartbeat.usd_estimate, "usd")}`
              : n(heartbeat)
          }
        />
        <Stat
          label="$ wasted in exhausted loops"
          value={val(all.wasted_usd, "usd")}
          hint={n(all.wasted_usd)}
        />
      </Stats>
      <Block testId="insights-cost-days">
        <SubHead meta="display cost, unpriced harnesses excluded">By day</SubHead>
        <DayBarChart
          days={days}
          values={perDay}
          unit="usd"
          marks={dayMarks}
          height={150}
          testId="insights-cost-chart"
          empty="No spend recorded in this window"
        />
      </Block>
      <Grid>
        <Block testId="insights-cost-roles">
          <SubHead>By role</SubHead>
          <MeterList rows={roles} unit="usd" testId="insights-cost-role-rows" />
        </Block>
        <Block testId="insights-cost-models">
          <SubHead>By model</SubHead>
          <MeterList rows={models} unit="usd" testId="insights-cost-model-rows" tone="bg-chart-2" />
        </Block>
      </Grid>
    </SectionCard>
  );
}

// ── Reliability ────────────────────────────────────────────────

export function ReliabilitySection({ data }: { data: InsightsData }) {
  const { all } = data;
  const mail = all.mail_health;
  const env = all.env_availability;
  return (
    <SectionCard title="Reliability" meta={n(all.incidents)} testId="insights-reliability">
      <Stats>
        <Stat
          label="Incidents · MTTR p50"
          value={`${val(all.incidents, "count")}${typeof all.incidents?.mttr_p50_s === "number" ? ` · ${formatValue(all.incidents.mttr_p50_s, "s")}` : ""}`}
          hint={n(all.incidents)}
        />
        <Stat
          label="Sub-agent failures"
          value={val(all.subagent_failure_rate)}
          hint={n(all.subagent_failure_rate)}
        />
        <Stat
          label="Mail bounces"
          value={share(mail?.bounce_rate)}
          hint={
            typeof mail?.queue_age_p90_s === "number"
              ? `queue p90 ${formatValue(mail.queue_age_p90_s, "s")}`
              : n(mail)
          }
        />
        <Stat label="Env ready" value={share(env?.env_ready_share)} hint={n(env)} />
        <Stat
          label="Preview ready"
          value={share(env?.preview_ready_share)}
          hint={
            typeof env?.sha_mismatches === "number" ? `sha mismatches ${env.sha_mismatches}` : null
          }
        />
        <Stat
          label="NEXT wakes"
          value={val(all.engine_wake_efficiency)}
          hint={n(all.engine_wake_efficiency)}
        />
      </Stats>
    </SectionCard>
  );
}

// ── Findings and retros ────────────────────────────────────────

function FindingRow({
  factoryId,
  issue,
  action,
}: {
  factoryId: string;
  issue: IssueView;
  action?: ReactNode;
}) {
  return (
    <li
      data-testid={`insights-finding-${issue.ref ?? issue.id}`}
      className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 py-1.5 text-sm"
    >
      <Link
        to={alertPageHref(factoryId, issue.ref ?? issue.id)}
        componentId="factory.insights.finding"
        className="font-mono text-xs text-muted-foreground hover:text-foreground hover:underline"
      >
        {issue.ref ?? "F"}
      </Link>
      <span className="min-w-0 flex-1 truncate">{issue.summary}</span>
      <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
        {issue.project_slug ? `${issue.project_slug} · ` : ""}×{issue.count}
      </span>
      {action}
    </li>
  );
}

export function FindingsSection({
  factoryId,
  insights,
  proposeFor,
}: {
  factoryId: string;
  insights: InsightsResponse;
  /** The finding's "Propose change" button. */
  proposeFor: (issue: IssueView) => ReactNode;
}) {
  return (
    <SectionCard
      title="Findings and retros"
      meta={`open findings ${insights.findings_open} · incidents ${insights.incidents_open}`}
      testId="insights-findings"
      action={
        <Link
          to={improvementsPageHref(factoryId, "findings")}
          componentId="factory.insights.all_findings"
          className="text-sm text-muted-foreground hover:text-foreground hover:underline"
        >
          All findings →
        </Link>
      }
    >
      <Grid>
        <Block>
          <SubHead meta="most frequent first">Findings</SubHead>
          {insights.findings.length === 0 ? (
            <p className="text-sm text-muted-foreground">No open findings.</p>
          ) : (
            <ul className="flex min-w-0 flex-col divide-y divide-border">
              {insights.findings.map((issue) => (
                <FindingRow
                  key={issue.id}
                  factoryId={factoryId}
                  issue={issue}
                  action={proposeFor(issue)}
                />
              ))}
            </ul>
          )}
        </Block>
        <Block testId="insights-retros">
          <SubHead meta="latest per project">Retros</SubHead>
          {insights.retros.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No retro yet: Sentinel files one daily and when a project is done.
            </p>
          ) : (
            <ul className="flex min-w-0 flex-col divide-y divide-border">
              {insights.retros.map((retro) => (
                <li key={retro.id} className="flex min-w-0 flex-col py-1.5 text-sm">
                  <span className="flex min-w-0 items-baseline gap-2">
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {retro.project_slug ?? "factory"}
                    </span>
                    <span className="min-w-0 truncate">{retro.summary}</span>
                  </span>
                  {retro.category && (
                    <span className="text-xs text-muted-foreground">
                      root cause: {humanize(retro.category)}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Block>
      </Grid>
    </SectionCard>
  );
}

// ── Experiments ────────────────────────────────────────────────

export function ExperimentsSection({ factoryId, data }: { factoryId: string; data: InsightsData }) {
  const { experiments } = data.insights;
  const health = data.all.proposal_health;
  return (
    <SectionCard
      title="Experiments"
      meta="definition changes on trial, kept and reverted"
      testId="insights-experiments"
      action={
        <Link
          to={improvementsPageHref(factoryId, "history")}
          componentId="factory.insights.experiments"
          className="text-sm text-muted-foreground hover:text-foreground hover:underline"
        >
          Improvements →
        </Link>
      }
    >
      <Stats>
        <Stat
          label="On trial"
          value={
            <span className="inline-flex items-center gap-1">
              <FlaskConicalIcon aria-hidden className="size-3.5 text-muted-foreground" />
              {experiments.on_trial}
            </span>
          }
          testId="insights-experiments-trial"
        />
        <Stat label="Kept" value={experiments.kept} tone="text-success" />
        <Stat
          label="Reverted"
          value={experiments.reverted}
          tone={experiments.reverted ? "text-warning" : undefined}
        />
        <Stat label="Proposal acceptance" value={val(health)} hint={n(health)} />
        <Stat
          label="Undo rate"
          value={share(health?.undo_rate)}
          hint={typeof health?.edited === "number" ? `edited ${health.edited}` : null}
        />
      </Stats>
    </SectionCard>
  );
}
