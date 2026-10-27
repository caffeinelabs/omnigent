// Pure project-page logic: the project's decisions and team, and its demos
// grouped by PR with step tallies. Components render these; tests pin them.

import type {
  DecisionView,
  DemoView,
  FactorySnapshot,
  MemberRef,
  PrView,
  ProjectDemoView,
  ProjectView,
} from "@/lib/factoryApi";
import { plural } from "../factoryFormat";

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
  const runs = groups.reduce((n, g) => n + 1 + g.earlier.length, 0);
  return `${plural(groups.length, "PR")} · ${plural(runs, "run")}`;
}

/** Where a PR lives on GitHub when the board doesn't track it. */
export function githubPrUrl(repo: string, number: number): string {
  return `https://github.com/${repo}/pull/${number}`;
}
