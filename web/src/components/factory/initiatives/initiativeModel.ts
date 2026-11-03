// What the initiative card and page show, from the server's line and view.
// Pure: the components only lay these out. Every word that is not fixed copy
// comes from the factory's own records.

import type { FactoryStage } from "@/lib/factoryApi";
import type {
  InitiativeLine,
  InitiativeProject,
  InitiativeRun,
  InitiativeState,
  InitiativeStep,
  InitiativeView,
} from "@/lib/factoryInitiativesApi";
import { formatElapsed, formatUsd, plural, shortSha } from "../factoryFormat";
import type { StatusMeaning } from "../ui/statusTokens";

export interface StateMeta {
  label: string;
  meaning: StatusMeaning;
}

/** Each state's chip: green while it moves, red when a step fails, grey when it rests. */
export const STATE_META: Record<InitiativeState, StateMeta> = {
  planned: { label: "Planned", meaning: "idle" },
  building: { label: "Building", meaning: "working" },
  on_base: { label: "On the base env", meaning: "working" },
  accepting: { label: "Acceptance running", meaning: "working" },
  failing: { label: "Acceptance failing", meaning: "broken" },
  done: { label: "Done", meaning: "working" },
  closed: { label: "Closed", meaning: "idle" },
  cancelled: { label: "Cancelled", meaning: "idle" },
};

export function stateMeta(state: string): StateMeta {
  return STATE_META[state as InitiativeState] ?? { label: state, meaning: "idle" };
}

/** `2/4 merged`. */
export function mergedText(merged: number, total: number): string {
  return `${merged}/${total} merged`;
}

/** The line without its leading title (the card prints the title as a link). */
export function lineRest(line: Pick<InitiativeLine, "title" | "line">): string {
  const prefix = `${line.title}: `;
  return line.line.startsWith(prefix) ? line.line.slice(prefix.length) : line.line;
}

export type AcceptanceMark = "pass" | "fail" | "skipped" | "pending";

export interface CardModel {
  ref: string;
  title: string;
  href: string;
  state: StateMeta;
  /** Merged projects out of all, for the bar. */
  merged: number;
  total: number;
  /** `Skeleton on the base env` / `Skeleton not on the base env yet`, or null without one. */
  skeleton: { text: string; ok: boolean } | null;
  /** Where people try it once the skeleton is on the base env, or null. */
  tryIt: TryIt | null;
  /** `Acceptance 3/5 pass`, `Acceptance failing`, `No acceptance run yet`. */
  acceptance: { text: string; mark: AcceptanceMark };
  /** `Waiting on core (a merge of !12)` (at most two, then `+N`). */
  waiting: string | null;
  next: string | null;
  cost: string | null;
}

/** The base env's app link and what to turn on there to try the initiative. */
export interface TryIt {
  url: string;
  /** `Turn on settings.versions there`, or null without a flag. */
  hint: string | null;
}

function tryIt(url: string | null | undefined, flag: string | null | undefined): TryIt | null {
  if (!url) return null;
  return { url, hint: flag ? `Turn on ${flag} there` : null };
}

/** Where to try an initiative's walking skeleton, once it is on the base env. */
export function tryOnBase(view: InitiativeView): TryIt | null {
  return view.skeleton_on_base ? tryIt(view.base?.app_url, view.flag?.key) : null;
}

function acceptanceSummary(line: InitiativeLine): CardModel["acceptance"] {
  const tally = line.acceptance;
  if (!tally.total) return { text: "No acceptance steps yet", mark: "pending" };
  if (tally.status === "FAIL") {
    return { text: `Acceptance failing · ${tally.failed} of ${tally.total} steps`, mark: "fail" };
  }
  if (tally.status === "ERROR") return { text: "Acceptance run broke (harness)", mark: "fail" };
  if (tally.status == null) {
    return {
      text: tally.running ? "Acceptance running" : "No acceptance run yet",
      mark: "pending",
    };
  }
  const skipped = tally.skipped ? ` · ${tally.skipped} cannot run in envs` : "";
  return { text: `Acceptance ${tally.passed}/${tally.total} pass${skipped}`, mark: "pass" };
}

/** The Home card of one initiative. */
export function cardModel(line: InitiativeLine): CardModel {
  const waiting = line.waiting_on ?? [];
  return {
    ref: line.ref,
    title: line.title,
    href: line.href,
    state: stateMeta(line.state),
    merged: line.merged,
    total: line.total,
    skeleton: line.skeleton
      ? line.skeleton_on_base
        ? { text: "Skeleton on the base env", ok: true }
        : { text: "Skeleton not on the base env yet", ok: false }
      : null,
    tryIt: line.skeleton_on_base ? tryIt(line.try_url, line.flag) : null,
    acceptance: acceptanceSummary(line),
    waiting: waiting.length
      ? `Waiting on ${waiting.slice(0, 2).join(", ")}${waiting.length > 2 ? ` +${waiting.length - 2}` : ""}`
      : null,
    next: line.next_wave ? `Next: ${line.next_wave}` : null,
    cost: formatUsd(line.cost_usd ?? null),
  };
}

