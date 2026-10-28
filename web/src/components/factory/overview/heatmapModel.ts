// The Overview's activity heatmap: the last 60 minutes per project in 2-minute
// cells, each the most telling thing that happened in it (a tool running,
// an agent thinking, waiting on a human, waiting on CI or a preview).
//
// Waits come from spans (decisions, ready waits, gates, env and preview
// transitions). Agent activity comes from events and the snapshot's "now"
// until the server writes turn and tool spans, which then paint too. Without
// spans (an older server) events are bucketed by kind instead. Pure; tests
// pin it.

import type { EventView, FactorySnapshot, ProjectView, SpanKind, SpanView } from "@/lib/factoryApi";
import { isClosedProject, isIntegrationProject } from "../board/boardModel";
import { formatElapsed } from "../factoryFormat";
import { needsAttention, projectRowFacts } from "../projects/projectsModel";

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
  env: "ci",
  preview: "ci",
  demo: "ci",
};

/**
 * Env and preview spans are state episodes named by state; only a transition
 * is waiting (a `ready` or `parked` env is not).
 */
const TRANSITIONAL_STATES = new Set(["starting", "deploying", "degraded", "parking"]);

/** The span kinds the heatmap paints (what it asks the server for). */
export const HEATMAP_SPAN_KINDS = Object.keys(SPAN_CELL) as SpanKind[];

/** A span's cell kind, or null when it paints nothing. */
export function spanCellKind(span: Pick<SpanView, "kind" | "name">): CellKind | null {
  const kind = SPAN_CELL[span.kind];
  if (!kind) return null;
  if ((span.kind === "env" || span.kind === "preview") && !TRANSITIONAL_STATES.has(span.name)) {
    return null;
  }
  return kind;
}

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
  /** Something wants a human's eye (flags, failing gates): ranked above quiet rows. */
  attention: boolean;
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

/** Paints `[fromS, toS)` (an instant marks its cell) when it overlaps the window. */
function paintSpan(
  cells: CellKind[],
  startS: number,
  nowS: number,
  fromS: number,
  toS: number,
  kind: CellKind,
) {
  if (toS <= startS || fromS >= nowS + HEATMAP_CELL_S) return;
  paint(cells, startS, fromS, Math.max(toS, fromS + 1), kind);
}

function failingGate(snapshot: FactorySnapshot, project: ProjectView): string | null {
  return projectRowFacts(snapshot, project).gates.find((g) => g.state === "fail")?.label ?? null;
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
  const blocking = blockingDecision(snapshot, project);
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
  const failing = failingGate(snapshot, project);
  if (failing) return { note: `✕ ${failing}`, tone: "fail" };
  if (project.claim_mismatch) return { note: "claim ≠ gates", tone: "warning" };
  return { note: null, tone: "muted" };
}

function blockingDecision(snapshot: FactorySnapshot, project: ProjectView) {
  return snapshot.decisions.find(
    (d) => d.status === "open" && d.blocking && project.open_decision_ids.includes(d.id),
  );
}

/** The projects a heatmap shows: work in flight. */
function rowProjects(snapshot: FactorySnapshot): ProjectView[] {
  return snapshot.projects.filter((p) => !isClosedProject(p) && !isIntegrationProject(p));
}

/**
 * What the snapshot knows now and spans may not cover: a running agent (its
 * tool since it started, else thinking), each agent's last activity, a
 * ready PR or a blocking decision waiting on a human.
 */
function paintSnapshot(
  snapshot: FactorySnapshot,
  grid: Map<string, CellKind[]>,
  startS: number,
  nowS: number,
) {
  for (const member of snapshot.members) {
    const cells = member.project ? grid.get(member.project) : undefined;
    if (!cells) continue;
    const now = member.now;
    if (member.last_activity_at != null) {
      paintSpan(
        cells,
        startS,
        nowS,
        member.last_activity_at,
        member.last_activity_at + 1,
        "thinking",
      );
    }
    if (now?.tool && now.tool_since != null && now.status === "running") {
      paintSpan(cells, startS, nowS, now.tool_since, nowS, "tool");
    } else if (member.running || now?.status === "running") {
      paintSpan(cells, startS, nowS, nowS - 1, nowS, "thinking");
    }
  }
  for (const project of rowProjects(snapshot)) {
    const cells = grid.get(project.slug)!;
    if (project.stage === "ready")
      paintSpan(cells, startS, nowS, project.stage_since, nowS, "human");
    const blocking = blockingDecision(snapshot, project);
    if (blocking) paintSpan(cells, startS, nowS, blocking.created_at, nowS, "human");
  }
}

