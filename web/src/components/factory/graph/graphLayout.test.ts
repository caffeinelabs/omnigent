import { describe, expect, it } from "vitest";
import {
  busGraph12,
  flowGraph,
  orgGraphOf,
  scaleSnapshot,
  workGraph40,
  workGraphRound3,
} from "../room/__fixtures__/roomFixtures";
import {
  COL_GAP,
  LANE_PAD,
  ORPHAN_AFTER_MS,
  TASK_W,
  type BusNodeData,
  type LaneNodeData,
  type RouteEdgeData,
  type TaskNodeData,
  busBadges,
  busWidth,
  layoutBus,
  layoutFlow,
  layoutOrg,
  layoutWork,
  workWarnings,
} from "./graphLayout";

describe("work graph layout (40 tasks)", () => {
  const graph = workGraph40();
  const layout = layoutWork(graph);
  const tasks = layout.nodes.filter((n) => n.type === "task");
  const lanes = layout.nodes.filter((n) => n.type === "lane");

  it("draws every task in its project's lane, waves as columns", () => {
    expect(tasks).toHaveLength(40);
    expect(lanes.map((n) => n.id)).toEqual([
      "lane:db-version",
      "lane:api-version",
      "lane:ux-badge",
      "lane:settings-row",
    ]);
    expect(layout.columns).toBe(5);
    for (const node of tasks) {
      const { task } = node.data as TaskNodeData;
      expect(node.position.x).toBe(LANE_PAD + (graph.layers[task.id]! - 1) * (TASK_W + COL_GAP));
      const lane = lanes.find((l) => l.id === `lane:${task.project_slug}`)!;
      expect(node.position.y).toBeGreaterThan(lane.position.y);
      expect(node.position.y).toBeLessThan(lane.position.y + Number(lane.style!.height));
    }
  });

  it("never overlaps two tasks", () => {
    const seen = new Set(tasks.map((n) => `${n.position.x},${n.position.y}`));
    expect(seen.size).toBe(40);
  });

  it("draws the critical path bold, serial pairs dashed and project dependencies between lanes", () => {
    const bold = layout.edges.filter((e) => e.data?.critical);
    expect(bold.length).toBeGreaterThan(0);
    expect(bold.every((e) => Number(e.style?.strokeWidth) > 2)).toBe(true);
    const serial = layout.edges.filter((e) => e.data?.kind === "serial");
    expect(serial).toHaveLength(1);
    expect(serial[0]!.style?.strokeDasharray).toBe("4 4");
    const projects = layout.edges.filter((e) => e.data?.kind === "depends_on");
    expect(projects.map((e) => [e.source, e.target, e.animated])).toEqual([
      ["lane:api-version", "lane:settings-row", false],
      ["lane:db-version", "lane:settings-row", true],
    ]);
    expect(layout.critical.size).toBe(20);
  });

  it("routes project dependencies in a gutter right of the lanes, one track each", () => {
    const projects = layout.edges.filter((e) => e.data?.kind === "depends_on");
    const laneRight = Number(lanes[0]!.style!.width);
    const tracks = projects.map((e) => (e.data as RouteEdgeData).points[1]!.x);
    expect(new Set(tracks).size).toBe(projects.length);
    for (const edge of projects) {
      const points = (edge.data as RouteEdgeData).points;
      // Out of the lane's right edge, down the gutter, back into the other lane.
      expect(points[0]!.x).toBe(laneRight);
      expect(points[1]!.x).toBeGreaterThan(laneRight);
      expect(points[points.length - 1]!.x).toBe(laneRight);
    }
    expect(layout.width).toBeGreaterThan(laneRight);
    const settings = lanes.find((l) => l.id === "lane:settings-row")!.data as LaneNodeData;
    expect(settings.after).toEqual([
      { slug: "api-version", met: true },
      { slug: "db-version", met: false },
    ]);
  });

  it("heads the columns W1…Wn above the lanes", () => {
    const heads = layout.nodes.filter((n) => n.type === "waveHead");
    expect(heads.map((n) => (n.data as { label: string }).label)).toEqual([
      "W1",
      "W2",
      "W3",
      "W4",
      "W5",
    ]);
    expect(heads.every((n) => n.position.y < lanes[0]!.position.y)).toBe(true);
    for (const node of tasks) {
      const head = heads[(graph.layers[(node.data as TaskNodeData).task.id] ?? 1) - 1]!;
      expect(node.position.x).toBe(head.position.x);
    }
  });

  it("rings ready tasks, chips decision blocks and flags overlaps", () => {
    const data = (ref: string) =>
      tasks.find((n) => (n.data as TaskNodeData).task.ref === ref)!.data as TaskNodeData;
    expect(data("T-5").ready).toBe(true);
    expect(data("T-8").waitsOn).toEqual([{ ref: "D-2", assignee: "mia@caffeine.ai" }]);
    // Warnings name tasks as the canvas does.
    expect(data("T-6").warnings[0]).toMatch(/^T-6 and T-7 both touch/);
    expect(workWarnings(graph)[0]!.message).toMatch(/^T-6 and T-7 both touch/);
    expect(data("T-1").ready).toBe(false);
  });

  it("hides finished tasks on request, keeping the columns", () => {
    const open = layoutWork(graph, { showDone: false });
    expect(open.nodes.filter((n) => n.type === "task")).toHaveLength(32);
    expect(open.columns).toBe(5);
    expect(
      open.edges.every(
        (e) => !e.source.startsWith("task") || open.nodes.some((n) => n.id === e.source),
      ),
    ).toBe(true);
  });

  it("scopes to one project and marks the selected task", () => {
    const one = layoutWork(graph, { project: "api-version", selectedTask: "T-12" });
    expect(one.nodes.filter((n) => n.type === "lane")).toHaveLength(1);
    expect(one.nodes.filter((n) => n.type === "task")).toHaveLength(10);
    const selected = one.nodes.find((n) => (n.data as TaskNodeData).selected)!;
    expect((selected.data as TaskNodeData).task.ref).toBe("T-12");
  });

  it("lays out the round-3 tasks with the blocked route on the critical path", () => {
    const small = layoutWork(workGraphRound3());
    const route = small.nodes.find((n) => (n.data as TaskNodeData)?.task?.ref === "T-4")!;
    expect((route.data as TaskNodeData).critical).toBe(true);
    expect((route.data as TaskNodeData).waitsOn.map((w) => w.ref)).toEqual(["D-31"]);
  });
});