/** Home lists the initiatives that are not cancelled, the ones in motion first. */
export function homeInitiatives(lines: readonly InitiativeLine[] | undefined): InitiativeLine[] {
  const rank: Record<string, number> = { failing: 0, accepting: 1, on_base: 2, building: 3 };
  return [...(lines ?? [])]
    .filter((line) => line.state !== "cancelled")
    .sort((a, b) => (rank[a.state] ?? 9) - (rank[b.state] ?? 9) || b.since - a.since);
}

// ── The page ───────────────────────────────────────────────────

export interface StepRow {
  n: number;
  step: string;
  expect: string | null;
  owner: string | null;
  needs: string[];
  mark: AcceptanceMark;
  /** The run's note: what it saw, or why the env cannot run the step. */
  note: string | null;
  screenshotUrl: string | null;
  /** A skipped step: the env lacks what it needs. */
  cannotRun: boolean;
}

export function stepRows(steps: readonly InitiativeStep[]): StepRow[] {
  return steps.map((step) => {
    const status = step.last?.status;
    const mark: AcceptanceMark =
      status === "pass"
        ? "pass"
        : status === "fail"
          ? "fail"
          : status === "skipped"
            ? "skipped"
            : "pending";
    return {
      n: step.n,
      step: step.step,
      expect: step.expect ?? null,
      owner: step.owner ?? null,
      needs: step.needs ?? [],
      mark,
      note: step.last?.note ?? null,
      screenshotUrl: step.last?.screenshot_url ?? null,
      cannotRun: mark === "skipped",
    };
  });
}

const RUN_WORD: Record<string, string> = {
  due: "waiting for the base env",
  requested: "running",
  blocked: "cannot run",
  expired: "no result came back",
  PASS: "passed",
  PARTIAL: "passed, some steps cannot run in envs",
  FAIL: "failed",
  ERROR: "broke (harness)",
};

/** `Run 2 · failed · abc1234 · tasks T-12`. */
export function runText(run: InitiativeRun): string {
  const parts = [`Run ${run.n}`, RUN_WORD[run.status] ?? run.status.toLowerCase()];
  if (run.sha) parts.push(shortSha(run.sha));
  if (run.tasks.length) parts.push(`tasks ${run.tasks.join(", ")}`);
  return parts.join(" · ");
}

/** The newest run, finished or not. */
export function lastRun(view: Pick<InitiativeView, "runs">): InitiativeRun | null {
  return view.runs.length ? view.runs[view.runs.length - 1] : null;
}

export interface FlowProject {
  slug: string;
  title: string;
  role: InitiativeProject["role"];
  roleLabel: string | null;
  stage: FactoryStage | null;
  merged: boolean;
  cancelled: boolean;
  waiting: string[];
  dependsOn: { slug: string; why: string | null; met: boolean }[];
  href: string | null;
}

export interface FlowWave {
  n: number;
  label: string;
  projects: FlowProject[];
}

const ROLE_LABEL: Record<InitiativeProject["role"], string | null> = {
  skeleton: "Walking skeleton",
  surface: "Faces users",
  part: null,
};

/** The projects as a small flow: one column per wave, the skeleton first. */
export function flowWaves(view: Pick<InitiativeView, "projects">): FlowWave[] {
  const waves = new Map<number, FlowProject[]>();
  for (const p of view.projects) {
    if (p.hidden) continue;
    const n = p.wave ?? 1;
    const list = waves.get(n) ?? [];
    list.push({
      slug: p.slug,
      title: p.title ?? p.slug,
      role: p.role,
      roleLabel: ROLE_LABEL[p.role],
      stage: (p.stage as FactoryStage | null | undefined) ?? null,
      merged: Boolean(p.merged),
      cancelled: p.status === "cancelled",
      waiting: p.waiting_on ?? [],
      dependsOn: (p.depends_on ?? []).map((d) => ({
        slug: d.slug,
        why: d.why ?? null,
        met: d.met,
      })),
      href: p.href ?? null,
    });
    waves.set(n, list);
  }
  const roleRank = { skeleton: 0, surface: 1, part: 2 } as const;
  return [...waves.entries()]
    .sort(([a], [b]) => a - b)
    .map(([n, projects]) => ({
      n,
      label: n === 1 ? "Wave 1 · walking skeleton" : `Wave ${n}`,
      projects: projects.sort((a, b) => roleRank[a.role] - roleRank[b.role]),
    }));
}

/** `Asked by ana (sample) · 2h ago` (`Asked 2h ago` without a name). */
export function askedText(view: Pick<InitiativeView, "asked_by" | "age_s">): string {
  const age = formatElapsed(view.age_s);
  const who = view.asked_by?.name ? ` by ${view.asked_by.name}` : "";
  return `Asked${who}${age ? ` · ${age} ago` : ""}`;
}

/** The page's facts line: merged, cost and the flag. */
export function factsText(view: InitiativeView): string {
  const parts = [mergedText(view.merged, view.total)];
  if (view.hidden) parts.push(`${plural(view.hidden, "project")} you cannot see`);
  const cost = formatUsd(view.cost_usd);
  if (cost) parts.push(`${cost} so far`);
  if (view.flag?.key) parts.push(`behind ${view.flag.key}`);
  else if (view.flag?.none) parts.push(`no flag: ${view.flag.none}`);
  return parts.join(" · ");
}
