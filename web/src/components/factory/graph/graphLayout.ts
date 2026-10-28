// Layouts of the four factory graphs for @xyflow/react, as pure functions of
// the graph API's answers (tests pin them on fixtures):
//
// - work: one lane per project, its tasks in columns by layer (W1, W2, …),
//   dependency and serialization edges, the critical path drawn bold, and
//   project `depends_on` edges between lanes;
// - bus: humans on the left, then leads, project leads and their sub-agents,
//   message edges as wide as their traffic, dashed when bounced or
//   unanswered, red when an agent waits on a human; orphan and ping-pong badges;
// - flow: the PR pipeline in a row with counts, p50 dwell and dollars per
//   stage, loop back-edges curving under it with their counters;
// - org: every member and sub-agent at any depth, one column per project,
//   indented by depth.

import type { Edge, Node } from "@xyflow/react";
import type { TaskView } from "@/lib/factoryApi";
import type {
  BusEdge,
  BusGraphResponse,
  BusNode,
  FlowGraphResponse,
  FlowNode,
  OrgGraphResponse,
  OrgNode,
  WorkGraphProject,
  WorkGraphResponse,
} from "@/lib/factoryRoomApi";
import { taskFinished } from "@/lib/factoryRoomApi";

// ── Work ───────────────────────────────────────────────────────

export const TASK_W = 196;
export const TASK_H = 58;
export const COL_GAP = 52;
export const ROW_GAP = 10;
export const LANE_PAD = 14;
export const LANE_HEAD = 34;
export const LANE_GAP = 22;
export const LANE_MIN_W = 300;

export interface TaskNodeData extends Record<string, unknown> {
  task: TaskView;
  critical: boolean;
  ready: boolean;
  /** Unmet decision deps, e.g. `D-7`, with whom they wait on. */
  waitsOn: { ref: string; assignee: string | null }[];
  /** Warnings naming this task (scope overlap, hub file). */
  warnings: string[];
  selected: boolean;
}

export interface LaneNodeData extends Record<string, unknown> {
  project: WorkGraphProject;
  columns: number;
}

export interface WorkLayout {
  nodes: Node[];
  edges: Edge[];
  /** The widest layer drawn (column headers W1…Wn). */
  columns: number;
  width: number;
  height: number;
  /** Task ids on the critical path, per the server. */
  critical: Set<string>;
}

export interface WorkLayoutOptions {
  /** Draw done and cancelled tasks (they keep their columns either way). */
  showDone?: boolean;
  /** Only these projects. */
  project?: string | null;
  selectedTask?: string | null;
}

function layerOf(graph: WorkGraphResponse, task: TaskView): number {
  return Math.max(1, graph.layers[task.id] ?? task.wave ?? 1);
}

/** Task ids on each project's critical path (the server's `critical_path` keys). */
function criticalIds(graph: WorkGraphResponse): Set<string> {
  const out = new Set<string>();
  for (const project of graph.projects) {
    const keys = new Set(project.critical_path);
    for (const task of graph.tasks) {
      if (task.project_slug === project.slug && keys.has(task.key)) out.add(task.id);
    }
  }
  for (const task of graph.tasks) if (task.critical) out.add(task.id);
  return out;
}

/** Consecutive pairs of a project's critical path, as task-id pairs. */
function criticalPairs(graph: WorkGraphResponse): Set<string> {
  const pairs = new Set<string>();
  for (const project of graph.projects) {
    const ids = project.critical_path
      .map((key) => graph.tasks.find((t) => t.project_slug === project.slug && t.key === key)?.id)
      .filter((id): id is string => Boolean(id));
    for (let i = 1; i < ids.length; i += 1) pairs.add(`${ids[i - 1]}>${ids[i]}`);
  }
  return pairs;
}

/** Warnings per task id (overlap and hub-file warnings name task keys). */
function warningsByTask(graph: WorkGraphResponse): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const warning of graph.warnings) {
    for (const key of warning.tasks ?? []) {
      const task = graph.tasks.find(
        (t) => t.key === key && (!warning.project || t.project_slug === warning.project),
      );
      if (!task) continue;
      out.set(task.id, [...(out.get(task.id) ?? []), warning.message]);
    }
  }
  return out;
}

