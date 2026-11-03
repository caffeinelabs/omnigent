// Pure project-page logic: the project's decisions and team, its demos
// grouped by PR with step tallies, and for the new cockpit why it exists (the
// why-line and the why-chain), its health chip, its PRs' gates in words, its
// measurement plan, spend by member and the page's tabs. Components render
// these; tests pin them.

import type {
  BetExpectedView,
  DecisionView,
  DemoView,
  FactorySnapshot,
  GateState,
  MemberRef,
  PrView,
  ProjectDemoGroupView,
  ProjectDemoView,
  ProjectHealth,
  ProjectView,
} from "@/lib/factoryApi";
import { formatElapsed, plural } from "../factoryFormat";
import type { ProjectTab } from "../factoryRoutes";
import { STAGE_STEP_LABEL, stageStepOf } from "../ui/statusTokens";
import type { WhyLink } from "../ui/WhyChain";

/** Decisions about the project: filed under it, or asked by its PL. */
export function projectDecisions(snapshot: FactorySnapshot, project: ProjectView): DecisionView[] {
  const pl = project.pl?.session_id;
  return snapshot.decisions.filter(
    (d) => d.project_slug === project.slug || (pl != null && d.asked_by_session_id === pl),
  );
}

/** The PL first, then its team. */
export function projectTeam(project: ProjectView): MemberRef[] {
  return [...(project.pl ? [project.pl] : []), ...project.team];
}

// ── Demos ──────────────────────────────────────────────────────

/** One PR's demos: its newest run and the earlier ones, newest first. */
export interface DemoGroup {
  key: string;
  repo: string;
  prNumber: number;
  /** The PR as the snapshot tracks it; null for a PR the board doesn't show. */
  pr: PrView | null;
  latest: ProjectDemoView;
  earlier: ProjectDemoView[];
}

function newestFirst(a: ProjectDemoView, b: ProjectDemoView): number {
  return b.created_at - a.created_at || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0);
}

/** Demos grouped by PR, groups ordered by their newest run. */
export function groupDemosByPr(demos: readonly ProjectDemoView[], prs: readonly PrView[]) {
  const groups = new Map<string, ProjectDemoView[]>();
  for (const demo of [...demos].sort(newestFirst)) {
    const key = `${demo.repo}#${demo.pr_number}`;
    const list = groups.get(key);
    if (list) list.push(demo);
    else groups.set(key, [demo]);
  }
  return [...groups.entries()].map(([key, [latest, ...earlier]]): DemoGroup => {
    const demo = latest!;
    return {
      key,
      repo: demo.repo,
      prNumber: demo.pr_number,
      pr: prs.find((p) => p.repo === demo.repo && p.number === demo.pr_number) ?? null,
      latest: demo,
      earlier,
    };
  });
}

/**
 * The server's per-PR groups (every run, newest first) as cards: each with
 * the PR as the board tracks it, its newest run and the earlier ones. Groups
 * keep the server's order (by newest run); runs are re-sorted defensively.
 */
export function demoGroupsFrom(
  groups: readonly ProjectDemoGroupView[],
  prs: readonly PrView[],
): DemoGroup[] {
  return groups.flatMap((group): DemoGroup[] => {
    const [latest, ...earlier] = [...group.runs].sort(newestFirst);
    if (!latest) return [];
    return [
      {
        key: `${group.repo}#${group.pr_number}`,
        repo: group.repo,
        prNumber: group.pr_number,
        pr:
          prs.find(
            (p) =>
              (group.pr_id != null && p.id === group.pr_id) ||
              (p.repo === group.repo && p.number === group.pr_number),
          ) ?? null,
        latest,
        earlier,
      },
    ];
  });
}

/** Runs across every group. */
export function demoRunCount(groups: readonly DemoGroup[]): number {
  return groups.reduce((n, g) => n + 1 + g.earlier.length, 0);
}

/** Open PRs with no demo recorded yet (in the list or on the snapshot). */
export function undemoedPrs(prs: readonly PrView[], groups: readonly DemoGroup[]): PrView[] {
  const demoed = new Set(groups.map((g) => g.key));
  return prs.filter(
    (pr) => pr.state === "open" && !pr.demo && !demoed.has(`${pr.repo}#${pr.number}`),
  );
}

export interface StepTally {
  passed: number;
  failed: number;
  skipped: number;
}

export function stepTally(demo: DemoView): StepTally {
  const tally: StepTally = { passed: 0, failed: 0, skipped: 0 };
  for (const step of demo.steps) {
    if (step.status === "pass") tally.passed += 1;
    else if (step.status === "fail") tally.failed += 1;
    else tally.skipped += 1;
  }
  return tally;
}

