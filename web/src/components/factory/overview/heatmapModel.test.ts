import { describe, expect, it } from "vitest";
import type { EventView, FactorySnapshot, SpanView } from "@/lib/factoryApi";
import {
  ROUND3_NOW_S,
  round3Snapshot,
  round3Spans,
  scaleSnapshot,
  serverSpan,
} from "../shell/__fixtures__/shellFixtures";
import {
  HEATMAP_CELLS,
  eventCellKind,
  heatmapFromEvents,
  heatmapFromSpans,
  heatmapStartS,
  projectNote,
  spanCellKind,
} from "./heatmapModel";

const NOW = ROUND3_NOW_S;

function span(patch: Partial<SpanView>): SpanView {
  return { ...round3Spans().data[0]!, ...patch };
}

/** The round-3 snapshot with nobody working and nothing waiting (only spans paint). */
function quietSnapshot(): FactorySnapshot {
  const snapshot = round3Snapshot();
  snapshot.members = snapshot.members.map((m) => ({
    ...m,
    running: false,
    now: null,
    last_activity_at: null,
  }));
  snapshot.decisions = snapshot.decisions.map((d) => ({ ...d, blocking: false }));
  snapshot.projects = snapshot.projects.map((p) =>
    p.stage === "ready" ? { ...p, stage: "review" } : p,
  );
  return snapshot;
}

function eventAt(kind: string, s: number, project = "ux-badge"): EventView {
  return {
    id: `${kind}-${s}`,
    ts: (heatmapStartS(NOW) + s) * 1000,
    kind,
    severity: "info",
    project_slug: project,
    from_id: null,
    to_id: null,
    from_title: null,
    to_title: null,
    subject: null,
    summary: kind,
    data: null,
  };
}

describe("heatmapFromSpans", () => {
  it("draws one row per project in flight, 30 cells of 2 min, the env and done ones left out", () => {
    const snapshot = round3Snapshot();
    const rows = heatmapFromSpans(snapshot, round3Spans().data, NOW);
    expect(rows.every((r) => r.cells.length === HEATMAP_CELLS)).toBe(true);
    const slugs = rows.map((r) => r.slug);
    expect(slugs).not.toContain("base-env");
    expect(slugs).not.toContain("docs-factory-envs");
    expect(slugs).toContain("db-version");
  });

  it("paints a running tool over thinking, waits and CI in the same cell", () => {
    const snapshot = quietSnapshot();
    const startS = heatmapStartS(NOW);
    const at = (s: number) => (startS + s) * 1000;
    const rows = heatmapFromSpans(
      snapshot,
      [
        span({ kind: "wait", project_slug: "db-version", started_at: at(0), ended_at: null }),
        span({ kind: "gate", project_slug: "db-version", started_at: at(130), ended_at: at(200) }),
        span({ kind: "turn", project_slug: "db-version", started_at: at(250), ended_at: at(300) }),
        span({ kind: "tool", project_slug: "db-version", started_at: at(260), ended_at: at(270) }),
        span({ kind: "pr", project_slug: "db-version", started_at: at(0), ended_at: null }),
      ],
      NOW,
    );
    const db = rows.find((r) => r.slug === "db-version")!;
    expect(db.cells[0]).toBe("human");
    expect(db.cells[1]).toBe("human"); // the wait outranks CI
    expect(db.cells[2]).toBe("tool");
    expect(db.cells.at(-1)).toBe("human"); // the open wait runs to now
  });

  it("ignores spans outside the window and container spans", () => {
    const snapshot = quietSnapshot();
    const old = (heatmapStartS(NOW) - 600) * 1000;
    const rows = heatmapFromSpans(
      snapshot,
      [span({ kind: "tool", project_slug: "ux-badge", started_at: old, ended_at: old + 1000 })],
      NOW,
    );
    expect(rows.find((r) => r.slug === "ux-badge")!.busy).toBe(0);
  });

  it("does not paint a ready env, a parked preview or the open PR round as waiting on CI", () => {
    // What the recorder writes: env and preview state episodes named by state,
    // open for hours, and the current head's round open while the PR is.
    const spans = [
      serverSpan("env", "ready", "ux-badge", 180, null),
      serverSpan("preview", "parked", "ux-badge", 300, null),
      serverSpan("pr_round", "0b1c2d3", "ux-badge", 240, null),
      serverSpan("pr", "!9653", "ux-badge", 240, null),
      serverSpan("task", "T-1 badge", "ux-badge", 240, null),
    ];
    const ux = heatmapFromSpans(quietSnapshot(), spans, NOW).find((r) => r.slug === "ux-badge")!;
    expect(ux.cells.every((c) => c === "idle")).toBe(true);
  });

  it("paints env and preview transitions and open gates as waiting on CI", () => {
    const spans = [
      serverSpan("env", "starting", "ux-badge", 60, 56),
      serverSpan("preview", "deploying", "ux-badge", 30, 28),
      serverSpan("gate", "checks", "ux-badge", 6, null),
    ];
    const ux = heatmapFromSpans(quietSnapshot(), spans, NOW).find((r) => r.slug === "ux-badge")!;
    expect(ux.cells.filter((c) => c === "ci").length).toBeGreaterThanOrEqual(5);
    expect(ux.cells.at(-1)).toBe("ci");
    expect(ux.cells[20]).toBe("idle");
    expect(spanCellKind({ kind: "env", name: "degraded" })).toBe("ci");
    expect(spanCellKind({ kind: "preview", name: "parking" })).toBe("ci");
    expect(spanCellKind({ kind: "env", name: "parked" })).toBeNull();
  });

  it("takes thinking and tools from events and the members' now (no turn or tool spans)", () => {
    const snapshot = quietSnapshot();
    const db = snapshot.members.find((m) => m.title === "ic:route-r2")!;
    db.running = true;
    db.now = {
      session_id: db.session_id,
      status: "running",
      turn_n: 4,
      tool: "Bash",
      tool_arg: "pnpm test",
      tool_since: NOW - 5 * 60,
      last_event_at: NOW,
      model: null,
      cost_usd: null,
      usd_per_h: null,
      ctx_pct: null,
      flags: [],
    };
    const rows = heatmapFromSpans(snapshot, round3Spans().data, NOW, [
      eventAt("message.delivered", 10),
      eventAt("gate.changed", 130),
    ]);
    const ux = rows.find((r) => r.slug === "ux-badge")!;
    expect(ux.cells[0]).toBe("thinking");
    expect(ux.cells[1]).toBe("idle"); // gate events are the spans' to paint
    const dbRow = rows.find((r) => r.slug === "db-version")!;
    expect(dbRow.cells.slice(-3)).toEqual(["tool", "tool", "tool"]);
  });

  it("fills a ready PR's wait from the snapshot when no span covers it", () => {
    const api = heatmapFromSpans(round3Snapshot(), [], NOW).find((r) => r.slug === "api-version")!;
    expect(api.note).toMatch(/^waiting for human /);
    expect(api.cells.at(-1)).toBe("human");
    // Ready 12m 40s ago: the last 7 cells wait (some show the agents' last turns).
    expect(api.cells.slice(-7).every((c) => c === "human" || c === "thinking")).toBe(true);
    expect(api.cells.slice(0, -7).includes("human")).toBe(false);
  });

  it("ranks failing and flagged projects above quiet ones", () => {
    const rows = heatmapFromSpans(scaleSnapshot(12, 60), [], NOW);
    expect(rows).toHaveLength(10); // 12 less the closed docs project and base-env
    const firstQuiet = rows.findIndex((r) => !r.attention);
    expect(firstQuiet).toBeGreaterThan(0);
    expect(rows.slice(firstQuiet).every((r) => !r.attention)).toBe(true);
    // The Overview shows 8: the CI-failed claim ≠ gates clones make the cut.
    const top8 = rows.slice(0, 8).map((r) => r.slug);
    expect(top8).toEqual(expect.arrayContaining(["auth-8", "audit-10", "cache-warm"]));
    expect(rows.find((r) => r.slug === "audit-10")).toMatchObject({ tone: "fail" });
    expect(rows.slice(8).every((r) => !r.attention)).toBe(true);
  });

  it("puts projects that need attention first, then the busiest", () => {
    const rows = heatmapFromSpans(round3Snapshot(), round3Spans().data, NOW);
    const firstQuiet = rows.findIndex((r) => r.tone === "muted");
    expect(rows.slice(firstQuiet).every((r) => r.tone === "muted")).toBe(true);
  });
});

