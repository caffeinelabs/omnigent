// Discovery as data: tolerant readers for the ledger's answers (the summary,
// opportunities, signals and, once the server has them, readouts and the
// prediction ledger), the table's bands and status words, the triage keys,
// the Flow picture's lanes and its chain walk, the value × confidence map,
// the Impact cards and the discovery starter an owner applies to turn it on.
// Every word that is not fixed copy comes from the factory's own records;
// nothing here names a product. Pure; `discoveryModel.test.ts` pins it.

import type {
  DefinitionDoc,
  FactorySnapshot,
  ProjectView,
  ProposalView,
  SourceDef,
} from "@/lib/factoryApi";
import { keyOwnedByTarget } from "../cmdk/useFactoryPaletteHotkey";
import {
  STARTER_SWEEP_H,
  STARTER_SYNTHESIS_H,
  STARTER_USD_PER_DAY,
  missingStarterSources,
} from "./discoveryStarter";
import { plural } from "../factoryFormat";
import {
  EVIDENCE_LEVELS,
  type EvidenceLevel,
  type ReadoutVerdict,
  STAGE_STEP_LABEL,
  stageStepOf,
} from "../ui/statusTokens";

// ── Readers ────────────────────────────────────────────────────

type Obj = Record<string, unknown>;

function obj(value: unknown): Obj | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Obj) : null;
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
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

function bool(value: unknown): boolean {
  return value === true;
}

function strings(value: unknown): string[] {
  return list(value).filter((v): v is string => typeof v === "string" && v.length > 0);
}

function level(value: unknown): EvidenceLevel | "none" {
  return typeof value === "string" && (EVIDENCE_LEVELS as readonly string[]).includes(value)
    ? (value as EvidenceLevel)
    : "none";
}

// ── The summary (`GET …/discovery`) ────────────────────────────

export type SourceState = "trial" | "active" | "paused" | "pending";

export interface SourceView {
  id: string;
  label: string;
  kind: string;
  state: SourceState | string;
  privacy: string | null;
  keepDays: number | null;
  callsToday: number | null;
  callsPerDay: number | null;
  /** `ok`, `new`, `erroring`, `failing` (on hold after repeated failures). */
  health: string | null;
  lastOkAt: number | null;
  lastError: string | null;
  signalsTotal: number | null;
  signalsWeek: number | null;
}

export interface GoalView {
  id: string;
  statement: string;
  metric: string | null;
  measured: boolean;
  opportunities: number | null;
}

export interface CoverageView {
  segment: string;
  label: string;
  focus: boolean;
  signals30d: number;
}

export interface ChainFacts {
  signalsWeek: number | null;
  opportunities: number | null;
  bets: number | null;
  building: number | null;
  shipped: number | null;
  measured: number | null;
}

/** The prediction ledger (plan A7); absent until the server measures readouts. */
export interface LedgerView {
  readouts: number;
  inside: number;
  /** Win rate per evidence level at filing, each with its n. */
  byLevel: { level: string; n: number; won: number }[];
  /** Readouts a level needs before its win rate shows. */
  minPerLevel: number;
  discovered: { won: number; total: number } | null;
  requested: { shipped: number; measured: number } | null;
}

export interface DiscoverySummary {
  defVersion: number | null;
  scout: { state: string; sessionId: string | null; title: string | null };
  budget: { usdPerDay: number | null; perAsk: number | null; spentToday: number | null };
  sources: SourceView[];
  chain: ChainFacts;
  cutoffs: { now: number | null; next: number | null };
  goals: GoalView[];
  unassigned: number | null;
  coverage: CoverageView[];
  signals: {
    total: number | null;
    unsorted: number | null;
    flagged: number | null;
    hunches: number | null;
  };
  /** Readouts waiting for a person's keep-or-drop (plan A7). */
  readoutsToDecide: number | null;
  ledger: LedgerView | null;
  /** When Scout's next weekly synthesis runs (epoch seconds), when the server says. */
  nextSynthesisAt: number | null;
}

function parseSource(raw: unknown): SourceView | null {
  const s = obj(raw);
  const id = str(s?.id);
  if (!s || !id) return null;
  return {
    id,
    label: str(s.label) ?? id,
    kind: str(s.kind) ?? "source",
    state: str(s.state) ?? "active",
    privacy: str(s.privacy),
    keepDays: num(s.keep_days),
    callsToday: num(s.calls_today),
    callsPerDay: num(s.calls_per_day),
    health: str(s.health),
    lastOkAt: num(s.last_ok_at),
    lastError: str(s.last_error),
    signalsTotal: num(s.signals_total),
    signalsWeek: num(s.signals_week),
  };
}

/** How many of a share's n were 1: A7 gives `{value, n}` per group, not counts. */
function wonOf(row: Obj | null): number {
  const won = num(row?.won);
  if (won != null) return won;
  const value = num(row?.value);
  const n = num(row?.n);
  return value != null && n != null ? Math.round(value * n) : 0;
}

export function parseLedger(raw: unknown): LedgerView | null {
  const l = obj(raw);
  if (!l) return null;
  // A7: `inside {n, of, share}`, `win_rate_by_level {level: {value, n}}`,
  // `by_origin {discovered: {value, n}}`, `requested {shipped, measured}`.
  const origin = obj(obj(l.by_origin)?.discovered);
  const discovered = obj(l.discovered ?? obj(l.hit_rate)?.discovered);
  const requested = obj(l.requested ?? obj(l.hit_rate)?.requested);
  const byLevelRaw = l.by_level ?? l.win_rate_by_level;
  const byLevel: LedgerView["byLevel"] = Array.isArray(byLevelRaw)
    ? byLevelRaw
        .map((row) => {
          const r = obj(row);
          const name = str(r?.level);
          return name ? { level: name, n: num(r?.n) ?? 0, won: wonOf(r) } : null;
        })
        .filter((r): r is LedgerView["byLevel"][number] => r != null)
    : Object.entries(obj(byLevelRaw) ?? {}).map(([name, value]) => {
        const r = obj(value);
        return { level: name, n: num(r?.n) ?? 0, won: wonOf(r) };
      });
  return {
    readouts: num(l.readouts, l.n) ?? 0,
    inside: num(l.inside, obj(l.inside)?.n, l.inside_range) ?? 0,
    byLevel,
    minPerLevel: num(l.min_per_level) ?? 5,
    discovered: discovered
      ? { won: num(discovered.won) ?? 0, total: num(discovered.total, discovered.n) ?? 0 }
      : origin
        ? { won: wonOf(origin), total: num(origin.n) ?? 0 }
        : null,
    requested: requested
      ? { shipped: num(requested.shipped) ?? 0, measured: num(requested.measured) ?? 0 }
      : null,
  };
}