describe("bus graph layout (12 nodes)", () => {
  const graph = busGraph12();
  const layout = layoutBus(graph);

  it("places people left, then leads, project leads and sub-agents", () => {
    expect(layout.nodes).toHaveLength(12);
    const column = (title: string) =>
      layout.nodes.find((n) => (n.data as BusNodeData).node.title === title)!.position.x;
    expect(column("luka@caffeine.ai")).toBe(0);
    expect(column("server")).toBe(0);
    expect(column("Atlas")).toBeLessThan(column("PL · db-version"));
    expect(column("PL · db-version")).toBeLessThan(column("ic:route-r2"));
  });

  it("widens edges by traffic, dashes bounced or unanswered ones, reds human waits", () => {
    const edge = (from: string, to: string) =>
      layout.edges.find((e) => e.id === `message:${from}>${to}`)!;
    const [atlas, sentinel, plDb, , plUx, ic] = graph.nodes.slice(3).map((n) => n.id);
    expect(Number(edge(atlas!, sentinel!).style?.strokeWidth)).toBeGreaterThan(
      Number(edge(plDb!, ic!).style?.strokeWidth),
    );
    expect(edge(ic!, plDb!).style?.strokeDasharray).toBe("5 4");
    expect(edge(plUx!, sentinel!).style?.strokeDasharray).toBe("5 4");
    const waits = layout.edges.filter((e) => e.data?.kind === "waits_on");
    expect(waits.map((e) => e.label)).toEqual(["D-2", "D-31"]);
    expect(waits.every((e) => e.className === "factory-edge-human")).toBe(true);
  });

  it("flags ping-pong pairs and orphan requests", () => {
    const badges = busBadges(graph.edges, graph.until_ms);
    // Atlas ⇄ Sentinel 23, PL · db-version ⇄ ic 13, Atlas ⇄ PL · db-version 9 (> 6 each).
    expect([...badges.pingPong.values()].sort((a, b) => b - a)).toEqual([23, 13, 9]);
    expect(badges.orphans.size).toBe(1);
    const atlas = layout.nodes.find((n) => (n.data as BusNodeData).node.title === "Atlas")!;
    expect((atlas.data as BusNodeData).badges).toEqual(["ping-pong"]);
    const fresh = busBadges(graph.edges, graph.until_ms - ORPHAN_AFTER_MS);
    expect(fresh.orphans.size).toBe(0);
  });

  it("hides spawn and supervise edges unless asked", () => {
    expect(layout.edges.some((e) => e.data?.kind === "spawn")).toBe(false);
    expect(
      layoutBus(graph, { showStructure: true }).edges.some((e) => e.data?.kind === "spawn"),
    ).toBe(true);
  });

  it("scales stroke width with the log of the count", () => {
    expect(busWidth(1)).toBe(1);
    expect(busWidth(8)).toBeCloseTo(4.75);
    expect(busWidth(10_000)).toBe(7);
  });
});