describe("heatmapFromEvents (servers without spans)", () => {
  it("marks each event's cell by its kind", () => {
    const snapshot = quietSnapshot();
    const startS = heatmapStartS(NOW);
    const event = (kind: string, s: number): EventView => ({
      id: kind,
      ts: (startS + s) * 1000,
      kind,
      severity: "info",
      project_slug: "ux-badge",
      from_id: null,
      to_id: null,
      from_title: null,
      to_title: null,
      subject: null,
      summary: kind,
      data: null,
    });
    const rows = heatmapFromEvents(
      snapshot,
      [
        event("decision.opened", 10),
        event("gate.changed", 130),
        event("dispatch", 250),
        event("heartbeat.sent", 400),
      ],
      NOW,
    );
    const ux = rows.find((r) => r.slug === "ux-badge")!;
    expect(ux.cells.slice(0, 4)).toEqual(["human", "ci", "thinking", "idle"]);
  });

  it("maps kinds to cells", () => {
    expect(eventCellKind("heartbeat.skipped")).toBeNull();
    expect(eventCellKind("needs_human")).toBe("human");
    expect(eventCellKind("preview.deployed")).toBe("ci");
    expect(eventCellKind("message.delivered")).toBe("thinking");
  });
});

describe("projectNote", () => {
  it("says what a project waits on", () => {
    const snapshot = round3Snapshot();
    const db = snapshot.projects.find((p) => p.slug === "db-version")!;
    expect(projectNote(snapshot, db, NOW)).toEqual({
      note: expect.stringMatching(/^waiting on D-2 · /),
      tone: "warning",
    });
    const api = snapshot.projects.find((p) => p.slug === "api-version")!;
    expect(projectNote(snapshot, api, NOW).note).toMatch(/^waiting for human /);
  });
});