/** The Discovery header's facts, read field by field: null when there is none. */
export function parseSummary(raw: unknown): DiscoverySummary | null {
  const body = obj(raw);
  if (!body) return null;
  const chain = obj(body.chain) ?? {};
  const scout = obj(body.scout) ?? {};
  const budget = obj(body.budget) ?? {};
  const bands = obj(body.bands) ?? {};
  const cutoffs = obj(bands.cutoffs) ?? {};
  const signals = obj(body.signals) ?? {};
  const readouts = obj(body.readouts);
  return {
    defVersion: num(body.def_version, body.rubric_version),
    scout: {
      state: str(scout.state) ?? "off",
      sessionId: str(scout.session_id),
      title: str(scout.title),
    },
    budget: {
      usdPerDay: num(budget.usd_per_day),
      perAsk: num(budget.per_ask),
      spentToday: num(budget.spent_today),
    },
    sources: list(body.sources)
      .map(parseSource)
      .filter((s): s is SourceView => s != null),
    chain: {
      signalsWeek: num(chain.signals_week, chain.signals),
      opportunities: num(chain.opportunities),
      bets: num(chain.bets),
      building: num(chain.building),
      shipped: num(chain.shipped),
      measured: num(chain.measured),
    },
    cutoffs: { now: num(cutoffs.now), next: num(cutoffs.next) },
    goals: list(body.goals)
      .map((g): GoalView | null => {
        const row = obj(g);
        const id = str(row?.id);
        if (!row || !id) return null;
        return {
          id,
          statement: str(row.statement) ?? id,
          metric: str(row.metric),
          measured: bool(row.measured) || str(row.metric) != null,
          opportunities: num(row.opportunities),
        };
      })
      .filter((g): g is GoalView => g != null),
    unassigned: num(body.unassigned),
    coverage: list(body.coverage)
      .map((c): CoverageView | null => {
        const row = obj(c);
        const segment = str(row?.segment);
        if (!row || !segment) return null;
        return {
          segment,
          label: str(row.label) ?? segment,
          focus: bool(row.focus),
          signals30d: num(row.signals_30d) ?? 0,
        };
      })
      .filter((c): c is CoverageView => c != null),
    signals: {
      total: num(signals.total),
      unsorted: num(signals.unsorted),
      flagged: num(signals.flagged),
      hunches: num(signals.hunches),
    },
    readoutsToDecide: num(readouts?.to_decide, readouts?.waiting, body.readouts_to_decide),
    ledger: parseLedger(body.ledger ?? body.prediction_ledger),
    nextSynthesisAt: num(body.next_synthesis_at, obj(body.cadence)?.next_synthesis_at),
  };
}

// ── Opportunities ──────────────────────────────────────────────

export type Band = "now" | "next" | "later" | "parked";
export const BANDS: readonly Band[] = ["now", "next", "later", "parked"];
export type Quadrant = "bet" | "test_first" | "quick_win" | "wait";

export interface ScorePart {
  name: string;
  value: number;
  input: string;
  reason: string;
}

export interface OpportunityEvidence {
  level: EvidenceLevel | "none";
  confidence: number | null;
  accounts: number;
  sourceKinds: string[];
  bySource: Record<string, number>;
  say: number;
  do: number;
  counter: number;
  hunches: number;
  lastSeen: number | null;
  stale: boolean;
  reason: string | null;
}

export interface OpportunityRow {
  id: string;
  title: string;
  statement: string;
  status: string;
  statusReason: string | null;
  band: Band | null;
  rank: number | null;
  value: number | null;
  quadrant: Quadrant | null;
  parts: ScorePart[];
  scoreLine: string | null;
  evidence: OpportunityEvidence;
  goal: string | null;
  nonGoal: string | null;
  segments: string[];
  votes: { up: number; down: number; mine: number };
  pinned: boolean;
  parked: { until: number | null; reason: string | null; by: string | null } | null;
  killed: { reason: string | null; note: string | null; by: string | null } | null;
  research: {
    question: string;
    budgetUsd: number | null;
    due: number | null;
    state: string | null;
    by: string | null;
  } | null;
  next: { action: string; label: string; project: string | null; proposal: string | null } | null;
  bet: { proposal: string; status: string | null; kind: string | null } | null;
  projects: { slug: string; status: string | null; stage: string | null }[];
  stale: boolean;
}

export interface QuoteView {
  signal: string;
  text: string;
  source: string | null;
  segments: string[];
  observedAt: number | null;
  verified: boolean;
  hunch: boolean;
  author: string | null;
}

export interface WhyStepView {
  kind: "evidence" | "opportunity" | "bet" | "project" | "readout";
  ref: string | null;
  label: string;
  missing: boolean;
}

export interface OpportunityDetail extends OpportunityRow {
  problem: string | null;
  alternatives: string[];
  betNote: { ideas: string[]; riskiest: string | null; cheapest: string | null } | null;
  quotes: { said: QuoteView[]; against: QuoteView[]; hunches: QuoteView[]; total: number };
  why: WhyStepView[];
  lifecycle: { step: string; reached: boolean; current: boolean }[];
  mergedInto: string | null;
  createdBy: string | null;
  pinnedBy: string | null;
  proposals: { proposal: string; status: string | null; title: string | null; bet: boolean }[];
  rubricVersion: number | null;
}

export interface OpportunityList {
  rows: OpportunityRow[];
  total: number;
  cutoffs: { now: number | null; next: number | null };
  defVersion: number | null;
  formula: string | null;
}

function parseEvidence(raw: unknown): OpportunityEvidence {
  const e = obj(raw) ?? {};
  const bySource: Record<string, number> = {};
  for (const [key, value] of Object.entries(obj(e.by_source) ?? {})) {
    if (typeof value === "number" && value > 0) bySource[key] = value;
  }
  return {
    level: level(e.level),
    confidence: num(e.confidence),
    accounts: num(e.accounts) ?? 0,
    sourceKinds: strings(e.source_kinds),
    bySource,
    say: num(e.say) ?? 0,
    do: num(e.do) ?? 0,
    counter: num(e.counter) ?? 0,
    hunches: num(e.hunches) ?? 0,
    lastSeen: num(e.last_seen),
    stale: bool(e.stale),
    reason: str(e.reason),
  };
}

function parseBand(value: unknown): Band | null {
  return typeof value === "string" && (BANDS as readonly string[]).includes(value)
    ? (value as Band)
    : null;
}

function parseQuadrant(value: unknown): Quadrant | null {
  return value === "bet" || value === "test_first" || value === "quick_win" || value === "wait"
    ? value
    : null;
}

/** One opportunity as the table shows it; null without an id or a title. */
export function parseOpportunityRow(raw: unknown): OpportunityRow | null {
  const r = obj(raw);
  const id = str(r?.id, r?.ref);
  const title = str(r?.title);
  if (!r || !id || !title) return null;
  const score = obj(r.score) ?? {};
  const votes = obj(r.votes) ?? {};
  const park = obj(r.parked);
  const kill = obj(r.killed);
  const research = obj(r.research);
  const next = obj(r.next);
  const bet = obj(r.bet);
  return {
    id,
    title,
    statement: str(r.statement) ?? "",
    status: str(r.status) ?? "idea",
    statusReason: str(r.status_reason),
    band: parseBand(r.band),
    rank: num(r.rank, score.rank),
    value: num(score.value),
    quadrant: parseQuadrant(score.quadrant),
    parts: list(score.parts)
      .map((p): ScorePart | null => {
        const part = obj(p);
        const name = str(part?.name);
        const value = num(part?.value);
        if (!part || !name || value == null) return null;
        return { name, value, input: str(part.input) ?? "", reason: str(part.reason) ?? "" };
      })
      .filter((p): p is ScorePart => p != null),
    scoreLine: str(score.line),
    evidence: parseEvidence(r.evidence),
    goal: str(r.goal),
    nonGoal: str(r.non_goal),
    segments: strings(r.segments),
    votes: { up: num(votes.up) ?? 0, down: num(votes.down) ?? 0, mine: num(votes.mine) ?? 0 },
    pinned: bool(r.pinned),
    parked: park
      ? { until: num(park.until), reason: str(park.reason), by: str(park.by_name) }
      : null,
    killed: kill ? { reason: str(kill.reason), note: str(kill.note), by: str(kill.by_name) } : null,
    research:
      research && str(research.question)
        ? {
            question: str(research.question)!,
            budgetUsd: num(research.budget_usd),
            due: num(research.due),
            state: str(research.state),
            by: str(research.by_name),
          }
        : null,
    next:
      next && str(next.action)
        ? {
            action: str(next.action)!,
            label: str(next.label) ?? str(next.action)!,
            project: str(next.project),
            proposal: str(next.proposal),
          }
        : null,
    bet:
      bet && str(bet.proposal)
        ? { proposal: str(bet.proposal)!, status: str(bet.status), kind: str(bet.kind) }
        : null,
    projects: list(r.projects)
      .map((p) => {
        const row = obj(p);
        const slug = str(row?.slug);
        return slug ? { slug, status: str(row?.status), stage: str(row?.stage) } : null;
      })
      .filter((p): p is OpportunityRow["projects"][number] => p != null),
    stale: bool(r.stale),
  };
}