describe("flow and org layouts", () => {
  it("lines the pipeline up and curves loop back-edges under it", () => {
    const layout = layoutFlow(flowGraph());
    expect(layout.nodes.map((n) => n.id)).toEqual([
      "plan",
      "build",
      "review",
      "verify",
      "demo",
      "ready",
      "merged",
    ]);
    expect(new Set(layout.nodes.map((n) => n.position.y)).size).toBe(1);
    const back = layout.edges.filter((e) => e.id.startsWith("back:"));
    // The shortest loop on the top level, each on its own level.
    expect(back.map((e) => e.label)).toEqual(["CI fail ×2", "changes ×3", "FAIL(env) ×1"]);
    const points = back.map((e) => (e.data as RouteEdgeData).points);
    const depths = points.map((p) => p[1]!.y);
    expect(new Set(depths).size).toBe(3);
    expect(depths).toEqual([...depths].sort((a, b) => a - b));
    // Into Build, deeper loops land further left: no vertical crosses a run.
    const into = points.map((p) => p[p.length - 1]!.x);
    expect(into[2]!).toBeLessThan(into[1]!);
    expect(into[1]!).toBeLessThan(into[0]!);
    // Labels start right of their own loop's way down.
    for (const edge of back) {
      const data = edge.data as RouteEdgeData;
      expect(data.labelAt!.x).toBeGreaterThan(data.points[0]!.x);
      expect(data.labelAt!.y).toBe(data.points[1]!.y);
    }
  });

  it("draws every member at any depth, one column per project", () => {
    const graph = orgGraphOf(scaleSnapshot(60));
    const layout = layoutOrg(graph);
    expect(layout.nodes.filter((n) => n.type === "org")).toHaveLength(60);
    expect(layout.nodes.filter((n) => n.type === "orgGroup")).toHaveLength(graph.groups.length);
    expect(layout.depth).toBeGreaterThanOrEqual(1);
    const deep = { ...graph, nodes: graph.nodes.map((n) => ({ ...n })) };
    // A chain eight levels deep: no depth limit.
    const chain = deep.nodes.filter((n) => !n.is_top_level).slice(0, 8);
    chain.forEach((n, i) => {
      if (i > 0) n.parent = chain[i - 1]!.id;
    });
    expect(layoutOrg(deep).depth).toBeGreaterThanOrEqual(8);
  });
});