/** `"7 passed · 1 skipped"`; `"no steps"` for a demo that recorded none. */
export function tallyText(tally: StepTally): string {
  const parts = [
    tally.passed > 0 && `${tally.passed} passed`,
    tally.failed > 0 && `${tally.failed} failed`,
    tally.skipped > 0 && `${tally.skipped} skipped`,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" · ") : "no steps";
}

/** `"2 PRs · 3 runs"`. */
export function demosSummary(groups: readonly DemoGroup[]): string {
  return `${plural(groups.length, "PR")} · ${plural(demoRunCount(groups), "run")}`;
}

/** Where a PR lives on GitHub when the board doesn't track it. */
export function githubPrUrl(repo: string, number: number): string {
  return `https://github.com/${repo}/pull/${number}`;
}

// ── The new cockpit: why, health, gates, measurement, spend ────

/**
 * Why a project exists: from user evidence (an opportunity), a person's
 * request, an agent's idea (or a hunch, counted as one), or that it stopped.
 */
export type WhyKind = "evidence" | "requested" | "idea" | "stopped";

export interface ProjectWhy {
  kind: WhyKind;
  /** One line, e.g. `from O-3 · measured by weekly publishes`, `No user evidence · requested by mia · not measured`. */
  text: string;
  /** The opportunity it comes from (`O-3`). */
  opportunity: string | null;
  /** Who asked for it (a person or an agent), when known. */
  by: string | null;
  /** A metric is bound to it, so it is read out after release. */
  measured: boolean;
  /** "No user evidence" leads the line (drawn dotted, never as success). */
  noEvidence: boolean;
}

function personName(userId: string): string {
  return userId.split("@")[0] || userId;
}

/** Who asked for the project, in words (`mia`, `Atlas`), when the server says. */
export function requesterName(project: ProjectView): string | null {
  const by = project.requested_by;
  if (by?.name) return by.kind === "human" ? personName(by.name) : by.name;
  return project.human_owner ? personName(project.human_owner) : null;
}

export function projectWhy(project: ProjectView): ProjectWhy {
  const why = project.why ?? null;
  const by = requesterName(project);
  const base = { opportunity: why?.opportunity ?? null, by, measured: Boolean(why?.metric) };
  if (project.overlay === "failed") {
    const reason = (project.overlay_reason ?? "").split(" · ")[0]?.trim();
    return {
      ...base,
      kind: "stopped",
      text: reason ? `Stopped: ${reason}` : "Stopped",
      noEvidence: false,
    };
  }
  if (why?.opportunity) {
    const measured = why.metric ? `measured by ${why.metric}` : "not measured yet";
    return {
      ...base,
      kind: "evidence",
      text: `from ${why.opportunity} · ${measured}`,
      noEvidence: false,
    };
  }
  const tail = why?.metric ? `measured by ${why.metric}` : "not measured";
  if (project.requested_by?.kind === "agent") {
    return {
      ...base,
      kind: "idea",
      text: `No user evidence · ${by ?? "an agent"}'s idea, counted as a hunch · ${tail}`,
      noEvidence: true,
    };
  }
  if (why?.kind === "hunch") {
    return {
      ...base,
      kind: "idea",
      text: `No user evidence · ${by ? `${by}'s hunch` : "a hunch, counted as one"} · ${tail}`,
      noEvidence: true,
    };
  }
  return {
    ...base,
    kind: "requested",
    text: `No user evidence · requested${by ? ` by ${by}` : ""} · ${tail}`,
    noEvidence: true,
  };
}

/** `+0.01 to +0.04 share`, `-70 to -40 %`: a bet's predicted range, signed. */
export function expectedRange(expected: BetExpectedView | null | undefined): string | null {
  if (!expected) return null;
  const signed = (n: number) => (n > 0 ? `+${n}` : String(n));
  return `${signed(expected.low)} to ${signed(expected.high)} ${expected.unit}`.trim();
}

/** What the why-chain's evidence and opportunity boxes show, from the discovery ledger. */
export interface OpportunityFacts {
  accounts: number | null;
  hunches: number | null;
  level: string | null;
  rank: number | null;
  /** Signals per source (`forum 22 · trk 12`). */
  bySource: Record<string, number>;
  /**
   * The newest readout written back to the opportunity, from its own why-chain
   * (`ref` like `C-31-d7`, `label` like `day 7 · won`); null before any.
   */
  readout: { ref: string; label: string } | null;
}