/** `GET …/opportunities`, row by row: null when the answer holds no list. */
export function parseOpportunityList(raw: unknown): OpportunityList | null {
  const body = obj(raw);
  const data = Array.isArray(raw)
    ? raw
    : Array.isArray(body?.data)
      ? (body!.data as unknown[])
      : null;
  if (!data) return null;
  const rows = data.map(parseOpportunityRow).filter((row): row is OpportunityRow => row != null);
  const bands = obj(body?.bands) ?? {};
  return {
    rows,
    total: num(body?.total) ?? rows.length,
    cutoffs: { now: num(bands.now), next: num(bands.next) },
    defVersion: num(body?.def_version),
    formula: str(body?.formula),
  };
}

function parseQuote(raw: unknown): QuoteView | null {
  const q = obj(raw);
  const signal = str(q?.signal);
  const text = str(q?.text);
  if (!q || !signal || !text) return null;
  return {
    signal,
    text,
    source: str(q.source),
    segments: strings(q.segments),
    observedAt: num(q.observed_at),
    verified: bool(q.verified),
    hunch: bool(q.hunch),
    author: str(q.author),
  };
}

const WHY_KINDS = new Set(["evidence", "opportunity", "bet", "project", "readout"]);

/** `GET …/opportunities/{O-n}`: one opportunity as its panel shows it. */
export function parseOpportunityDetail(raw: unknown): OpportunityDetail | null {
  const row = parseOpportunityRow(raw);
  const r = obj(raw);
  if (!row || !r) return null;
  const quotes = obj(r.quotes) ?? {};
  const note = obj(r.bet_note);
  const quoteList = (value: unknown) =>
    list(value)
      .map(parseQuote)
      .filter((q): q is QuoteView => q != null);
  return {
    ...row,
    problem: str(r.problem),
    alternatives: strings(r.alternatives),
    betNote: note
      ? {
          ideas: strings(note.ideas ?? note.solutions),
          riskiest: str(note.riskiest, note.riskiest_assumption),
          cheapest: str(note.cheapest, note.cheapest_test),
        }
      : typeof r.bet_note === "string" && r.bet_note.trim()
        ? { ideas: [], riskiest: null, cheapest: r.bet_note.trim() }
        : null,
    quotes: {
      said: quoteList(quotes.said),
      against: quoteList(quotes.against),
      hunches: quoteList(quotes.hunches),
      total: num(quotes.total) ?? 0,
    },
    why: list(r.why)
      .map((w): WhyStepView | null => {
        const step = obj(w);
        const kind = str(step?.kind);
        if (!step || !kind || !WHY_KINDS.has(kind)) return null;
        return {
          kind: kind as WhyStepView["kind"],
          ref: str(step.ref),
          label: str(step.label) ?? "",
          missing: bool(step.missing),
        };
      })
      .filter((w): w is WhyStepView => w != null),
    lifecycle: list(r.lifecycle)
      .map((l) => {
        const step = obj(l);
        const name = str(step?.step);
        return name
          ? { step: name, reached: bool(step?.reached), current: bool(step?.current) }
          : null;
      })
      .filter((l): l is OpportunityDetail["lifecycle"][number] => l != null),
    mergedInto: str(r.merged_into),
    createdBy: str(r.created_by),
    pinnedBy: str(r.pinned_by),
    proposals: list(r.proposals)
      .map((p) => {
        const row2 = obj(p);
        const ref = str(row2?.proposal);
        return ref
          ? {
              proposal: ref,
              status: str(row2?.status),
              title: str(row2?.title),
              bet: bool(row2?.bet),
            }
          : null;
      })
      .filter((p): p is OpportunityDetail["proposals"][number] => p != null),
    rubricVersion: num(r.rubric_version),
  };
}

// ── Bands, status words, filters ───────────────────────────────

export const BAND_WORD: Record<Band, string> = {
  now: "Now",
  next: "Next",
  later: "Later",
  parked: "Parked",
};

/** `.23` for a rank under one, else the number to two places. */
export function rankText(rank: number | null | undefined): string {
  if (rank == null) return "—";
  return rank < 1 ? rank.toFixed(2).replace(/^0/, "") : rank.toFixed(2);
}

/** What a band's header says: its cut-off (a rubric field) or why it holds what it holds. */
export function bandNote(band: Band, cutoffs: { now: number | null; next: number | null }): string {
  if (band === "now") return cutoffs.now != null ? `score ≥ ${rankText(cutoffs.now)}` : "";
  if (band === "next") return cutoffs.next != null ? `score ≥ ${rankText(cutoffs.next)}` : "";
  if (band === "later") return "below the Next cut-off";
  return "under a non-goal or snoozed; never reaches the portfolio lead";
}

export interface BandSection {
  band: Band | "closed";
  label: string;
  note: string;
  rows: OpportunityRow[];
}

/** Rows by band in table order (Now, Next, Later, Parked, then closed), keeping the server's order inside. */
export function bandSections(
  rows: readonly OpportunityRow[],
  cutoffs: { now: number | null; next: number | null },
): BandSection[] {
  const sections: BandSection[] = BANDS.map((band) => ({
    band,
    label: BAND_WORD[band],
    note: bandNote(band, cutoffs),
    rows: rows.filter((r) => r.band === band),
  }));
  sections.push({
    band: "closed",
    label: "Closed",
    note: "kept, dropped or merged",
    rows: rows.filter((r) => r.band == null),
  });
  return sections.filter((s) => s.rows.length > 0);
}

export interface StatusWords {
  text: string;
  /** Waits for a person (the only amber on the table). */
  waits: boolean;
  /** A second line: research in flight, a parked reason. */
  detail: string | null;
}

/** A pending bet waits for a person; one still co-signing waits for the other lead. */
export function betWaitsForPerson(bet: OpportunityRow["bet"]): boolean {
  return bet != null && bet.status === "pending";
}

function dueText(due: number | null, nowS: number): string | null {
  if (due == null) return null;
  const days = Math.round((due - nowS) / 86400);
  if (days <= 0) return "due today";
  return days === 1 ? "due tomorrow" : `due in ${days} days`;
}