export function layoutWork(graph: WorkGraphResponse, options: WorkLayoutOptions = {}): WorkLayout {
  const showDone = options.showDone ?? true;
  const projects = graph.projects.filter((p) => !options.project || p.slug === options.project);
  const critical = criticalIds(graph);
  const pairs = criticalPairs(graph);
  const warnings = warningsByTask(graph);
  const live = graph.tasks.filter((t) => t.state !== "cancelled");
  const columns = Math.max(1, ...live.map((t) => layerOf(graph, t)));
  const laneWidth = Math.max(LANE_MIN_W, LANE_PAD * 2 + columns * TASK_W + (columns - 1) * COL_GAP);
  const nodes: Node[] = [];
  const shown = new Set<string>();
  let y = 0;
  for (const project of projects) {
    const tasks = live.filter(
      (t) => t.project_slug === project.slug && (showDone || !taskFinished(t.state)),
    );
    const byLayer = new Map<number, TaskView[]>();
    for (const task of tasks) {
      const layer = layerOf(graph, task);
      byLayer.set(layer, [...(byLayer.get(layer) ?? []), task]);
    }
    const rows = Math.max(1, ...[...byLayer.values()].map((list) => list.length));
    const height = LANE_HEAD + rows * TASK_H + (rows - 1) * ROW_GAP + LANE_PAD;
    nodes.push({
      id: `lane:${project.slug}`,
      type: "lane",
      position: { x: 0, y },
      data: { project, columns } satisfies LaneNodeData,
      style: { width: laneWidth, height },
      draggable: false,
      selectable: false,
      zIndex: 0,
    });
    const ready = new Set(project.ready);
    for (const [layer, list] of byLayer) {
      list
        .sort((a, b) => a.priority - b.priority || a.number - b.number)
        .forEach((task, row) => {
          shown.add(task.id);
          const waitsOn = graph.external_edges
            .filter((e) => e.to === task.id && e.kind === "decision" && !e.met)
            .map((e) => ({ ref: e.from.replace(/^decision:/, ""), assignee: e.assignee ?? null }));
          nodes.push({
            id: task.id,
            type: "task",
            position: {
              x: LANE_PAD + (layer - 1) * (TASK_W + COL_GAP),
              y: y + LANE_HEAD + row * (TASK_H + ROW_GAP),
            },
            data: {
              task,
              critical: critical.has(task.id),
              ready: ready.has(task.key),
              waitsOn,
              warnings: warnings.get(task.id) ?? [],
              selected: options.selectedTask === task.id || options.selectedTask === task.ref,
            } satisfies TaskNodeData,
            style: { width: TASK_W, height: TASK_H },
            zIndex: 2,
          });
        });
    }
    y += height + LANE_GAP;
  }
  const edges: Edge[] = [];
  for (const edge of graph.edges) {
    if (!shown.has(edge.from) || !shown.has(edge.to)) continue;
    const bold = pairs.has(`${edge.from}>${edge.to}`);
    edges.push({
      id: `${edge.kind}:${edge.from}>${edge.to}`,
      source: edge.from,
      target: edge.to,
      type: "smoothstep",
      animated: false,
      data: { kind: edge.kind, critical: bold, met: edge.met },
      className: bold
        ? "factory-edge-critical"
        : edge.met
          ? "factory-edge-met"
          : "factory-edge-open",
      style: {
        strokeWidth: bold ? 2.75 : 1.25,
        strokeDasharray: edge.kind === "serial" ? "4 4" : undefined,
      },
      label: edge.kind === "serial" ? "serial" : undefined,
      zIndex: 1,
    });
  }
  const laneIds = new Set(projects.map((p) => p.slug));
  for (const edge of graph.project_edges) {
    if (!laneIds.has(edge.from) || !laneIds.has(edge.to)) continue;
    edges.push({
      id: `project:${edge.from}>${edge.to}`,
      source: `lane:${edge.from}`,
      target: `lane:${edge.to}`,
      sourceHandle: "bottom",
      targetHandle: "top",
      type: "smoothstep",
      animated: !edge.met,
      data: { kind: "depends_on", met: edge.met },
      label: edge.met ? "depends on ✓" : "depends on",
      className: "factory-edge-project",
      style: { strokeWidth: 1.5, strokeDasharray: "6 4" },
      zIndex: 1,
    });
  }
  return { nodes, edges, columns, width: laneWidth, height: Math.max(0, y - LANE_GAP), critical };
}