function emptyGrid(snapshot: FactorySnapshot): Map<string, CellKind[]> {
  return new Map(
    rowProjects(snapshot).map((p) => [p.slug, Array<CellKind>(HEATMAP_CELLS).fill("idle")]),
  );
}

const TONE_RANK: Record<HeatmapRow["tone"], number> = { fail: 2, warning: 1, muted: 0 };

/** Rows ranked: failing, then flagged, then the busiest; quiet ones last. */
function finish(
  snapshot: FactorySnapshot,
  grid: Map<string, CellKind[]>,
  nowS: number,
  limit: number,
): HeatmapRow[] {
  const rows = rowProjects(snapshot).map((project): HeatmapRow => {
    const cells = grid.get(project.slug)!;
    const { note, tone } = projectNote(snapshot, project, nowS);
    return {
      slug: project.slug,
      title: project.title,
      cells,
      busy: cells.filter((c) => c !== "idle").length,
      note,
      tone,
      attention:
        tone !== "muted" ||
        needsAttention(snapshot, project) ||
        failingGate(snapshot, project) != null,
    };
  });
  return rows
    .sort(
      (a, b) =>
        TONE_RANK[b.tone] - TONE_RANK[a.tone] ||
        Number(b.attention) - Number(a.attention) ||
        b.busy - a.busy,
    )
    .slice(0, limit);
}

/**
 * Rows from spans (`started_at`/`ended_at` in ms; an open span runs to now),
 * with agent activity from `events` and the snapshot.
 */
export function heatmapFromSpans(
  snapshot: FactorySnapshot,
  spans: readonly SpanView[],
  nowS: number,
  events: readonly EventView[] = [],
  limit = Infinity,
): HeatmapRow[] {
  const startS = heatmapStartS(nowS);
  const grid = emptyGrid(snapshot);
  for (const span of spans) {
    const kind = spanCellKind(span);
    const cells = span.project_slug ? grid.get(span.project_slug) : undefined;
    if (!kind || !cells) continue;
    const from = Math.floor(span.started_at / 1000);
    const to = span.ended_at != null ? Math.ceil(span.ended_at / 1000) : nowS;
    if (from >= nowS) continue;
    paintSpan(cells, startS, nowS, from, to, kind);
  }
  // Spans cover the waits; events add what agents did.
  for (const event of events) {
    const cells = event.project_slug ? grid.get(event.project_slug) : undefined;
    if (!cells || eventCellKind(event.kind) !== "thinking") continue;
    const at = Math.floor(event.ts / 1000);
    paintSpan(cells, startS, nowS, at, at + 1, "thinking");
  }
  paintSnapshot(snapshot, grid, startS, nowS);
  return finish(snapshot, grid, nowS, limit);
}

/** Rows from events (no spans on this server): each event marks its cell. */
export function heatmapFromEvents(
  snapshot: FactorySnapshot,
  events: readonly EventView[],
  nowS: number,
  limit = Infinity,
): HeatmapRow[] {
  const startS = heatmapStartS(nowS);
  const grid = emptyGrid(snapshot);
  for (const event of events) {
    const kind = eventCellKind(event.kind);
    const cells = event.project_slug ? grid.get(event.project_slug) : undefined;
    const at = Math.floor(event.ts / 1000);
    if (!kind || !cells || at < startS) continue;
    paintSpan(cells, startS, nowS, at, at + 1, kind);
  }
  paintSnapshot(snapshot, grid, startS, nowS);
  return finish(snapshot, grid, nowS, limit);
}