export const KILL_REASONS = [
  "off_strategy",
  "weak_evidence",
  "wrong_segment",
  "too_costly",
  "solved_elsewhere",
  "harmful",
  "duplicate",
] as const;
export type KillReason = (typeof KILL_REASONS)[number];

export const KILL_REASON_WORD: Record<KillReason, string> = {
  off_strategy: "Off strategy",
  weak_evidence: "Evidence too weak",
  wrong_segment: "Wrong users for us",
  too_costly: "Too costly for what it gives",
  solved_elsewhere: "Already solved elsewhere",
  harmful: "Would harm users or the product",
  duplicate: "A duplicate",
};

function killWord(reason: string | null): string {
  return reason && reason in KILL_REASON_WORD
    ? KILL_REASON_WORD[reason as KillReason].toLowerCase()
    : (reason ?? "no reason");
}

/** The Status column: what happens to the opportunity next, in words. */
export function statusWords(row: OpportunityRow, nowS: number): StatusWords {
  const research = row.research
    ? [
        "Scout researching",
        dueText(row.research.due, nowS),
        row.research.budgetUsd != null ? `$${row.research.budgetUsd} ask` : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : null;
  switch (row.status) {
    case "proposed":
      if (row.bet?.status === "awaiting_cosign") {
        return {
          text: `${row.bet.kind === "test" ? "Test" : "Bet"}: the other lead is co-signing`,
          waits: false,
          detail: research,
        };
      }
      return {
        text: `${row.bet?.kind === "test" ? "Test" : "Bet"} waits for you`,
        waits: betWaitsForPerson(row.bet),
        detail: research,
      };
    case "building":
      return { text: "Building", waits: false, detail: research };
    case "shipped":
      return { text: "Shipped · readout after release", waits: false, detail: null };
    case "measured":
      return { text: "Measured · keep or drop?", waits: true, detail: null };
    case "kept":
      return { text: "Kept after its readout", waits: false, detail: null };
    case "validated":
      return { text: "Validated, not planned", waits: false, detail: research };
    case "parked":
      return {
        text: row.nonGoal ? "Parked under a non-goal" : "Parked",
        waits: false,
        detail: row.nonGoal ?? row.parked?.reason ?? null,
      };
    case "dropped":
      return {
        text: `Dropped: ${killWord(row.killed?.reason ?? null)}`,
        waits: false,
        detail: null,
      };
    case "merged":
      return { text: "Merged", waits: false, detail: row.statusReason };
    default:
      return {
        text: row.quadrant === "test_first" ? "Idea · test first" : "Idea",
        waits: false,
        detail: research,
      };
  }
}

export type ChainKey = "signals" | "opportunities" | "bets" | "building" | "shipped" | "measured";

/** A filter on the table: a chain count, a segment, or a source. */
export interface TableFilter {
  chain?: ChainKey | null;
  segment?: string | null;
  source?: string | null;
}

const CLOSED = new Set(["merged", "dropped", "kept"]);

/** The rows a filter keeps (closed ones only under "measured"). */
export function filterRows(rows: readonly OpportunityRow[], filter: TableFilter): OpportunityRow[] {
  return rows.filter((row) => {
    switch (filter.chain) {
      case "opportunities":
        if (CLOSED.has(row.status)) return false;
        break;
      case "bets":
        if (row.status !== "proposed") return false;
        break;
      case "building":
        if (row.status !== "building") return false;
        break;
      case "shipped":
        if (row.status !== "shipped") return false;
        break;
      case "measured":
        if (row.status !== "measured" && row.status !== "kept") return false;
        break;
      default:
        break;
    }
    if (filter.segment && !row.segments.includes(filter.segment)) return false;
    if (filter.source && !(filter.source in row.evidence.bySource)) return false;
    return true;
  });
}

/** One count of the chain strip. */
export interface ChainCell {
  key: ChainKey;
  count: number;
  label: string;
  waiting: boolean;
}

/**
 * Signals this week → opportunities → bets → building → shipped → measured.
 * A count the server did not give reads 0 (a grey zero, never a guess);
 * bets turn amber when one waits for a person.
 */
export function chainCells(
  chain: ChainFacts,
  options: { betsWaiting?: boolean; readoutsWaiting?: boolean } = {},
): ChainCell[] {
  const cell = (key: ChainKey, count: number | null, one: string, many: string): ChainCell => ({
    key,
    count: count ?? 0,
    label: (count ?? 0) === 1 ? one : many,
    waiting: false,
  });
  return [
    cell("signals", chain.signalsWeek, "signal this week", "signals this week"),
    cell("opportunities", chain.opportunities, "opportunity", "opportunities"),
    { ...cell("bets", chain.bets, "bet", "bets"), waiting: Boolean(options.betsWaiting) },
    cell("building", chain.building, "building", "building"),
    cell("shipped", chain.shipped, "shipped", "shipped"),
    {
      ...cell("measured", chain.measured, "measured", "measured"),
      waiting: Boolean(options.readoutsWaiting),
    },
  ];
}

/** A source's state in words: `on`, `trial`, `paused`, `waits for an owner`, `on hold after failures`. */
export function sourceStateWord(source: Pick<SourceView, "state" | "health">): string {
  if (source.state === "pending") return "waits for an owner";
  if (source.health === "failing") return "on hold after failures";
  if (source.state === "paused") return "paused";
  if (source.state === "trial") return "trial";
  if (source.health === "erroring") return "on, with errors";
  return "on";
}

/**
 * The segments the factory hears least from (the two quietest in 30 days),
 * so a blind spot shows next to the goal.
 */
export function quietSegments(coverage: readonly CoverageView[], max = 2): CoverageView[] {
  return [...coverage]
    .sort((a, b) => a.signals30d - b.signals30d || a.label.localeCompare(b.label))
    .slice(0, max);
}

// ── Triage keys ────────────────────────────────────────────────

export type TriageAction = "down" | "up" | "open" | "vote" | "plan" | "park" | "kill";

/** `j`/`k` move, Enter opens, `v` vote, `n` plan, `p` park, `x` kill. */
export const TRIAGE_KEYS: Readonly<Record<string, TriageAction>> = {
  j: "down",
  k: "up",
  Enter: "open",
  v: "vote",
  n: "plan",
  p: "park",
  x: "kill",
};

/**
 * The triage action of a key press, or null: with a modifier, or when the key
 * belongs to what has focus (a field, a menu, a list, a dialog, the Room).
 */
export function triageAction(event: {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  target?: EventTarget | null;
}): TriageAction | null {
  if (event.metaKey || event.ctrlKey || event.altKey) return null;
  if (keyOwnedByTarget(event.target ?? null)) return null;
  return TRIAGE_KEYS[event.key] ?? null;
}

/** The levers an opportunity's status allows (an injection-suspect signal has none). */
export function leversFor(row: OpportunityRow): {
  vote: boolean;
  plan: boolean;
  park: boolean;
  kill: boolean;
  research: boolean;
} {
  const closed = CLOSED.has(row.status);
  return {
    vote: !closed,
    plan:
      !closed && row.status !== "parked" && row.status !== "proposed" && row.status !== "building",
    park: !closed && row.status !== "parked",
    kill: row.status !== "dropped" && row.status !== "merged",
    research: !closed,
  };
}

// ── Signals ────────────────────────────────────────────────────

export interface SignalView {
  id: string;
  source: string;
  sourceKind: string | null;
  kind: string | null;
  sayDo: string | null;
  /** The redacted excerpt: untrusted user text, never instructions. */
  text: string | null;
  gone: string | null;
  observedAt: number | null;
  segments: string[];
  /** Scout's label (agent text). */
  paraphrase: string | null;
  intensity: string | null;
  injection: boolean;
  excluded: string | null;
  /** The server's word: false for an injection suspect, an expired or withdrawn one. */
  actionable: boolean;
  weight: number | null;
  hunch: boolean;
  /** How the server names the author: a person's id, or an agent's title. */
  author: string | null;
  /** `human` or `agent` when the server says so. */
  authorKind: string | null;
  links: { opportunity: string; title: string | null; counter: boolean }[];
}

export function parseSignal(raw: unknown): SignalView | null {
  const s = obj(raw);
  const id = str(s?.id);
  if (!s || !id) return null;
  const injection = bool(s.injection_suspect) || strings(s.flags).includes("injection_suspect");
  const excluded = str(s.excluded);
  return {
    id,
    source: str(s.source) ?? "source",
    sourceKind: str(s.source_kind),
    kind: str(s.kind),
    sayDo: str(s.say_do),
    text: str(s.text),
    gone: str(s.gone),
    observedAt: num(s.observed_at, s.ingested_at),
    segments: strings(s.segments),
    paraphrase: str(obj(s.paraphrase)?.text),
    intensity: str(s.intensity),
    injection,
    excluded,
    // Never actionable when the server or the flags say it may be an injection.
    actionable: s.actionable !== false && !injection && excluded == null && !str(s.gone),
    weight: num(s.weight),
    hunch: bool(s.hunch),
    author: str(s.author),
    authorKind: str(s.author_kind),
    links: list(s.links)
      .map((l) => {
        const link = obj(l);
        const opportunity = str(link?.opportunity);
        return opportunity
          ? { opportunity, title: str(link?.title), counter: bool(link?.counter) }
          : null;
      })
      .filter((l): l is SignalView["links"][number] => l != null),
  };
}

export function parseSignals(raw: unknown): SignalView[] | null {
  const body = obj(raw);
  const data = Array.isArray(raw)
    ? raw
    : Array.isArray(body?.data)
      ? (body!.data as unknown[])
      : null;
  if (!data) return null;
  return data.map(parseSignal).filter((s): s is SignalView => s != null);
}

/**
 * Whether the viewer may withdraw this conviction. The server takes back only
 * a person's hunch (never an agent's idea or a repo post), by its author or an
 * owner; `author` is the person's id as the server names it.
 */
export function mayWithdrawHunch(
  hunch: SignalView,
  viewer: { userId: string | null; owner: boolean },
): boolean {
  if (hunch.kind !== "hunch" || hunch.gone || hunch.author == null) return false;
  if (hunch.authorKind != null && hunch.authorKind !== "human") return false;
  return viewer.owner || (viewer.userId != null && hunch.author === viewer.userId);
}

/** What came of a hunch, in words, from the opportunities it is attached to. */
export function hunchOutcome(hunch: SignalView, rows: readonly OpportunityRow[]): string {
  if (hunch.gone) return hunch.gone === "withdrawn" ? "Withdrawn" : "Expired";
  const linked = hunch.links
    .map((l) => rows.find((r) => r.id === l.opportunity))
    .filter((r): r is OpportunityRow => r != null);
  if (linked.length === 0) {
    return hunch.links.length
      ? `On ${hunch.links.map((l) => l.opportunity).join(", ")}`
      : "No opportunity yet. Scout looks for evidence and proposes the cheapest test.";
  }
  const best = linked[0]!;
  const accounts = best.evidence.accounts;
  const found = accounts
    ? `Evidence found: ${plural(accounts, "account")}.`
    : "No user evidence yet.";
  return `${found} ${statusWords(best, 0).text}.`;
}

// ── Readouts (plan A7) ─────────────────────────────────────────

/** A decision the server offers on a written readout (A7), the recommended one first. */
export interface ReadoutOption {
  decision: ReadoutDecision;
  label: string;
  recommended: boolean;
  /** Going against the recommendation: an owner, a named reviewer and a reason. */
  against: boolean;
}

export type ReadoutDecision = "keep" | "drop" | "iterate" | "extend";

/** A person's "did it help?" on a readout measured by a person's verdict. */
export type ReadoutAnswer = "helped" | "no_change" | "hurt";

export interface ReadoutView {
  id: string;
  /** Its key, e.g. `C-31-d28` (links may name a readout by it). */
  key: string | null;
  /** `scheduled`, `awaiting_answer`, `written`, `decided`, `cancelled` (A7). */
  status: string | null;
  proposal: string | null;
  opportunity: string | null;
  title: string;
  day: number | null;
  method: string | null;
  metric: string | null;
  verdict: ReadoutVerdict | null;
  recommendation: string | null;
  low: number | null;
  high: number | null;
  unit: string;
  actual: number | null;
  inside: boolean | null;
  n: number | null;
  /** `measured` is false while no source reads it (then `held` says nothing). */
  guardrails: { name: string; held: boolean; measured: boolean }[];
  decision: { decision: string; by: string | null; reason: string | null; against: boolean } | null;
  nextDay: number | null;
  /** The server's why-line for its verdict. */
  why: string | null;
  /** Why a due readout still waits for its data (its window not read or sorted yet). */
  waiting: string | null;
  options: ReadoutOption[];
  answer: { answer: string; reason: string | null; by: string | null } | null;
}

const VERDICTS = new Set(["won", "flat", "lost", "inconclusive"]);
const DECISIONS = new Set(["keep", "drop", "iterate", "extend"]);

export function parseReadout(raw: unknown): ReadoutView | null {
  const r = obj(raw);
  const id = str(r?.id, r?.ref);
  if (!r || !id) return null;
  const expected = obj(r.expected ?? r.predicted) ?? {};
  const decision = obj(r.decision);
  const verdict = str(r.verdict);
  const low = num(expected.low, r.low);
  const high = num(expected.high, r.high);
  const actual = num(r.actual, r.effect, obj(r.effect)?.value);
  const inside =
    typeof r.predicted_vs_actual === "boolean"
      ? r.predicted_vs_actual
      : typeof r.inside === "boolean"
        ? r.inside
        : str(r.predicted_vs_actual) === "inside"
          ? true
          : str(r.predicted_vs_actual) === "outside"
            ? false
            : actual != null && low != null && high != null
              ? actual >= Math.min(low, high) && actual <= Math.max(low, high)
              : null;
  const answer = obj(r.answer);
  return {
    id,
    key: str(r.key),
    status: str(r.status),
    proposal: str(r.proposal, r.bet),
    opportunity: str(r.opportunity),
    title: str(r.title, r.outcome) ?? id,
    day: num(r.day),
    method: str(r.method),
    metric: str(r.metric),
    verdict: verdict && VERDICTS.has(verdict) ? (verdict as ReadoutVerdict) : null,
    recommendation: str(r.recommendation),
    low,
    high,
    unit: str(expected.unit, r.unit) ?? "",
    actual,
    inside,
    n: num(r.n),
    guardrails: list(r.guardrails)
      .map((g) => {
        const row = obj(g);
        const name = str(row?.name, row?.guardrail);
        const state = str(row?.state);
        if (!name) return null;
        return state
          ? { name, held: state === "held", measured: state !== "unmeasured" }
          : { name, held: row?.held !== false, measured: true };
      })
      .filter((g): g is ReadoutView["guardrails"][number] => g != null),
    decision: decision
      ? {
          decision: str(decision.decision) ?? "",
          by: str(decision.by, decision.by_name),
          reason: str(decision.reason),
          against: bool(decision.against_recommendation ?? decision.against),
        }
      : str(r.decision)
        ? { decision: str(r.decision)!, by: null, reason: null, against: false }
        : null,
    nextDay: num(r.next_day),
    why: str(r.why),
    waiting: str(obj(r.waiting)?.why, r.waiting),
    options: list(r.options)
      .map((o): ReadoutOption | null => {
        const row = obj(o);
        const choice = str(row?.decision);
        if (!row || !choice || !DECISIONS.has(choice)) return null;
        return {
          decision: choice as ReadoutDecision,
          label: str(row.label) ?? choice,
          recommended: bool(row.recommended),
          against: bool(row.against_recommendation),
        };
      })
      .filter((o): o is ReadoutOption => o != null),
    answer: answer
      ? {
          answer: str(answer.answer) ?? "",
          reason: str(answer.reason),
          by: str(answer.by),
        }
      : null,
  };
}

/** A readout named in the address: by its id or its key (`C-31-d28`). */
export function findReadout(
  readouts: readonly ReadoutView[],
  ref: string | null,
): ReadoutView | null {
  if (!ref) return null;
  return readouts.find((r) => r.id === ref || r.key === ref) ?? null;
}

export function parseReadouts(raw: unknown): ReadoutView[] | null {
  const body = obj(raw);
  const data = Array.isArray(raw)
    ? raw
    : Array.isArray(body?.data)
      ? (body!.data as unknown[])
      : null;
  if (!data) return null;
  return data.map(parseReadout).filter((r): r is ReadoutView => r != null);
}

/** The latest readout of each bet (day 28 over day 7). */
export function latestReadouts(readouts: readonly ReadoutView[]): ReadoutView[] {
  const byBet = new Map<string, ReadoutView>();
  for (const r of readouts) {
    const key = r.proposal ?? r.id;
    const seen = byBet.get(key);
    if (!seen || (r.day ?? 0) > (seen.day ?? 0)) byBet.set(key, r);
  }
  return [...byBet.values()];
}

// ── Impact ─────────────────────────────────────────────────────

export interface PendingBetCard {
  proposal: string;
  title: string;
  opportunity: string | null;
  project: string | null;
  /** `waits for you`, `co-signing`, `building`, `shipped`. */
  state: string;
  waits: boolean;
  low: number;
  high: number;
  unit: string;
  metric: string;
}

/**
 * Bets with a predicted range and no readout yet: those waiting for a person
 * or the other lead (from the proposals) and those building (frozen into
 * their project's charter).
 */
export function pendingBets(
  proposals: readonly ProposalView[],
  projects: readonly ProjectView[],
  readouts: readonly ReadoutView[],
): PendingBetCard[] {
  const measured = new Set(readouts.map((r) => r.proposal).filter(Boolean));
  const out: PendingBetCard[] = [];
  const seen = new Set<string>();
  for (const p of proposals) {
    if (!p.bet || measured.has(p.ref)) continue;
    if (p.status !== "pending" && p.status !== "awaiting_cosign") continue;
    seen.add(p.ref);
    out.push({
      proposal: p.ref,
      title: p.bet.outcome || p.title,
      opportunity: p.opportunity ?? null,
      project: null,
      state: p.status === "awaiting_cosign" ? "co-signing" : "waits for you",
      waits: p.status === "pending",
      low: p.bet.expected.low,
      high: p.bet.expected.high,
      unit: p.bet.expected.unit,
      metric: p.bet.metric,
    });
  }
  for (const project of projects) {
    const why = project.why;
    if (!why?.proposal || !why.expected || measured.has(why.proposal) || seen.has(why.proposal)) {
      continue;
    }
    seen.add(why.proposal);
    const shipped = project.stage === "merged" || project.stage === "done";
    out.push({
      proposal: why.proposal,
      title: why.outcome ?? project.charter?.outcome ?? project.title ?? project.slug,
      opportunity: why.opportunity ?? null,
      project: project.slug,
      state: shipped ? "shipped" : "building",
      waits: false,
      low: why.expected.low,
      high: why.expected.high,
      unit: why.expected.unit,
      metric: why.metric ?? "",
    });
  }
  return out;
}

export interface LedgerFacts {
  readouts: number;
  inside: number;
  levelLine: string;
  discovered: { won: number; total: number };
  requested: { count: number; measured: number };
}

/**
 * The prediction ledger and hit rate by origin: the server's numbers once it
 * keeps a ledger, else counted from the readouts it returned and the projects
 * (requested work is never counted as a success).
 */
export function ledgerFacts(
  ledger: LedgerView | null,
  readouts: readonly ReadoutView[],
  projects: readonly ProjectView[],
): LedgerFacts {
  const latest = latestReadouts(readouts);
  const live = projects.filter((p) => p.kind !== "integration" && p.status !== "cancelled");
  const requested = live.filter((p) => !p.why?.opportunity);
  // Measured means a readout exists, not merely a metric.
  const readOut = new Set(latest.map((r) => r.proposal).filter(Boolean));
  const requestedMeasured = requested.filter(
    (p) => p.why?.proposal != null && readOut.has(p.why.proposal),
  ).length;
  const enough = (ledger?.byLevel ?? []).filter((l) => l.n >= (ledger?.minPerLevel ?? 5));
  const min = ledger?.minPerLevel ?? 5;
  const levelLine = enough.length
    ? enough.map((l) => `${l.level} ${l.won} of ${l.n} won`).join(" · ")
    : `not enough data (needs ${min} readouts per level)`;
  return {
    readouts: ledger?.readouts ?? latest.length,
    inside: ledger?.inside ?? latest.filter((r) => r.inside === true).length,
    levelLine,
    discovered: ledger?.discovered ?? {
      won: latest.filter((r) => r.verdict === "won").length,
      total: latest.length,
    },
    requested: {
      count: ledger?.requested?.shipped ?? requested.length,
      measured: ledger?.requested?.measured ?? requestedMeasured,
    },
  };
}

// ── The value × confidence map ─────────────────────────────────

export interface MapPoint {
  id: string;
  title: string;
  value: number;
  confidence: number;
  quadrant: Quadrant | null;
  tone: "building" | "waits" | "data";
}

export interface MapDividers {
  value: number;
  confidence: number;
}

/** The weight a hunch counts with when the rubric sets none. */
export const HUNCH_WEIGHT_FALLBACK = 0.1;

/** The hunch weight of the rubric in force (`discovery.rubric.ladder.hunch`). */
export function hunchWeight(definition: DefinitionDoc | null | undefined): number {
  const ladder = obj(obj(obj(definition?.discovery)?.rubric)?.ladder);
  return num(ladder?.hunch) ?? HUNCH_WEIGHT_FALLBACK;
}

/** A weight as people read it: `0.1`, `0.25`. */
export function weightText(weight: number): string {
  return String(Math.round(weight * 100) / 100);
}

/** The map's dividers: rubric fields of the definition in force (null when it has none). */
export function mapDividers(definition: DefinitionDoc | null | undefined): MapDividers | null {
  const map = obj(obj(obj(definition?.discovery)?.rubric)?.map);
  const value = num(map?.value_high);
  const confidence = num(map?.confidence_high);
  return value != null && confidence != null ? { value, confidence } : null;
}

/** Open opportunities with a value and a confidence, as dots. */
export function mapPoints(rows: readonly OpportunityRow[]): MapPoint[] {
  return rows
    .filter((r) => r.band != null && r.band !== "parked" && r.value != null)
    .map((r) => ({
      id: r.id,
      title: r.title,
      value: Math.max(0, Math.min(1, r.value ?? 0)),
      confidence: Math.max(0, Math.min(1, r.evidence.confidence ?? 0)),
      quadrant: r.quadrant,
      tone:
        r.status === "building"
          ? "building"
          : statusWords(r, 0).waits
            ? "waits"
            : ("data" as MapPoint["tone"]),
    }));
}

export const QUADRANT_WORD: Record<Quadrant, string> = {
  bet: "Bet",
  test_first: "Test first",
  wait: "Wait for signals",
  quick_win: "Quick win if tiny",
};

// ── Flow: the loop as fixed lanes ──────────────────────────────

export type FlowLane = 0 | 1 | 2 | 3 | 4;
export const FLOW_LANES: readonly { lane: FlowLane; label: string }[] = [
  { lane: 0, label: "Sense · sources" },
  { lane: 1, label: "Make sense · opportunities" },
  { lane: 2, label: "Decide · bets" },
  { lane: 3, label: "Build · projects" },
  { lane: 4, label: "Measure · readouts" },
];

export interface FlowNode {
  id: string;
  lane: FlowLane;
  title: string;
  detail: string;
  /** Waits for a person (amber), a placeholder (dashed), or plain. */
  look: "amber" | "dashed" | "plain";
  /** What Open shows (`?peek=` value or a project path), when anything. */
  peek: string | null;
  project: string | null;
}

export interface FlowEdge {
  from: string;
  to: string;
  /** Signals behind the link (drawn thicker). */
  weight: number;
  /** solid: evidence; dotted: requested, no evidence (and hunches); dashed: not measured yet. */
  style: "solid" | "dotted" | "dashed";
  /** A readout flowing back into its opportunity (left out of the chain walk). */
  back?: boolean;
}

export interface FlowModel {
  nodes: FlowNode[];
  edges: FlowEdge[];
}

export interface FlowInput {
  sources: readonly SourceView[];
  rows: readonly OpportunityRow[];
  proposals: readonly ProposalView[];
  snapshot: Pick<FactorySnapshot, "projects">;
  readouts: readonly ReadoutView[];
  /** Pending sources (Needs you), drawn amber and dashed. */
  pendingSources?: readonly { id: string; label: string }[];
}

const REQUESTED = "requested";
const NOT_MEASURED = "not-measured";

/** Projects the Flow draws: every work project, shipped ones too, never a cancelled one. */
function isLiveProject(p: ProjectView): boolean {
  return p.kind !== "integration" && p.status !== "cancelled";
}

/**
 * Sources → Now and Next opportunities (Later and parked folded) → bets and
 * a "Requested work" box → projects → readouts, with the links between them:
 * a source feeds an opportunity by its signals, a team hunch dotted, a bet
 * builds a project, requested work ships with no metric (dashed to "nothing
 * is learned"), a readout flows back to its opportunity.
 */
export function flowModel(input: FlowInput): FlowModel {
  const nodes: FlowNode[] = [];
  const edges: FlowEdge[] = [];
  const has = (id: string) => nodes.some((n) => n.id === id);
  const sourceOf = new Map<string, string>();
  for (const s of input.sources) {
    const id = `source:${s.id}`;
    sourceOf.set(s.id, id);
    nodes.push({
      id,
      lane: 0,
      title: s.label,
      detail: `${s.kind} · ${sourceStateWord(s)}`,
      // One that waits for an owner's approval is drawn as a pending one (amber).
      look: s.state === "pending" ? "amber" : s.state === "paused" ? "dashed" : "plain",
      peek: id,
      project: null,
    });
  }
  for (const s of input.pendingSources ?? []) {
    const id = `source:${s.id}`;
    if (has(id)) continue;
    nodes.push({
      id,
      lane: 0,
      title: s.label,
      detail: "waits for you",
      look: "amber",
      peek: id,
      project: null,
    });
  }
  const open = input.rows.filter((r) => r.band === "now" || r.band === "next");
  for (const r of open) {
    nodes.push({
      id: r.id,
      lane: 1,
      title: r.title,
      detail: [
        BAND_WORD[r.band as Band],
        rankText(r.rank),
        r.evidence.accounts ? plural(r.evidence.accounts, "account") : null,
      ]
        .filter(Boolean)
        .join(" · "),
      look: statusWords(r, 0).waits ? "amber" : "plain",
      peek: r.id,
      project: null,
    });
    for (const [source, count] of Object.entries(r.evidence.bySource)) {
      const from = sourceOf.get(source);
      if (from) edges.push({ from, to: r.id, weight: count, style: "solid" });
    }
    if (r.evidence.hunches > 0) {
      const team = input.sources.find((s) => s.kind === "team");
      if (team) {
        edges.push({ from: `source:${team.id}`, to: r.id, weight: 1, style: "dotted" });
      }
    }
  }
  const folded = input.rows.filter((r) => r.band === "later" || r.band === "parked").length;
  if (folded) {
    nodes.push({
      id: "later",
      lane: 1,
      title: `Later and parked: ${folded}`,
      detail: "",
      look: "dashed",
      peek: null,
      project: null,
    });
  }
  const bets = input.proposals.filter(
    (p) =>
      p.bet &&
      p.opportunity &&
      ["pending", "awaiting_cosign", "applied", "kept"].includes(p.status),
  );
  for (const p of bets) {
    const waits = p.status === "pending";
    nodes.push({
      id: p.ref,
      lane: 2,
      title: p.bet!.outcome || p.title,
      detail: `${p.bet!.kind === "test" ? "Test" : "Outcome bet"} · ${
        waits ? "waits for you" : p.status === "awaiting_cosign" ? "co-signing" : "applied"
      }`,
      look: waits ? "amber" : "plain",
      peek: p.ref,
      project: null,
    });
    if (has(p.opportunity!))
      edges.push({ from: p.opportunity!, to: p.ref, weight: 2, style: "solid" });
  }
  const projects = input.snapshot.projects.filter(isLiveProject);
  const requested = projects.filter((p) => !p.why?.opportunity);
  if (requested.length) {
    nodes.push({
      id: REQUESTED,
      lane: 2,
      title: "Requested work",
      detail: "no evidence, no metric",
      look: "dashed",
      peek: null,
      project: null,
    });
  }
  const readouts = latestReadouts(input.readouts);
  for (const p of projects) {
    const id = `project:${p.slug}`;
    nodes.push({
      id,
      lane: 3,
      title: p.charter?.outcome?.trim() || p.title || p.slug,
      detail: p.overlay === "failed" ? "stopped" : STAGE_STEP_LABEL[stageStepOf(p.stage)],
      look: p.overlay === "failed" ? "dashed" : "plain",
      peek: null,
      project: p.slug,
    });
    const bet = p.why?.proposal;
    if (bet && has(bet)) edges.push({ from: bet, to: id, weight: 2, style: "solid" });
    else if (p.why?.opportunity && has(p.why.opportunity)) {
      edges.push({ from: p.why.opportunity, to: id, weight: 2, style: "solid" });
    }
    if (!p.why?.opportunity) {
      edges.push({ from: REQUESTED, to: id, weight: 1, style: "dotted" });
      edges.push({ from: id, to: NOT_MEASURED, weight: 1, style: "dashed" });
    } else if (bet && !readouts.some((r) => r.proposal === bet)) {
      const pend = `pending:${bet}`;
      if (!has(pend)) {
        nodes.push({
          id: pend,
          lane: 4,
          title: `${bet}: after release`,
          detail: "readouts on day 7 and 28",
          look: "dashed",
          peek: null,
          project: null,
        });
      }
      edges.push({ from: id, to: pend, weight: 2, style: "dashed" });
    }
  }
  for (const r of readouts) {
    const id = `readout:${r.id}`;
    nodes.push({
      id,
      lane: 4,
      title: `${r.proposal ?? r.id}${r.day != null ? ` · day ${r.day}` : ""}${r.verdict ? ` · ${r.verdict}` : ""}`,
      detail:
        r.low != null && r.high != null
          ? `predicted ${r.low} to ${r.high} ${r.unit}${r.actual != null ? `, actual ${r.actual} ${r.unit}` : ""}`
          : (r.metric ?? ""),
      look: r.decision == null && r.day != null && r.day >= 28 ? "amber" : "plain",
      peek: id,
      project: null,
    });
    const project = projects.find((p) => p.why?.proposal === r.proposal);
    if (project) edges.push({ from: `project:${project.slug}`, to: id, weight: 2, style: "solid" });
    if (r.opportunity && has(r.opportunity)) {
      edges.push({ from: id, to: r.opportunity, weight: 1.5, style: "dashed", back: true });
    }
  }
  if (requested.length) {
    nodes.push({
      id: NOT_MEASURED,
      lane: 4,
      title: "Requested work",
      detail: "ships with no metric: nothing is learned",
      look: "dashed",
      peek: null,
      project: null,
    });
  }
  return { nodes, edges: edges.filter((e) => has(e.from) && has(e.to)) };
}

/** Everything upstream and downstream of `id` (back curves are not part of a chain). */
export function chainOf(id: string, model: FlowModel): Set<string> {
  const up = new Set([id]);
  const down = new Set([id]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const e of model.edges) {
      if (e.back) continue;
      if (down.has(e.from) && !down.has(e.to)) {
        down.add(e.to);
        changed = true;
      }
      if (up.has(e.to) && !up.has(e.from)) {
        up.add(e.from);
        changed = true;
      }
    }
  }
  return new Set([...up, ...down]);
}