// ── Bus ────────────────────────────────────────────────────────

export const BUS_W = 168;
export const BUS_H = 44;
const BUS_COL = 300;
const BUS_ROW = 88;

export const PING_PONG_MIN = 6;
export const ORPHAN_AFTER_MS = 2 * 3600 * 1000;

export interface BusNodeData extends Record<string, unknown> {
  node: BusNode;
  /** Messages it sent and received in the window. */
  sent: number;
  received: number;
  badges: string[];
}

export interface BusBadges {
  /** `a|b` (sorted ids) → messages both ways. */
  pingPong: Map<string, number>;
  /** Edge ids with unanswered requests older than {@link ORPHAN_AFTER_MS}. */
  orphans: Set<string>;
}

function edgeId(edge: BusEdge): string {
  return `${edge.kind}:${edge.from}>${edge.to}`;
}

/** Ping-pong pairs (more than 6 messages both ways) and orphan requests. */
export function busBadges(edges: readonly BusEdge[], nowMs: number): BusBadges {
  const pingPong = new Map<string, number>();
  const orphans = new Set<string>();
  const messages = edges.filter(
    (e): e is Extract<BusEdge, { kind: "message" }> => e.kind === "message",
  );
  for (const edge of messages) {
    const back = messages.find((e) => e.from === edge.to && e.to === edge.from);
    if (back && edge.from < edge.to) {
      const total = edge.count + back.count;
      if (total > PING_PONG_MIN) pingPong.set(`${edge.from}|${edge.to}`, total);
    }
    if (edge.unanswered > 0 && nowMs - edge.last_at >= ORPHAN_AFTER_MS) orphans.add(edgeId(edge));
  }
  return { pingPong, orphans };
}

/** An edge's stroke for its traffic: 1 px for one message, up to 7 px. */
export function busWidth(count: number): number {
  return Math.min(7, 1 + Math.log2(Math.max(1, count)) * 1.25);
}

function busColumn(node: BusNode): number {
  if (node.kind === "human") return 0;
  if (node.kind === "server") return 0;
  if (node.role === "lead") return 1;
  if (node.role === "project_lead") return 2;
  return 3;
}

/**
 * Which sides an edge leaves and enters by, from where its ends sit: right
 * to left going right, left to right going left, bottom to top within a
 * column. Outgoing and incoming handles sit at different heights, so the
 * two directions between a pair run side by side.
 */
export function busHandles(
  from: { x: number; y: number } | undefined,
  to: { x: number; y: number } | undefined,
): { sourceHandle: string; targetHandle: string } {
  if (!from || !to || from.x < to.x) return { sourceHandle: "r-out", targetHandle: "l-in" };
  if (from.x > to.x) return { sourceHandle: "l-out", targetHandle: "r-in" };
  return from.y <= to.y
    ? { sourceHandle: "b-out", targetHandle: "t-in" }
    : { sourceHandle: "t-out", targetHandle: "b-in" };
}

export interface BusLayout {
  nodes: Node[];
  edges: Edge[];
  badges: BusBadges;
}