/** The ledger's opportunity (`GET …/opportunities/O-n`), read field by field. */
export function opportunityFacts(raw: unknown): OpportunityFacts | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  const evidence =
    row.evidence && typeof row.evidence === "object"
      ? (row.evidence as Record<string, unknown>)
      : {};
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const bySource: Record<string, number> = {};
  if (evidence.by_source && typeof evidence.by_source === "object") {
    for (const [key, value] of Object.entries(evidence.by_source as Record<string, unknown>)) {
      if (typeof value === "number" && value > 0) bySource[key] = value;
    }
  }
  const chain = Array.isArray(row.why) ? (row.why as unknown[]) : [];
  const link = chain.find(
    (l): l is Record<string, unknown> =>
      Boolean(l) && typeof l === "object" && (l as Record<string, unknown>).kind === "readout",
  );
  const readout =
    link && link.missing !== true && typeof link.ref === "string" && typeof link.label === "string"
      ? { ref: link.ref, label: link.label }
      : null;
  return {
    accounts: num(evidence.accounts),
    hunches: num(evidence.hunches),
    level: typeof evidence.level === "string" ? evidence.level : null,
    rank: num(row.rank),
    bySource,
    readout,
  };
}

/** `.50` for a rank under one, else the number. */
function rankText(rank: number): string {
  return rank < 1 ? rank.toFixed(2).replace(/^0/, "") : String(rank);
}

export interface WhyChainOptions {
  /** The opportunity's evidence, when the ledger answered. */
  opportunity?: OpportunityFacts | null;
  /** Href of the opportunity's panel. */
  opportunityHref?: string | null;
}

/**
 * Evidence → Opportunity → Bet → Project → Readout for one project; a link
 * that does not exist yet is missing (drawn hatched) and says what is absent.
 */
export function projectWhyChain(project: ProjectView, options: WhyChainOptions = {}): WhyLink[] {
  const why = project.why ?? null;
  const facts = options.opportunity ?? null;
  const stopped = project.overlay === "failed";
  const links: WhyLink[] = [];
  if (why?.opportunity) {
    const sources = Object.entries(facts?.bySource ?? {})
      .map(([key, n]) => `${key} ${n}`)
      .join(" · ");
    const hunches = facts?.hunches
      ? `${facts.hunches} ${facts.hunches === 1 ? "hunch" : "hunches"}`
      : "";
    links.push({
      kind: "evidence",
      value: facts?.accounts
        ? `${plural(facts.accounts, "account")}`
        : facts?.hunches
          ? hunches
          : `see ${why.opportunity}`,
      detail: [sources, facts?.accounts ? hunches : ""].filter(Boolean).join(" · ") || undefined,
    });
    links.push({
      kind: "opportunity",
      value: facts?.level ? `${why.opportunity} · ${facts.level}` : why.opportunity,
      detail: facts?.rank != null ? `score ${rankText(facts.rank)}` : undefined,
      to: options.opportunityHref ?? undefined,
    });
  } else {
    links.push({ kind: "evidence", value: "none", detail: "no user evidence", missing: true });
    links.push({ kind: "opportunity", value: "none", detail: "not linked", missing: true });
  }
  if (why?.kind) {
    const range = expectedRange(why.expected);
    links.push({
      kind: why.kind === "test" ? "test" : "bet",
      value: why.proposal ?? why.kind,
      detail: range ? `expects ${range}` : (why.metric ?? undefined),
    });
  } else {
    links.push({ kind: "bet", value: "none", detail: "no metric or range", missing: true });
  }
  links.push({
    kind: "project",
    value: project.slug,
    detail: stopped ? "stopped" : STAGE_STEP_LABEL[stageStepOf(project.stage)],
  });
  const days = why?.measurement?.readout_days ?? [];
  // The ledger's newest readout counts here only when it is this project's bet's (`C-31-d7`).
  const readout =
    facts?.readout && why?.proposal && facts.readout.ref.startsWith(`${why.proposal}-`)
      ? facts.readout
      : null;
  links.push(
    readout
      ? { kind: "readout", value: readout.label, detail: readout.ref }
      : {
          kind: "readout",
          value: "not yet",
          detail: why?.metric
            ? days.length
              ? `day ${days.join(" and ")} after release`
              : "after release"
            : "not measured",
          missing: true,
        },
  );
  return links;
}

/** The project's health chip: stopped, your merge, stale, or the shown health. */
export interface HealthChip {
  tone: "green" | "amber" | "red" | "grey" | "neutral";
  label: string;
  /** What the chip means in full (a tooltip and the accessible name). */
  title: string;
}

export const HEALTH_WORD: Record<ProjectHealth, string> = {
  on_track: "On track",
  at_risk: "At risk",
  off_track: "Off track",
};

/**
 * Stopped (red) first, then a merge that waits on the viewer (amber), a
 * closed or shipped project, a stale lead update (grey), and the health the
 * server shows (green on track, red off track, neutral at risk: amber is only
 * for what needs a person). Null from a server without health.
 */