// ── Side panels in the address (`?peek=`) ──────────────────────

export type PeekTarget =
  | { kind: "opportunity"; id: string }
  | { kind: "bet"; id: string }
  | { kind: "source"; id: string }
  | { kind: "readout"; id: string };

/** `O-15`, `C-31`, `source:<id>`, `readout:<id>`; null for anything else. */
export function peekTarget(value: string | null | undefined): PeekTarget | null {
  const v = (value ?? "").trim();
  if (/^O-\d+$/i.test(v)) return { kind: "opportunity", id: v.toUpperCase() };
  if (/^C-\d+$/i.test(v)) return { kind: "bet", id: v.toUpperCase() };
  if (v.startsWith("source:") && v.length > 7) return { kind: "source", id: v.slice(7) };
  if (v.startsWith("readout:") && v.length > 8) return { kind: "readout", id: v.slice(8) };
  return null;
}

// ── Pending sources (a definition proposal adding one) ─────────

export interface PendingSource {
  id: string;
  label: string;
  proposal: string;
  source: Partial<SourceDef>;
  by: string | null;
}

/** Sources an open proposal would add: they wait for an owner. */
export function pendingSources(proposals: readonly ProposalView[]): PendingSource[] {
  const out: PendingSource[] = [];
  for (const p of proposals) {
    if (p.status !== "pending") continue;
    for (const op of p.ops) {
      const path = str(op.path)?.replace(/\//g, ".");
      const value = obj(op.value);
      if (path !== "discovery.sources" || str(op.op) !== "add" || !value) continue;
      const id = str(value.id);
      if (!id) continue;
      out.push({
        id,
        label: str(value.label) ?? id,
        proposal: p.ref,
        source: value as Partial<SourceDef>,
        by: p.author_title ?? null,
      });
    }
  }
  return out;
}

// ── Turning discovery on: the discovery starter ────────────────
// The starter itself (its sources and changes) is `discoveryStarter.ts`,
// shared with Steer › Overview; here are its plain words.

export {
  STARTER_SOURCES,
  type StarterSource,
  starterChanges,
  starterWaiting,
} from "./discoveryStarter";

/** What each starter source lets Scout read, in plain words (hunches with the rubric's weight). */
export function starterReads(kind: string, weight: number = HUNCH_WEIGHT_FALLBACK): string | null {
  switch (kind) {
    case "repo":
      return "issues, discussions and releases of the factory's own repositories from the last 90 days (the team's own posts count as hunches)";
    case "web":
      return "up to 30 web searches a day and the public pages they find, plus a weekly read of competitors' changelogs once the product profile lists them";
    case "team":
      return `hunches from the factory's people ("I think … because …"), counted with weight ${weightText(weight)}, never as user evidence`;
    default:
      return null;
  }
}

/** The starter in plain words: what Scout reads, what it costs, how often it runs. */
export function starterSummary(definition: DefinitionDoc | null | undefined): {
  /** Each source Scout would read; `added` when the starter adds it (else the factory has it). */
  reads: { id: string; label: string; text: string; added: boolean }[];
  usdPerDay: number | null;
  perAsk: number | null;
  sweepH: number | null;
  synthesisH: number | null;
} {
  const discovery = definition?.discovery;
  const have = discovery?.sources ?? [];
  const weight = hunchWeight(definition);
  const reads = [
    ...have.map((s) => ({
      id: s.id,
      label: s.label,
      text: starterReads(s.kind, weight) ?? `a ${s.kind} source`,
      added: false,
    })),
    ...missingStarterSources(definition).map((s) => ({
      id: s.id,
      label: s.label,
      text: starterReads(s.kind, weight) ?? "",
      added: true,
    })),
  ];
  // As applied: the factory's own budget and cadence, else the starter's.
  return {
    reads,
    usdPerDay: num(discovery?.budgets?.usd_per_day) ?? STARTER_USD_PER_DAY,
    perAsk: num(discovery?.budgets?.per_ask),
    sweepH: num(discovery?.cadence?.sweep_h) ?? STARTER_SWEEP_H,
    synthesisH: num(discovery?.cadence?.synthesis_h) ?? STARTER_SYNTHESIS_H,
  };
}

/** `daily`, `weekly`, `every 12 hours`, `off`. */
export function cadenceWord(hours: number | null): string | null {
  if (hours == null) return null;
  if (hours === 0) return "off";
  if (hours === 24) return "daily";
  if (hours === 168) return "weekly";
  if (hours % 24 === 0) return `every ${hours / 24} days`;
  return `every ${hours} hours`;
}