export function layoutBus(
  graph: BusGraphResponse,
  options: { nowMs?: number; showStructure?: boolean } = {},
): BusLayout {
  const nowMs = options.nowMs ?? graph.until_ms;
  const badges = busBadges(graph.edges, nowMs);
  const counts = new Map<string, { sent: number; received: number }>();
  for (const edge of graph.edges) {
    if (edge.kind !== "message") continue;
    const from = counts.get(edge.from) ?? { sent: 0, received: 0 };
    from.sent += edge.count;
    counts.set(edge.from, from);
    const to = counts.get(edge.to) ?? { sent: 0, received: 0 };
    to.received += edge.count;
    counts.set(edge.to, to);
  }
  // Columns: people and the server, leads, project leads, their sub-agents (by project).
  const columns: BusNode[][] = [[], [], [], []];
  for (const node of graph.nodes) columns[busColumn(node)]!.push(node);
  const projectOrder = (n: BusNode) => n.project ?? "";
  columns[2]!.sort((a, b) => projectOrder(a).localeCompare(projectOrder(b)));
  columns[3]!.sort(
    (a, b) => projectOrder(a).localeCompare(projectOrder(b)) || a.title.localeCompare(b.title),
  );
  columns[0]!.sort((a, b) => (a.kind === "server" ? 1 : 0) - (b.kind === "server" ? 1 : 0));
  const pingNodes = new Set([...badges.pingPong.keys()].flatMap((k) => k.split("|")));
  const nodes: Node[] = [];
  const tallest = Math.max(...columns.map((c) => c.length), 1);
  columns.forEach((column, x) => {
    const offset = ((tallest - column.length) * BUS_ROW) / 2;
    column.forEach((node, row) => {
      const traffic = counts.get(node.id) ?? { sent: 0, received: 0 };
      const nodeBadges: string[] = [];
      if (pingNodes.has(node.id)) nodeBadges.push("ping-pong");
      nodes.push({
        id: node.id,
        type: "bus",
        position: { x: x * BUS_COL, y: offset + row * BUS_ROW },
        data: { node, ...traffic, badges: nodeBadges } satisfies BusNodeData,
        style: { width: BUS_W, height: BUS_H },
      });
    });
  });
  const at = new Map(nodes.map((n) => [n.id, n.position]));
  const edges: Edge[] = [];
  for (const edge of graph.edges) {
    const id = edgeId(edge);
    if ((edge.kind === "spawn" || edge.kind === "supervise") && !options.showStructure) continue;
    const handles = busHandles(at.get(edge.from), at.get(edge.to));
    if (edge.kind === "message") {
      const bounced = edge.bounced > 0;
      const orphan = badges.orphans.has(id);
      const unanswered = edge.unanswered > 0;
      const pair = [edge.from, edge.to].sort().join("|");
      const label = [`${edge.count}`, orphan ? "orphan" : null].filter(Boolean).join(" · ");
      edges.push({
        id,
        source: edge.from,
        target: edge.to,
        ...handles,
        type: "default",
        label,
        data: { kind: "message", orphan, bounced },
        className:
          orphan || badges.pingPong.has(pair) ? "factory-edge-warn" : "factory-edge-message",
        style: {
          strokeWidth: busWidth(edge.count),
          strokeDasharray: bounced || unanswered ? "5 4" : undefined,
        },
      });
    } else if (edge.kind === "waits_on") {
      edges.push({
        id,
        source: edge.from,
        target: edge.to,
        ...handles,
        type: "default",
        label: edge.label ?? "waits",
        data: { kind: "waits_on" },
        className: "factory-edge-human",
        style: { strokeWidth: 2 },
        animated: true,
      });
    } else if (edge.kind === "alert") {
      edges.push({
        id,
        source: edge.from,
        target: edge.to,
        ...handles,
        type: "default",
        label: `alert ×${edge.count}`,
        data: { kind: "alert" },
        className: "factory-edge-warn",
        style: { strokeWidth: 1.5, strokeDasharray: "2 3" },
      });
    } else {
      edges.push({
        id,
        source: edge.from,
        target: edge.to,
        ...handles,
        type: "default",
        data: { kind: edge.kind },
        className: "factory-edge-structure",
        style: { strokeWidth: 1 },
      });
    }
  }
  return { nodes, edges, badges };
}

// ── Flow ───────────────────────────────────────────────────────

export const FLOW_W = 132;
export const FLOW_H = 76;
const FLOW_GAP = 64;

export interface FlowNodeData extends Record<string, unknown> {
  node: FlowNode;
}

