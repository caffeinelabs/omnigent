// Pure projects-table logic: which projects are work, environments or closed,
// the filters, and each row's facts (stage, tasks, PR and gates, loops,
// agents, spend, owner, dependencies, flags). Tests pin it.

import type {
  DecisionView,
  FactorySnapshot,
  GateState,
  PrView,
  ProjectView,
} from "@/lib/factoryApi";
import {
  isClosedProject,
  isIntegrationProject,
  orderProjects,
  projectPrList,
} from "../board/boardModel";
import { gateState, shownGates, GATE_KEY_LABEL, STATE_WORD } from "../board/GateChip";
import { type FactoryTone, primaryPr } from "../factoryFormat";

export interface ProjectSections {
  work: ProjectView[];
  envs: ProjectView[];
  closed: ProjectView[];
}

/** Work in board order, the live base env(s), and closed projects. */
export function projectSections(projects: readonly ProjectView[]): ProjectSections {
  const ordered = orderProjects([...projects]);
  return {
    work: ordered.filter((p) => !isIntegrationProject(p) && !isClosedProject(p)),
    envs: ordered.filter((p) => isIntegrationProject(p)),
    closed: ordered.filter((p) => !isIntegrationProject(p) && isClosedProject(p)),
  };
}

export type ProjectFilter = "all" | "attention" | "building" | "ready" | "waiting" | "paused";

export const PROJECT_FILTER_LABEL: Record<ProjectFilter, string> = {
  all: "All",
  attention: "Needs attention",
  building: "In progress",
  ready: "Ready",
  waiting: "Queued / waiting",
  paused: "Paused",
};

const IN_PROGRESS = new Set(["planning", "building", "review", "verify"]);

/** Something about the project wants a human's eye. */
export function needsAttention(snapshot: FactorySnapshot, project: ProjectView): boolean {
  if (project.overlay === "blocked" || project.overlay === "failed") return true;
  if (project.overlay === "pr_closed" || project.claim_mismatch) return true;
  const loops = project.loops_summary;
  if (loops && loops.exhausted + loops.escalated + loops.thrashing > 0) return true;
  return openDecisions(snapshot, project).length > 0;
}

export function matchesFilter(
  snapshot: FactorySnapshot,
  project: ProjectView,
  filter: ProjectFilter,
): boolean {
  switch (filter) {
    case "all":
      return true;
    case "attention":
      return needsAttention(snapshot, project);
    case "building":
      return IN_PROGRESS.has(project.stage) && project.overlay !== "paused";
    case "ready":
      return project.stage === "ready";
    case "waiting":
      return project.stage === "queued" || project.overlay === "waiting";
    case "paused":
      return project.overlay === "paused";
  }
}

function openDecisions(snapshot: FactorySnapshot, project: ProjectView): DecisionView[] {
  const ids = new Set(project.open_decision_ids);
  return snapshot.decisions.filter((d) => d.status === "open" && ids.has(d.id));
}

export interface RowFlag {
  key: string;
  label: string;
  tone: FactoryTone;
  title: string;
}

export interface GateDot {
  key: string;
  state: GateState;
  label: string;
}

export interface ProjectRowFacts {
  prs: PrView[];
  pr: PrView | null;
  gates: GateDot[];
  /** `"1/3"`, or null without a work graph. */
  tasks: string | null;
  /** `"impl 3/3"`, `"3▶ 1⛔"`, or null without loops. */
  loops: string | null;
  loopsTone: FactoryTone;
  agents: number;
  running: number;
  usdPerH: number | null;
  owner: string | null;
  dependsOn: string[];
  flags: RowFlag[];
}

/** One gate dot per shown gate of `pr`, for a dense row. */
export function gateDots(pr: PrView): GateDot[] {
  return shownGates(pr).flatMap((gate) => {
    const state = gateState(pr, gate);
    return state
      ? [{ key: gate, state, label: `${GATE_KEY_LABEL[gate]} ${STATE_WORD[state]}` }]
      : [];
  });
}

function loopsText(project: ProjectView): { text: string | null; tone: FactoryTone } {
  const loops = project.loops_summary;
  if (!loops) return { text: null, tone: "muted" };
  const parts = [
    loops.running > 0 && `${loops.running}▶`,
    loops.exhausted > 0 && `${loops.exhausted}⛔`,
    loops.thrashing + loops.stalled > 0 && `${loops.thrashing + loops.stalled}⟳`,
    loops.escalated > 0 && `${loops.escalated}↑`,
  ].filter(Boolean) as string[];
  const bad = loops.exhausted + loops.escalated + loops.thrashing > 0;
  if (parts.length === 0) return { text: loops.converged > 0 ? "✓" : "—", tone: "muted" };
  return { text: parts.join(" "), tone: bad ? "warning" : "active" };
}

/** Everything a projects-table row shows about `project`. */
export function projectRowFacts(snapshot: FactorySnapshot, project: ProjectView): ProjectRowFacts {
  const prs = projectPrList(snapshot, project);
  const pr = primaryPr(prs);
  const team = [...(project.pl ? [project.pl] : []), ...project.team];
  const members = snapshot.members.filter((m) => m.project === project.slug);
  const rates = members.map((m) => m.now?.usd_per_h).filter((v): v is number => v != null);
  const summary = project.tasks_summary;
  const loops = loopsText(project);
  const flags: RowFlag[] = [];
  for (const decision of openDecisions(snapshot, project)) {
    flags.push({
      key: decision.id,
      label: decision.ref,
      tone: decision.blocking ? "warning" : "muted",
      title: decision.question,
    });
  }
  if (project.claim_mismatch) {
    flags.push({
      key: "claim",
      label: "claim ≠ gates",
      tone: "warning",
      title: "The PL claims a stage the gates don’t show",
    });
  }
  if (project.overlay === "failed") {
    flags.push({
      key: "failed",
      label: "failed",
      tone: "fail",
      title: project.overlay_reason ?? "Failed",
    });
  }
  if (project.overlay === "waiting" && (project.depends_on?.length ?? 0) > 0) {
    flags.push({
      key: "waiting",
      label: `after ${project.depends_on!.join(", ")}`,
      tone: "muted",
      title: project.overlay_reason ?? "Waits on the projects it depends on",
    });
  }
  if (project.overlay === "pr_closed") {
    flags.push({
      key: "pr_closed",
      label: "PR closed",
      tone: "warning",
      title: project.overlay_reason ?? "A PR was closed",
    });
  }
  return {
    prs,
    pr,
    gates: pr && pr.state === "open" ? gateDots(pr) : [],
    tasks: summary && summary.total > 0 ? `${summary.done}/${summary.total}` : null,
    loops: loops.text,
    loopsTone: loops.tone,
    agents: team.length,
    running: team.filter((m) => m.running).length,
    usdPerH: rates.length > 0 ? rates.reduce((a, b) => a + b, 0) : null,
    owner: project.human_owner ?? null,
    dependsOn: project.depends_on ?? [],
    flags,
  };
}

/** `"7 active · 3 closed"`. */
export function sectionsSummary(sections: ProjectSections): string {
  const parts = [`${sections.work.length} active`];
  if (sections.closed.length > 0) parts.push(`${sections.closed.length} closed`);
  return parts.join(" · ");
}
