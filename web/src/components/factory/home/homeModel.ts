// Home as data: the headline and its facts, one row per project (outcome,
// why-line, stage, team), the Needs you rows the card shows (the server's one
// list, capped), the loop strip's stages, the cold-start checklist, and
// tolerant readers for the discovery summary and the ranked opportunities.
// Every word that is not fixed copy comes from the factory's own records;
// nothing here names a product. Pure; `homeModel.test.ts` pins it.

import type { Ask } from "@/lib/factoryAsksApi";
import type {
  AgentRowView,
  DefinitionDoc,
  FactorySnapshot,
  FactoryStage,
  MemberView,
  ProjectView,
  PrView,
} from "@/lib/factoryApi";
import { type TeamMember, groupTeams, modelLabel, teamRows } from "../agents/teamsModel";
import { formatElapsed, formatUsd, plural, primaryPr, projectPrs } from "../factoryFormat";
import type { MemberIdentity } from "../ui/MemberAvatar";
import type { LoopGoal, LoopStage } from "../ui/LoopStrip";
import {
  type AgentState,
  EVIDENCE_LEVELS,
  type EvidenceLevel,
  STAGE_STEP_LABEL,
  type StageStep,
  stageStepOf,
} from "../ui/statusTokens";

/** Needs you shows this many rows, then "and N more". */
export const NEEDS_ROWS_MAX = 5;
/** What users need shows the top opportunities in their Now and Next bands. */
export const USERS_NEED_MAX = 4;

// ── Headline ───────────────────────────────────────────────────

export interface HeadlineInput {
  /** Rows of Needs you; null while it loads. */
  needs: number | null;
  /** Projects agents are working on now. */
  moving: number;
  state: "active" | "paused" | "done" | string;
}

/** `5 things need you. Everything else is moving.` (never a number it does not have). */
export function homeHeadline({ needs, moving, state }: HeadlineInput): string {
  const tail =
    state === "paused"
      ? "The factory is paused."
      : state === "done"
        ? "The factory has finished."
        : moving > 0
          ? null
          : "Nothing is in progress.";
  if (needs == null) {
    return tail ?? `${plural(moving, "project")} ${moving === 1 ? "is" : "are"} moving.`;
  }
  if (needs > 0) {
    const head = `${needs} ${needs === 1 ? "thing needs" : "things need"} you.`;
    return `${head} ${tail ?? "Everything else is moving."}`;
  }
  return `Nothing needs you. ${tail ?? "Everything is moving."}`;
}

export interface FactsInput {
  building: number;
  waitingMerge: number;
  /** The count is the viewer's own merges (Needs you's merge rows), not every ready project. */
  yourMerge?: boolean;
  todayUsd: number | null | undefined;
  dayBudgetUsd: number | null | undefined;
  usdPerH: number | null | undefined;
}

/** `1 building · 4 waiting for your merge · $128 spent today of $240`, zeros skipped. */
export function homeFacts({
  building,
  waitingMerge,
  yourMerge = false,
  todayUsd,
  dayBudgetUsd,
  usdPerH,
}: FactsInput): string[] {
  const facts: string[] = [];
  if (building) facts.push(`${building} building`);
  if (waitingMerge) facts.push(`${waitingMerge} waiting for ${yourMerge ? "your" : "a"} merge`);
  if (todayUsd != null && todayUsd > 0) {
    const of = dayBudgetUsd ? ` of ${money(dayBudgetUsd)}` : "";
    facts.push(`${money(todayUsd)} spent today${of}`);
  } else if (usdPerH != null && usdPerH > 0) {
    facts.push(`${formatUsd(usdPerH)}/h now`);
  }
  return facts;
}

/** `$128` for whole amounts and anything from $100, else `$12.50`. */
export function money(usd: number): string {
  return usd >= 100 || Number.isInteger(usd) ? `$${Math.round(usd)}` : (formatUsd(usd) ?? "");
}

