// The new cockpit's Projects page as data: one outcome-first card per live
// project (why it exists, its stage, its lead's latest update, its team and
// its spend against its budget), the filters (all, from evidence, requested,
// needs you, stopped), the board's columns by stage, and the shipped fold
// with what each readout says. Built on Home's project rows, so both pages
// tell one story. Pure; `projectCards.test.ts` pins it.

import type { FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import type { TeamMember } from "../agents/teamsModel";
import { type HomeProjectRow, homeProjects } from "../home/homeModel";
import {
  type HealthChip,
  type ProjectWhy,
  healthChip,
  projectBudget,
  projectWhy,
} from "../project/projectModel";
import { STAGE_STEPS, STAGE_STEP_LABEL, type StageStep } from "../ui/statusTokens";

export const PROJECT_FILTERS = ["all", "evidence", "requested", "needs", "stopped"] as const;
export type ProjectFilter = (typeof PROJECT_FILTERS)[number];

export const FILTER_LABEL: Record<ProjectFilter, string> = {
  all: "All",
  evidence: "From evidence",
  requested: "Requested",
  needs: "Needs you",
  stopped: "Stopped",
};

export type ProjectsView = "cards" | "board";

export interface ProjectCardData extends HomeProjectRow {
  origin: ProjectWhy;
  health: HealthChip | null;
  /** The lead's latest update in one line, with who posted it. */
  update: { by: string | null; text: string } | null;
  spend: { spent: number; budget: number | null } | null;
  /** A merge waits on the viewer, or the project has an open question. */
  needsYou: boolean;
}

/** A shipped project and what its readout says (requested work is "not measured"). */
export interface ShippedCard {
  slug: string;
  title: string;
  readout: string;
  measured: boolean;
}

function updateLine(project: ProjectView): ProjectCardData["update"] {
  const update = project.update;
  if (!update) return null;
  const text = update.done ?? update.next ?? update.blocker ?? null;
  return text ? { by: update.by ?? null, text } : null;
}

function readoutWords(project: ProjectView): string {
  const why = project.why;
  if (!why?.metric) return "not measured";
  const days = why.measurement?.readout_days ?? [];
  return days.length ? `readout at day ${days.join(" and ")}` : "readout after release";
}

/**
 * One card per live project, in Home's order (working, waiting for a merge,
 * stopped), and the shipped ones.
 *
 * @param mergeForYou - Projects whose merge waits on the viewer; null: any ready PR.
 */
export function projectCards(
  snapshot: FactorySnapshot,
  members: readonly TeamMember[],
  nowS: number,
  mergeForYou: ReadonlySet<string> | null = null,
): { cards: ProjectCardData[]; shipped: ShippedCard[] } {
  const bySlug = new Map(snapshot.projects.map((p) => [p.slug, p]));
  const { rows, shipped } = homeProjects(snapshot, members, nowS, mergeForYou);
  const cards = rows.map((row): ProjectCardData => {
    const project = bySlug.get(row.slug)!;
    const merge = row.stripe === "amber";
    return {
      ...row,
      origin: projectWhy(project),
      health: healthChip(project, { mergeForYou: merge, nowS }),
      update: updateLine(project),
      spend:
        project.cost_usd != null || projectBudget(project) != null
          ? { spent: project.cost_usd ?? 0, budget: projectBudget(project) }
          : null,
      needsYou: merge || project.open_decision_ids.length > 0,
    };
  });
  return {
    cards,
    shipped: shipped.map((s) => {
      const project = bySlug.get(s.slug)!;
      return { slug: s.slug, title: s.title, readout: readoutWords(project), measured: s.measured };
    }),
  };
}

/** The cards a filter keeps. */
export function filterCards(
  cards: readonly ProjectCardData[],
  filter: ProjectFilter,
): ProjectCardData[] {
  switch (filter) {
    case "all":
      return [...cards];
    case "evidence":
      return cards.filter((c) => c.origin.kind === "evidence");
    case "requested":
      return cards.filter((c) => c.origin.kind === "requested" || c.origin.kind === "idea");
    case "needs":
      return cards.filter((c) => c.needsYou);
    case "stopped":
      return cards.filter((c) => c.broken);
  }
}

/** How many cards each filter keeps. */
export function filterCounts(cards: readonly ProjectCardData[]): Record<ProjectFilter, number> {
  return Object.fromEntries(
    PROJECT_FILTERS.map((f) => [f, filterCards(cards, f).length]),
  ) as Record<ProjectFilter, number>;
}

/** The filter a `?f=` value names (all otherwise). */
export function filterOf(value: string | null | undefined): ProjectFilter {
  return (PROJECT_FILTERS as readonly string[]).includes(value ?? "")
    ? (value as ProjectFilter)
    : "all";
}

/** Ready for merge is a bottleneck once this many projects wait there. */
export const BOTTLENECK_AT = 3;

export interface BoardColumn {
  step: StageStep;
  label: string;
  cards: ProjectCardData[];
  shipped: ShippedCard[];
  /** Work piles up here waiting for people (Ready for merge). */
  bottleneck: boolean;
}

/**
 * The board: one column per stage (Plan to Shipped). Stopped projects sit in
 * no column (the page lists them under the board); shipped ones fill the last.
 */
export function boardColumns(
  cards: readonly ProjectCardData[],
  shipped: readonly ShippedCard[],
): BoardColumn[] {
  return STAGE_STEPS.map((step) => {
    const here = cards.filter((c) => !c.broken && c.step === step);
    return {
      step,
      label: STAGE_STEP_LABEL[step],
      cards: here,
      shipped: step === "shipped" ? [...shipped] : [],
      bottleneck: step === "ready" && here.length >= BOTTLENECK_AT,
    };
  });
}
