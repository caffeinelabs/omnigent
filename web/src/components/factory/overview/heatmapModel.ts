// The Overview's activity heatmap: the last 60 minutes per project in 2-minute
// cells, each the most telling thing that happened in it (a tool running,
// an agent thinking, waiting on a human, waiting on CI or a preview). Spans
// are the source of truth; without them (an older server) events are bucketed
// instead. Pure; tests pin it.

import type { EventView, FactorySnapshot, ProjectView, SpanKind, SpanView } from "@/lib/factoryApi";
import { isClosedProject, isIntegrationProject } from "../board/boardModel";
import { formatElapsed } from "../factoryFormat";

export const HEATMAP_WINDOW_S = 60 * 60;
export const HEATMAP_CELL_S = 2 * 60;
export const HEATMAP_CELLS = HEATMAP_WINDOW_S / HEATMAP_CELL_S;

export type CellKind = "tool" | "thinking" | "human" | "ci" | "idle";

/** Which wins when several things share a cell. */
const PRIORITY: Record<CellKind, number> = { idle: 0, ci: 1, human: 2, thinking: 3, tool: 4 };

const SPAN_CELL: Partial<Record<SpanKind, CellKind>> = {
  tool: "tool",
  turn: "thinking",
  chat_turn: "thinking",
  loop_iter: "thinking",
  spawn: "thinking",
  intervention: "thinking",
  wait: "human",
  decision: "human",
  ready_wait: "human",
  gate: "ci",
  pr_round: "ci",
  env: "ci",
  preview: "ci",
  demo: "ci",
};

/** An event's cell kind (the fallback when the server has no spans). */
export function eventCellKind(kind: string): CellKind | null {
  if (kind.startsWith("heartbeat.")) return null;
  if (kind.startsWith("decision.") || kind === "needs_human") return "human";
  if (/^(gate|pr|preview|demo|env)\./.test(kind)) return "ci";
  return "thinking";
}

export interface HeatmapRow {
  slug: string;
  title: string;
  cells: CellKind[];
  /** Cells with anything in them. */
  busy: number;
  /** Why the project sits where it does now, e.g. `waiting on D-2 · 34m`. */
  note: string | null;
  tone: "muted" | "warning" | "fail";
}

/** The window's first second: `now - 60 min`, rounded down to a cell edge. */
export function heatmapStartS(nowS: number): number {
  return Math.floor((nowS - HEATMAP_WINDOW_S) / HEATMAP_CELL_S) * HEATMAP_CELL_S;
}

function paint(cells: CellKind[], startS: number, fromS: number, toS: number, kind: CellKind) {
  const first = Math.max(0, Math.floor((fromS - startS) / HEATMAP_CELL_S));
  const last = Math.min(HEATMAP_CELLS - 1, Math.floor((toS - 1 - startS) / HEATMAP_CELL_S));
  for (let i = first; i <= last; i += 1) {
    if (PRIORITY[kind] > PRIORITY[cells[i]!]) cells[i] = kind;
  }
}

/** What waits, or went wrong, on a project right now. */
export function projectNote(
  snapshot: FactorySnapshot,
  project: ProjectView,
  nowS: number,
): { note: string | null; tone: HeatmapRow["tone"] } {
  const pl = project.pl;
  if (pl && (pl.health === "dead" || pl.status === "failed")) {
    const since =
      pl.last_activity_at != null ? ` ${formatElapsed(nowS - pl.last_activity_at)}` : "";
    return { note: `✕ PL down${since}`, tone: "fail" };
  }
  const blocking = snapshot.decisions.find(
    (d) => d.status === "open" && d.blocking && project.open_decision_ids.includes(d.id),
  );
  if (blocking) {
    return {
      note: `waiting on ${blocking.ref} · ${formatElapsed(nowS - blocking.created_at)}`,
      tone: "warning",
    };
  }
  if (project.overlay === "paused") return { note: "paused", tone: "muted" };
  if (project.overlay === "waiting") {
    const deps = project.depends_on ?? [];
    return { note: deps.length ? `waiting on ${deps.join(", ")}` : "waiting", tone: "muted" };
  }
  if (project.stage === "ready") {
    return {
      note: `waiting for human ${formatElapsed(nowS - project.stage_since)}`,
      tone: "muted",
    };
  }
  if (project.overlay === "failed") return { note: "failed", tone: "fail" };
  return { note: null, tone: "muted" };
}

/** The projects a heatmap shows: work in flight, the busiest first. */
function rowProjects(snapshot: FactorySnapshot): ProjectView[] {
  return snapshot.projects.filter((p) => !isClosedProject(p) && !isIntegrationProject(p));
}

function finish(
  snapshot: FactorySnapshot,
  grid: Map<string, CellKind[]>,
  nowS: number,
  limit: number,
): HeatmapRow[] {
  const rows = rowProjects(snapshot).map((project): HeatmapRow => {
    const cells = grid.get(project.slug) ?? Array<CellKind>(HEATMAP_CELLS).fill("idle");
    const { note, tone } = projectNote(snapshot, project, nowS);
    return {
      slug: project.slug,
      title: project.title,
      cells,
      busy: cells.filter((c) => c !== "idle").length,
      note,
      tone,
    };
  });
  return rows
    .sort((a, b) => Number(b.tone !== "muted") - Number(a.tone !== "muted") || b.busy - a.busy)
    .slice(0, limit);
}

function emptyGrid(snapshot: FactorySnapshot): Map<string, CellKind[]> {
  return new Map(
    rowProjects(snapshot).map((p) => [p.slug, Array<CellKind>(HEATMAP_CELLS).fill("idle")]),
  );
}

/** Rows from spans (`started_at`/`ended_at` in ms; an open span runs to now). */
export function heatmapFromSpans(
  snapshot: FactorySnapshot,
  spans: readonly SpanView[],
  nowS: number,
  limit = 8,
): HeatmapRow[] {
  const startS = heatmapStartS(nowS);
  const grid = emptyGrid(snapshot);
  for (const span of spans) {
    const kind = SPAN_CELL[span.kind];
    const cells = span.project_slug ? grid.get(span.project_slug) : undefined;
    if (!kind || !cells) continue;
    const from = Math.floor(span.started_at / 1000);
    const to = span.ended_at != null ? Math.ceil(span.ended_at / 1000) : nowS;
    if (to <= startS || from >= nowS) continue;
    // A span shorter than a cell still marks it.
    paint(cells, startS, from, Math.max(to, from + 1), kind);
  }
  return finish(snapshot, grid, nowS, limit);
}

/** Rows from events (no spans on this server): each event marks its cell. */
export function heatmapFromEvents(
  snapshot: FactorySnapshot,
  events: readonly EventView[],
  nowS: number,
  limit = 8,
): HeatmapRow[] {
  const startS = heatmapStartS(nowS);
  const grid = emptyGrid(snapshot);
  for (const event of events) {
    const kind = eventCellKind(event.kind);
    const cells = event.project_slug ? grid.get(event.project_slug) : undefined;
    const at = Math.floor(event.ts / 1000);
    if (!kind || !cells || at < startS || at >= nowS + HEATMAP_CELL_S) continue;
    paint(cells, startS, at, at + 1, kind);
  }
  return finish(snapshot, grid, nowS, limit);
}