/** `MONDAY 2 NOVEMBER · 18:40` in the viewer's own time zone. */
export function headlineDate(nowS: number, locale?: string): string {
  const at = new Date(nowS * 1000);
  const day = at.toLocaleDateString(locale ?? "en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
  const time = at.toLocaleTimeString(locale ?? "en-GB", { hour: "2-digit", minute: "2-digit" });
  return `${day.replace(",", "")} · ${time}`;
}

// ── Projects ───────────────────────────────────────────────────

/** Why a project exists, in one line (B4 grows this into the project's why-chain). */
export interface WhyLine {
  kind: "evidence" | "requested" | "hunch" | "stopped";
  /** e.g. `from O-3 · measured by weekly publishes`, `No user evidence · requested by mia`. */
  text: string;
  /** The opportunity it comes from, linked. */
  ref?: string | null;
  /** "No user evidence" leads the line (drawn dotted, never as success). */
  noEvidence: boolean;
}

export type RowStripe = "none" | "amber" | "red";

export interface HomeProjectRow {
  slug: string;
  /** What users get (the charter's outcome), else the project's title. */
  title: string;
  why: WhyLine;
  stage: FactoryStage;
  step: StageStep;
  broken: boolean;
  /** Amber only when it needs a person (its merge), red when it stopped. */
  stripe: RowStripe;
  /** `Verify`, `Ready for merge`, `Stopped`. */
  stageLabel: string;
  /** `18m in stage`, or how long it has waited for a merge or been stopped (`5h 50m`). */
  stageAge: string;
  /** The ready PR the stage line names, linked. */
  pr: { ref: string; id: string } | null;
  team: TeamMember[];
}

export interface ShippedLine {
  slug: string;
  title: string;
  /** Requested work without a metric says "not measured", never "success". */
  measured: boolean;
}

function person(userId: string): string {
  return userId.split("@")[0] || userId;
}

function outcome(project: ProjectView): string {
  return project.charter?.outcome?.trim() || project.title || project.slug;
}

/** Why a project exists: from evidence, a hunch, or a person's request; or that it stopped. */
export function whyLine(project: ProjectView): WhyLine {
  if (project.overlay === "failed") {
    const reason = (project.overlay_reason ?? "").split(" · ")[0]?.trim();
    return {
      kind: "stopped",
      text: reason ? `Stopped: ${reason}` : "Stopped",
      noEvidence: false,
    };
  }
  const why = project.why ?? null;
  if (why?.opportunity) {
    const measured = why.metric ? `measured by ${why.metric}` : "not measured yet";
    return {
      kind: "evidence",
      text: `from ${why.opportunity} · ${measured}`,
      ref: why.opportunity,
      noEvidence: false,
    };
  }
  if (why?.kind === "hunch") {
    return {
      kind: "hunch",
      text: "No user evidence · a hunch, counted as one · not measured",
      noEvidence: true,
    };
  }
  const by = project.human_owner ? ` by ${person(project.human_owner)}` : "";
  return {
    kind: "requested",
    text: `No user evidence · requested${by} · not measured`,
    noEvidence: true,
  };
}

function isLive(project: ProjectView): boolean {
  return (project.status == null || project.status === "active") && project.kind !== "integration";
}

const SHIPPED: ReadonlySet<FactoryStage> = new Set(["merged", "done"]);

function group(project: ProjectView): number {
  if (project.overlay === "failed") return 2;
  return project.stage === "ready" ? 1 : 0;
}

const STAGE_ORDER: readonly FactoryStage[] = [
  "queued",
  "planning",
  "building",
  "review",
  "verify",
  "ready",
  "merged",
  "done",
];

function readyPr(prs: readonly PrView[]): PrView | null {
  const pr = primaryPr([...prs]);
  return pr && pr.state === "open" && pr.ready ? pr : null;
}

function stageWords(project: ProjectView, step: StageStep, nowS: number) {
  const age = formatElapsed(nowS - project.stage_since) ?? "";
  if (project.overlay === "failed") return { stageLabel: "Stopped", stageAge: age };
  if (project.stage === "ready") return { stageLabel: STAGE_STEP_LABEL.ready, stageAge: age };
  return { stageLabel: STAGE_STEP_LABEL[step], stageAge: `${age} in stage` };
}

/**
 * One row per live project, working first, then waiting for a merge, then
 * stopped (furthest along and longest in stage first within each); shipped
 * work folds into one line.
 */
export function homeProjects(
  snapshot: FactorySnapshot,
  members: readonly TeamMember[],
  nowS: number,
  /** Projects whose merge waits on the viewer (Needs you's merge rows); null: any ready PR. */
  mergeForYou: ReadonlySet<string> | null = null,
): { rows: HomeProjectRow[]; shipped: ShippedLine[] } {
  const live = snapshot.projects.filter(isLive);
  const shared = new Set(
    snapshot.projects.filter((p) => p.kind === "integration").map((p) => p.slug),
  );
  const { teams } = groupTeams(members, live, shared);
  const teamOf = new Map(teams.map((t) => [t.slug, t.members]));
  const rows = live
    .filter((p) => !SHIPPED.has(p.stage))
    .sort(
      (a, b) =>
        group(a) - group(b) ||
        STAGE_ORDER.indexOf(b.stage) - STAGE_ORDER.indexOf(a.stage) ||
        a.stage_since - b.stage_since ||
        a.slug.localeCompare(b.slug),
    )
    .map((project): HomeProjectRow => {
      const step = stageStepOf(project.stage);
      const pr = project.stage === "ready" ? readyPr(projectPrs(snapshot, project)) : null;
      const broken = project.overlay === "failed";
      return {
        slug: project.slug,
        title: outcome(project),
        why: whyLine(project),
        stage: project.stage,
        step,
        broken,
        stripe: broken
          ? "red"
          : pr && (mergeForYou == null || mergeForYou.has(project.slug))
            ? "amber"
            : "none",
        ...stageWords(project, step, nowS),
        pr: pr ? { ref: `#${pr.number}`, id: pr.id } : null,
        team: teamOf.get(project.slug) ?? [],
      };
    });
  const shipped = live
    .filter((p) => SHIPPED.has(p.stage))
    .map((p) => ({ slug: p.slug, title: outcome(p), measured: Boolean(p.why?.metric) }));
  return { rows, shipped };
}

const SHORT_ROLE: Record<string, [string, string]> = {
  project_lead: ["Lead", "Leads"],
  ic: ["Builder", "Builders"],
  reviewer: ["Reviewer", "Reviewers"],
  demo: ["Demo", "Demos"],
  env_keeper: ["Env", "Envs"],
  planner: ["Planner", "Planners"],
};

/** One role of a row's team line: `Builders` and the models they run. */
export interface TeamLinePart {
  role: string;
  models: string[];
}

/**
 * A team's shape in words, compact for a Home row (J2 without a tap):
 * `Lead opus · Builders sonnet, gpt-5-codex · Reviewer gpt-5-codex`.
 */
export function homeTeamLine(team: readonly TeamMember[]): TeamLinePart[] {
  const byRole = new Map<string, { n: number; models: Set<string> }>();
  for (const m of team) {
    const key = m.row.role ?? "agent";
    const entry = byRole.get(key) ?? { n: 0, models: new Set<string>() };
    entry.n += 1;
    entry.models.add(m.row.model_label ?? modelLabel(m.row.model) ?? m.row.vendor ?? "?");
    byRole.set(key, entry);
  }
  return [...byRole.entries()].map(([key, { n, models }]) => {
    const [one, many] = SHORT_ROLE[key] ?? [m0(key), m0(key)];
    return { role: n > 1 ? many : one, models: [...models] };
  });
}

function m0(role: string): string {
  return role ? role.charAt(0).toUpperCase() + role.slice(1).replace(/_/g, " ") : "Agent";
}

/**
 * Every agent as Teams reads it, from the snapshot alone (Home makes no fleet
 * read): its handle, stuck state, vendor and model come from the member rows.
 */
export function homeAgentRows(snapshot: FactorySnapshot): AgentRowView[] {
  const byId = new Map(snapshot.members.map((m) => [m.session_id, m]));
  return teamRows(snapshot, undefined).map((row) => {
    const member = byId.get(row.session_id);
    return { ...row, vendor: member?.vendor ?? null, model: row.model ?? member?.model ?? null };
  });
}

/** Projects whose ready PR waits on the viewer: the items of Needs you's merge rows. */
export function mergeProjects(asks: readonly Ask[] | null | undefined): ReadonlySet<string> | null {
  if (!asks) return null;
  return new Set(
    asks
      .filter((a) => a.kind === "merge")
      .flatMap((a) => a.items.map((i) => i.project))
      .filter((slug): slug is string => Boolean(slug)),
  );
}

/** Projects agents work on now (planned through verify, not stopped). */
export function movingCount(snapshot: FactorySnapshot): number {
  return snapshot.projects.filter(
    (p) =>
      isLive(p) &&
      p.overlay !== "failed" &&
      p.stage !== "ready" &&
      p.stage !== "queued" &&
      !SHIPPED.has(p.stage),
  ).length;
}

/** Ready PRs that wait on the viewer's merge: the items of Needs you's merge rows. */
export function yourMergeCount(asks: readonly Ask[]): number {
  return asks
    .filter((a) => a.kind === "merge")
    .reduce((n, a) => n + a.items.length + a.more_count, 0);
}

/** Projects whose PR waits for a person's merge (for a server without the asks route). */
export function waitingMergeCount(snapshot: FactorySnapshot): number {
  return snapshot.projects.filter((p) => isLive(p) && p.stage === "ready" && p.overlay !== "failed")
    .length;
}

// ── Needs you ──────────────────────────────────────────────────

export type NeedsFilter = "all" | "mine";

/** The rows the card shows: all (or only those assigned to the viewer by name), capped. */
export function needsView(
  asks: readonly Ask[],
  filter: NeedsFilter,
  expanded: boolean,
  hidden: ReadonlySet<string> = new Set(),
): { shown: Ask[]; more: number; total: number } {
  const list = asks.filter((a) => !hidden.has(a.id) && (filter === "all" || a.mine));
  const shown = expanded ? list : list.slice(0, NEEDS_ROWS_MAX);
  return { shown, more: list.length - shown.length, total: list.length };
}

/** `expires in 4h` (or `defaults now`) for a deadline still ahead. */
export function deadlineText(at: number | null | undefined, nowS: number): string | null {
  if (at == null) return null;
  const left = at - nowS;
  if (left <= 0) return "due now";
  return `due in ${formatElapsed(left)}`;
}

/** The row's facts: meta (project, who asks, for whom), the cost of waiting, age, deadline. */
export function askMeta(ask: Ask, nowS: number): string[] {
  const parts = [...ask.meta];
  if (ask.cost_of_waiting.text) parts.push(ask.cost_of_waiting.text);
  const age = ask.since ? formatElapsed(nowS - ask.since) : null;
  if (age) parts.push(age);
  const due = deadlineText(ask.deadline_at, nowS);
  if (due) parts.push(due);
  return parts;
}

// ── The loop strip ─────────────────────────────────────────────

/** What Home reads from `GET …/discovery` (absent fields are unknown, never zero). */
export interface DiscoveryHomeFacts {
  signalsWeek: number | null;
  signalsUnsorted: number | null;
  opportunities: number | null;
  opportunitiesNow: number | null;
  bets: number | null;
  readouts: number | null;
  readoutsWon: number | null;
  /** When the first weekly synthesis runs (epoch seconds), before any opportunity. */
  nextSynthesisAt: number | null;
  learned: LearnedLine[];
}

export interface LearnedLine {
  text: string;
  /** An id it is about (`O-24`), linked. */
  ref: string | null;
}

function obj(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function num(...values: unknown[]): number | null {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function str(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

/**
 * The discovery summary, read field by field (the ledger's summary route may
 * name its counts flat, under `chain`, or per stage): null when there is none.
 */
export function parseDiscoveryFacts(raw: unknown): DiscoveryHomeFacts | null {
  const body = obj(raw);
  if (!body) return null;
  const chain = obj(body.chain) ?? {};
  const signals = obj(body.signals) ?? {};
  const opps = obj(body.opportunities) ?? {};
  const bets = obj(body.bets) ?? {};
  const readouts = obj(body.readouts) ?? {};
  const learned = Array.isArray(body.learned) ? body.learned : [];
  return {
    signalsWeek: num(body.signals_week, signals.week, chain.signals),
    signalsUnsorted: num(body.signals_unsorted, signals.unsorted),
    opportunities: num(body.opportunities_open, opps.open, chain.opportunities, body.opportunities),
    opportunitiesNow: num(body.opportunities_now, opps.now),
    bets: num(body.bets_open, bets.open, chain.bets, body.bets),
    readouts: num(body.readouts_total, readouts.total, chain.measured, body.readouts),
    readoutsWon: num(body.readouts_won, readouts.won),
    nextSynthesisAt: num(body.next_synthesis_at, obj(body.cadence)?.next_synthesis_at),
    learned: learned
      .map((line): LearnedLine | null => {
        const row = obj(line);
        const text = typeof line === "string" ? line : str(row?.text);
        return text ? { text, ref: str(row?.ref, row?.id) } : null;
      })
      .filter((line): line is LearnedLine => line != null),
  };
}

/** One ranked opportunity as Home lists it. */
export interface OpportunityRow {
  id: string;
  title: string;
  band: "now" | "next" | "later" | "parked" | null;
  score: number | null;
  level: EvidenceLevel | null;
  accounts: number | null;
  status: string | null;
}

/** The opportunities list, read row by row: null when there is none. */
export function parseOpportunities(raw: unknown): OpportunityRow[] | null {
  const body = obj(raw);
  const list = Array.isArray(raw)
    ? raw
    : Array.isArray(body?.data)
      ? (body!.data as unknown[])
      : null;
  if (!list) return null;
  return list
    .map((item): OpportunityRow | null => {
      const row = obj(item);
      const id = str(row?.ref, row?.id);
      const title = str(row?.title);
      if (!row || !id || !title) return null;
      const evidence = obj(row.evidence);
      const level = str(row.level, evidence?.level) as EvidenceLevel | null;
      const band = str(row.band) as OpportunityRow["band"];
      return {
        id,
        title,
        band: band && ["now", "next", "later", "parked"].includes(band) ? band : null,
        score: num(row.score, obj(row.score)?.rank, row.rank),
        level: level && EVIDENCE_LEVELS.includes(level) ? level : null,
        accounts: num(row.accounts, evidence?.accounts),
        status: str(row.status),
      };
    })
    .filter((row): row is OpportunityRow => row != null);
}

/** The top opportunities in their Now and Next bands (parked never). */
export function usersNeed(rows: readonly OpportunityRow[] | null | undefined): OpportunityRow[] {
  return (rows ?? [])
    .filter((row) => row.band !== "parked" && row.band !== "later")
    .slice(0, USERS_NEED_MAX);
}

export interface LoopLeads {
  discovery?: MemberIdentity;
  portfolio?: MemberIdentity;
  reliability?: MemberIdentity;
}

/** The three lead seats as the loop strip draws them over the stages they own. */
export function loopLeads(snapshot: FactorySnapshot, members: readonly TeamMember[]): LoopLeads {
  const view = new Map(snapshot.members.map((m) => [m.session_id, m]));
  const out: LoopLeads = {};
  for (const m of members) {
    if (!m.seat || out[m.seat]) continue;
    const row = view.get(m.row.session_id) as MemberView | undefined;
    out[m.seat] = {
      name: m.name,
      role: m.row.role,
      seat: m.seat,
      vendor: (row?.vendor as MemberIdentity["vendor"]) ?? null,
      model: m.row.model_label ?? null,
      state: AVATAR_OF[m.state],
    };
  }
  return out;
}

const AVATAR_OF: Record<TeamMember["state"], AgentState> = {
  working: "working",
  waiting: "waiting",
  stuck: "stuck",
  stopped: "dead",
  idle: "idle",
  done: "done",
};

/**
 * Whether discovery runs: this server has it on (`OMNIGENT_FACTORY_DISCOVERY`)
 * and the factory's definition turns it on. Either alone keeps it dark.
 */
export function discoveryRuns(
  definition: { discovery_server?: boolean; effective?: DefinitionDoc | null } | null | undefined,
): boolean {
  return definition?.discovery_server === true && definition.effective?.discovery?.enabled === true;
}

export interface LoopInput {
  snapshot: FactorySnapshot;
  definition: DefinitionDoc | null | undefined;
  /** Discovery runs (see `discoveryRuns`): the server's switch and the definition's. */
  discoveryOn: boolean;
  asks: readonly Ask[] | null | undefined;
  discovery: DiscoveryHomeFacts | null | undefined;
  leads: LoopLeads;
  /** Hrefs of the places each stage opens. */
  hrefs: { signals: string; opportunities: string; flow: string; projects: string; impact: string };
}

export interface HomeLoop {
  discoveryOn: boolean;
  goal: LoopGoal | null;
  stages: LoopStage[];
}

function countKind(asks: readonly Ask[] | null | undefined, kind: Ask["kind"]): number {
  return (asks ?? []).filter((a) => a.kind === kind).length;
}

/**
 * Sense → Make sense → Decide → Build → Measure, from what the factory
 * knows: its sources and goals (the definition), what waits on the viewer
 * (Needs you), its projects (the snapshot) and, once the ledger answers, the
 * discovery counts. A count nobody knows yet says so instead of a zero.
 */
export function homeLoop({
  snapshot,
  definition,
  discoveryOn,
  asks,
  discovery,
  leads,
  hrefs,
}: LoopInput): HomeLoop {
  const sources = definition?.discovery?.sources ?? [];
  const on = sources.filter((s) => s.state !== "paused").length;
  const sourceAsks = countKind(asks, "source");
  const goals = definition?.strategy?.goals ?? [];
  const first = goals[0] ?? null;
  const goal: LoopGoal | null = !discoveryOn
    ? null
    : first
      ? {
          id: first.id,
          statement: first.statement,
          measured: first.metric != null,
          note: first.metric ? undefined : "No source can measure it yet.",
        }
      : { statement: null, measured: false, note: "Opportunities are ranked against goals." };
  const live = snapshot.projects.filter((p) => isLive(p) && p.overlay !== "failed");
  const active = live.filter((p) => !SHIPPED.has(p.stage));
  const fromEvidence = active.filter((p) => p.why?.opportunity).length;
  const signals = discovery?.signalsWeek ?? null;
  const opps = discovery?.opportunities ?? null;
  const betAsks = countKind(asks, "bet");
  const bets = discovery?.bets ?? betAsks;
  const readouts = discovery?.readouts ?? 0;
  const won = discovery?.readoutsWon ?? 0;
  const stages: LoopStage[] = [
    {
      key: "sense",
      count: signals ?? on,
      unit: signals != null ? "signals this week" : on === 1 ? "source on" : "sources on",
      detail: signals != null ? `${on} ${on === 1 ? "source" : "sources"} on` : undefined,
      waiting: sourceAsks
        ? `${sourceAsks} ${sourceAsks === 1 ? "waits" : "wait"} for you`
        : undefined,
      owner: leads.discovery,
      to: hrefs.signals,
    },
    {
      key: "make_sense",
      count: opps ?? 0,
      unit: opps === 1 ? "opportunity" : "opportunities",
      detail:
        opps == null || opps === 0
          ? "first synthesis not run yet"
          : [
              discovery?.opportunitiesNow != null ? `${discovery.opportunitiesNow} in Now` : null,
              discovery?.signalsUnsorted ? `${discovery.signalsUnsorted} signals unsorted` : null,
            ]
              .filter(Boolean)
              .join(" · ") || undefined,
      owner: leads.discovery,
      to: hrefs.opportunities,
    },
    {
      key: "decide",
      count: bets,
      unit: bets === 1 ? "bet" : "bets",
      detail: bets ? undefined : "plans come from the top opportunities",
      waiting: betAsks ? `${betAsks} ${betAsks === 1 ? "waits" : "wait"} for you` : undefined,
      owner: leads.portfolio,
      to: hrefs.flow,
    },
    {
      key: "build",
      count: active.length,
      unit: active.length === 1 ? "project" : "projects",
      detail: `${fromEvidence} from evidence · ${active.length - fromEvidence} requested`,
      owner: leads.portfolio,
      to: hrefs.projects,
    },
    {
      key: "measure",
      count: readouts,
      unit: readouts === 1 ? "readout" : "readouts",
      detail: readouts
        ? `${won} won · requested work is not measured`
        : "nothing shipped from a bet yet",
      owner: leads.reliability,
      to: hrefs.impact,
    },
  ];
  return { discoveryOn, goal, stages };
}

// ── Cold start ─────────────────────────────────────────────────

export interface ChecklistStep {
  label: string;
  done: boolean;
}

/**
 * "Getting discovery going" while discovery runs (`discoveryOn`: the server's
 * switch and the definition's) and a step is still open: turned on, starter
 * sources approved, the product profile reviewed, a first goal, the first
 * weekly synthesis. Null once every step is done (or while it does not run).
 */
export function discoveryChecklist(
  definition: DefinitionDoc | null | undefined,
  asks: readonly Ask[] | null | undefined,
  discovery: DiscoveryHomeFacts | null | undefined,
  discoveryOn: boolean,
): ChecklistStep[] | null {
  const disc = definition?.discovery;
  if (!discoveryOn || !disc) return null;
  const product = definition?.product;
  const steps: ChecklistStep[] = [
    { label: "Turn on discovery", done: true },
    {
      label: "Approve the starter sources",
      done: (disc.sources ?? []).length > 0 && countKind(asks, "source") === 0,
    },
    {
      label: "Review the product profile",
      done: Boolean(product?.name || product?.summary) && countKind(asks, "profile") === 0,
    },
    { label: "Set a first goal", done: (definition?.strategy?.goals ?? []).length > 0 },
    { label: "First weekly synthesis", done: (discovery?.opportunities ?? 0) > 0 },
  ];
  return steps.every((s) => s.done) ? null : steps;
}

/** The factory's daily budget, when its definition sets one. */
export function dayBudget(definition: DefinitionDoc | null | undefined): number | null {
  const budgets = obj(definition?.budgets);
  return num(budgets?.factory_usd_per_day);
}