export function healthChip(
  project: ProjectView,
  options: { mergeForYou?: boolean; nowS: number },
): HealthChip | null {
  if (project.overlay === "failed") {
    return { tone: "red", label: "Stopped", title: "The project stopped" };
  }
  if (project.status && project.status !== "active") {
    return { tone: "neutral", label: "Closed", title: `The project is ${project.status}` };
  }
  if (project.stage === "merged" || project.stage === "done") {
    return { tone: "neutral", label: "Shipped", title: "Its work is merged" };
  }
  if (options.mergeForYou) {
    return {
      tone: "amber",
      label: "Needs your merge",
      title: "A pull request is ready and waits for your merge",
    };
  }
  const health = project.health;
  if (!health) return null;
  const word = HEALTH_WORD[health.health];
  if (health.stale && project.update) {
    const age = formatElapsed(options.nowS - project.update.at) ?? "";
    return {
      tone: "grey",
      label: `${word} · stale ${age}`.trim(),
      title: `The lead's update is ${age} old; this is computed from facts`,
    };
  }
  const why = health.reasons.length ? `: ${health.reasons.join(", ")}` : "";
  return {
    tone:
      health.health === "on_track" ? "green" : health.health === "off_track" ? "red" : "neutral",
    label: word,
    title:
      health.source === "facts" && health.agrees === false
        ? `${word} from the facts${why} (the lead said ${project.update ? HEALTH_WORD[project.update.health].toLowerCase() : "nothing"})`
        : `${word}${why}`,
  };
}

const GATE_WORD: Record<string, string> = {
  pass: "passed",
  fail: "failed",
  pending: "waiting",
};

/** `Checks passed · Review waiting · Verify waiting · Demo waiting`: a PR's gates in words. */
export function gateWords(pr: PrView): string[] {
  const out: string[] = [];
  const gates = pr.gates;
  const add = (label: string, state: GateState | null | undefined, running = "waiting") => {
    if (!state || state === "skip" || state === "unknown") return;
    out.push(`${label} ${state === "pending" ? running : (GATE_WORD[state] ?? state)}`);
  };
  add("Checks", gates.checks, "running");
  add("Review", gates.review);
  add("Verify", gates.verify);
  add("Demo", gates.demo);
  return out;
}

const METHOD_WORD: Record<string, string> = {
  controlled: "controlled (flag on vs off)",
  before_after: "before and after release",
  adoption: "adoption",
  signal_trend: "signal trend",
  human_verdict: "a person's verdict",
};

/** The frozen measurement plan as label/value rows, or null for work without a metric. */
export function measurementPlan(project: ProjectView): { label: string; value: string }[] | null {
  const why = project.why;
  if (!why?.metric) return null;
  const rows: { label: string; value: string }[] = [{ label: "Metric", value: why.metric }];
  if (why.measurement?.method) {
    rows.push({
      label: "Method",
      value: METHOD_WORD[why.measurement.method] ?? why.measurement.method,
    });
  }
  const range = expectedRange(why.expected);
  if (range) rows.push({ label: "Expects", value: range });
  const days = why.measurement?.readout_days ?? [];
  if (days.length)
    rows.push({ label: "Readouts", value: `day ${days.join(" and ")} after release` });
  if (why.guardrails?.length) rows.push({ label: "Guardrails", value: why.guardrails.join("; ") });
  if (why.kill) rows.push({ label: "Kill if", value: `${why.kill.state} by ${why.kill.date}` });
  return rows;
}

/** Each member's spend on the project (largest first), for the Spend card. */
export function spendByMember(
  snapshot: FactorySnapshot,
  project: ProjectView,
): { id: string; name: string; usd: number }[] {
  return snapshot.members
    .filter((m) => m.project === project.slug && (m.cost_usd ?? 0) > 0)
    .map((m) => ({ id: m.session_id, name: m.handle ?? m.title, usd: m.cost_usd ?? 0 }))
    .sort((a, b) => b.usd - a.usd || a.name.localeCompare(b.name));
}

/**
 * The project's budget: its team spec's, else the one the server measured its
 * health against (the charter's project budget); null when neither sets one.
 */
export function projectBudget(project: ProjectView): number | null {
  for (const value of [project.team_spec?.budget_usd, project.health?.budget_usd]) {
    if (typeof value === "number" && value > 0) return value;
  }
  return null;
}

/** The tab an old section anchor (`#prs`, `#activity`) belongs in. */
export function tabOfSection(hash: string | null | undefined): ProjectTab | null {
  switch ((hash ?? "").replace(/^#/, "")) {
    case "overview":
    case "decisions":
      return "overview";
    case "tasks":
    case "prs":
    case "demos":
    case "previews":
    case "loops":
      return "work";
    case "activity":
      return "history";
    case "charter":
      return "charter";
    default:
      return null;
  }
}