export function layoutFlow(graph: FlowGraphResponse): { nodes: Node[]; edges: Edge[] } {
  const index = new Map(graph.nodes.map((n, i) => [n.id, i]));
  const nodes: Node[] = graph.nodes.map((node, i) => ({
    id: node.id,
    type: "flow",
    position: { x: i * (FLOW_W + FLOW_GAP), y: 0 },
    data: { node } satisfies FlowNodeData,
    style: { width: FLOW_W, height: FLOW_H },
  }));
  const edges: Edge[] = graph.edges.map((edge) => ({
    id: `fwd:${edge.from}>${edge.to}`,
    source: edge.from,
    target: edge.to,
    sourceHandle: "right",
    targetHandle: "left",
    type: "straight",
    className: "factory-edge-open",
    style: { strokeWidth: 1.5 },
  }));
  for (const back of graph.back_edges) {
    if (!index.has(back.from) || !index.has(back.to)) continue;
    edges.push({
      id: `back:${back.from}>${back.to}:${back.label}`,
      source: back.from,
      target: back.to,
      sourceHandle: "bottom",
      targetHandle: "bottom-in",
      type: "smoothstep",
      label: `${back.label} ×${back.count}`,
      className: "factory-edge-warn",
      style: { strokeWidth: Math.min(4, 1 + back.count * 0.5), strokeDasharray: "5 4" },
      data: { kind: "back", count: back.count },
    });
  }
  return { nodes, edges };
}

// ── Org ────────────────────────────────────────────────────────

export const ORG_W = 210;
export const ORG_H = 40;
const ORG_ROW = 48;
const ORG_INDENT = 18;
const ORG_COL = 250;
const ORG_HEAD = 34;

export interface OrgNodeData extends Record<string, unknown> {
  node: OrgNode;
}

export interface OrgGroupData extends Record<string, unknown> {
  project: string | null;
  health: string;
  count: number;
}

export function layoutOrg(graph: OrgGraphResponse): {
  nodes: Node[];
  edges: Edge[];
  depth: number;
} {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const children = new Map<string, OrgNode[]>();
  for (const node of graph.nodes) {
    const parent = node.is_top_level ? null : node.parent;
    if (parent && byId.has(parent)) children.set(parent, [...(children.get(parent) ?? []), node]);
  }
  const nodes: Node[] = [];
  let maxDepth = 0;
  const groups = [...graph.groups].sort(
    (a, b) =>
      Number(a.project != null) - Number(b.project != null) ||
      (a.project ?? "").localeCompare(b.project ?? ""),
  );
  groups.forEach((group, col) => {
    const members = new Set(group.members);
    const roots = graph.nodes
      .filter((n) => members.has(n.id) && (n.is_top_level || !n.parent || !byId.has(n.parent)))
      .sort(
        (a, b) =>
          Number(b.role === "lead") - Number(a.role === "lead") || a.title.localeCompare(b.title),
      );
    const ordered: { node: OrgNode; depth: number }[] = [];
    const visit = (node: OrgNode, depth: number, seen: Set<string>) => {
      if (seen.has(node.id)) return;
      seen.add(node.id);
      ordered.push({ node, depth });
      maxDepth = Math.max(maxDepth, depth);
      for (const child of (children.get(node.id) ?? []).sort((a, b) =>
        a.title.localeCompare(b.title),
      )) {
        visit(child, depth + 1, seen);
      }
    };
    const seen = new Set<string>();
    for (const root of roots) visit(root, 0, seen);
    const x = col * ORG_COL;
    nodes.push({
      id: `org-group:${group.project ?? "leads"}`,
      type: "orgGroup",
      position: { x, y: 0 },
      data: {
        project: group.project,
        health: group.health,
        count: group.members.length,
      } satisfies OrgGroupData,
      style: { width: ORG_W, height: ORG_HEAD - 6 },
      draggable: false,
      selectable: false,
    });
    ordered.forEach(({ node, depth }, row) => {
      nodes.push({
        id: node.id,
        type: "org",
        position: { x: x + Math.min(depth, 6) * ORG_INDENT, y: ORG_HEAD + row * ORG_ROW },
        data: { node } satisfies OrgNodeData,
        style: { width: ORG_W - Math.min(depth, 6) * ORG_INDENT, height: ORG_H },
      });
    });
  });
  const edges: Edge[] = graph.edges
    .filter((e) => e.kind === "spawn")
    .map((edge) => ({
      id: `spawn:${edge.from}>${edge.to}`,
      source: edge.from,
      target: edge.to,
      sourceHandle: "left-out",
      targetHandle: "left",
      type: "smoothstep",
      className: "factory-edge-structure",
      style: { strokeWidth: 1 },
    }));
  return { nodes, edges, depth: maxDepth };
}
