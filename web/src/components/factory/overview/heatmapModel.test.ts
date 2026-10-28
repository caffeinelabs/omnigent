import { describe, expect, it } from "vitest";
import type { EventView, SpanView } from "@/lib/factoryApi";
import { ROUND3_NOW_S, round3Snapshot, round3Spans } from "../shell/__fixtures__/shellFixtures";
import {
  HEATMAP_CELLS,
  eventCellKind,
  heatmapFromEvents,
  heatmapFromSpans,
  heatmapStartS,
  projectNote,
} from "./heatmapModel";

const NOW = ROUND3_NOW_S;

function span(patch: Partial<SpanView>): SpanView {
  return { ...round3Spans().data[0]!, ...patch };
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
    const snapshot = round3Snapshot();
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
    const snapshot = round3Snapshot();
    const old = (heatmapStartS(NOW) - 600) * 1000;
    const rows = heatmapFromSpans(
      snapshot,
      [span({ kind: "tool", project_slug: "ux-badge", started_at: old, ended_at: old + 1000 })],
      NOW,
    );
    expect(rows.find((r) => r.slug === "ux-badge")!.busy).toBe(0);
  });

  it("puts projects that need attention first, then the busiest", () => {
    const rows = heatmapFromSpans(round3Snapshot(), round3Spans().data, NOW);
    const firstQuiet = rows.findIndex((r) => r.tone === "muted");
    expect(rows.slice(firstQuiet).every((r) => r.tone === "muted")).toBe(true);
  });
});

describe("heatmapFromEvents (servers without spans)", () => {
  it("marks each event's cell by its kind", () => {
    const snapshot = round3Snapshot();
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
