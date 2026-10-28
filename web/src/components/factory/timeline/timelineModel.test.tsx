import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { SpanView } from "@/lib/factoryApi";
import { IC_ROUTE_R2, PL_DB, PR_9631, timelineSpans } from "../agents/__fixtures__/opsFixtures";
import { ROUND3_NOW_S, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { DEFAULT_SHOW, asOf, buildTimeline, position, ticks, windowStart } from "./timelineModel";
import type { Bar } from "./timelineModel";
import { TimelineView, labelledBars } from "./TimelineView";

const NOW = ROUND3_NOW_S * 1000;
const HOUR = 3_600_000;

function build(
  options: Partial<Parameters<typeof buildTimeline>[2]> = {},
  spans: SpanView[] = timelineSpans(),
) {
  return buildTimeline(spans, round3Snapshot(), {
    from: NOW - 3 * HOUR,
    to: NOW,
    show: DEFAULT_SHOW,
    nowS: ROUND3_NOW_S,
    ...options,
  });
}

function laneOf(groups: ReturnType<typeof build>, key: string) {
  for (const group of groups) {
    const found = group.lanes.find((l) => l.key === key);
    if (found) return { group, lane: found };
  }
  return null;
}

describe("buildTimeline from span fixtures", () => {
  it("draws the T-4 implement loop with attempts r0..r2 and the exhausted marker", () => {
    const found = laneOf(build(), "loop:814ca975587e5811a185af63317e48c8");
    expect(found?.group.label).toBe("db-version");
    expect(found?.lane.label).toBe("T-4 implement");
    expect(found?.lane.bars.map((b) => b.label)).toEqual(["r0 ✗", "r1 ✗", "r2 ✗"]);
    expect(found?.lane.bars.every((b) => b.kind === "attempt" && b.tone === "fail")).toBe(true);
    expect(found?.lane.endMark).toBe("⛔");
  });

  it("puts the PL's wait on D-2 on its lane, as a human wait", () => {
    const pl = laneOf(build(), `agent:${PL_DB}`)!.lane;
    expect(pl.sub).toBe("PL");
    const waits = pl.bars.filter((b) => b.kind === "wait");
    expect(waits.map((b) => b.label)).toEqual(expect.arrayContaining(["waiting on D-2"]));
    expect(waits.every((b) => b.open && b.tone === "human")).toBe(true);
  });

  it("puts the IC's failing tools, its task and its live now bar on its lane", () => {
    const ic = laneOf(build(), `agent:${IC_ROUTE_R2}`)!.lane;
    expect(
      ic.bars.filter((b) => b.kind === "tool" && b.tone === "fail").length,
    ).toBeGreaterThanOrEqual(7);
    expect(ic.bars.some((b) => b.kind === "work")).toBe(true);
    expect(ic.bars.some((b) => b.kind === "now")).toBe(true);
    expect(ic.bars[0]!.target).toEqual({
      kind: "agent",
      sessionId: IC_ROUTE_R2,
      at: ic.bars[0]!.start,
    });
  });

  it("draws PR milestones and an env lane, and orders lanes PL, agents, loops, PRs, env", () => {
    const groups = build();
    const db = groups.find((g) => g.project === "db-version")!;
    const pr = db.lanes.find((l) => l.key === `pr:${PR_9631}`)!;
    expect(pr.label).toBe("!9631");
    expect(pr.bars.filter((b) => b.kind === "milestone").map((b) => b.label)).toEqual(
      expect.arrayContaining(["opened", "review ✓", "verify ✗", "checks ✗"]),
    );
    const kinds = db.lanes.map((l) => l.kind);
    expect(kinds.indexOf("agent")).toBeLessThan(kinds.indexOf("loop"));
    expect(kinds.indexOf("loop")).toBeLessThan(kinds.indexOf("pr"));
    expect(kinds.at(-1)).toBe("env");
    expect(db.lanes.find((l) => l.kind === "agent")!.sub).toBe("PL");
  });

  it("names a task's loop by the span's task ref when no PR carries it", () => {
    const spans = timelineSpans();
    const loop = spans.find((s) => s.kind === "loop" && s.loop_id)!;
    const lone: SpanView = {
      ...loop,
      id: "f".repeat(32),
      loop_id: "e".repeat(32),
      pr_id: null,
      task_id: "d".repeat(32),
      name: "implement",
      attrs: { ...loop.attrs, task_ref: "T-9" },
    };
    const iter = spans.find((s) => s.kind === "loop_iter" && s.loop_id === loop.loop_id)!;
    const run: SpanView = {
      ...iter,
      id: "c".repeat(32),
      loop_id: lone.loop_id,
      parent_id: lone.id,
      pr_id: null,
      task_id: lone.task_id,
    };
    const found = laneOf(build({}, [...spans, lone, run]), `loop:${"e".repeat(32)}`);
    expect(found?.lane.label).toBe("T-9 implement");
  });

  it("hides heartbeats by default and shows them on request", () => {
    const beats = (groups: ReturnType<typeof build>) =>
      laneOf(groups, `agent:${PL_DB}`)!.lane.bars.filter((b) => b.kind === "heartbeat").length;
    expect(beats(build())).toBe(0);
    expect(beats(build({ show: { ...DEFAULT_SHOW, heartbeats: true } }))).toBe(1);
    const noLoops = build({ show: { ...DEFAULT_SHOW, loops: false } });
    expect(laneOf(noLoops, "loop:814ca975587e5811a185af63317e48c8")).toBeNull();
  });

  it("replays as of a moment: later spans vanish and later ends reopen", () => {
    const at = 1793011500000; // between r1's start and end
    const replayed = asOf(timelineSpans(), at);
    expect(replayed.every((s) => s.started_at <= at)).toBe(true);
    const groups = build({ to: at, replay: true });
    const loop = laneOf(groups, "loop:814ca975587e5811a185af63317e48c8")!.lane;
    expect(loop.bars.map((b) => b.label)).toEqual(["r0 ✗", "r1"]);
    expect(loop.bars[1]!.open).toBe(true);
    expect(loop.endMark).toBeNull();
    expect(
      laneOf(groups, `agent:${IC_ROUTE_R2}`)?.lane.bars.some((b) => b.kind === "now"),
    ).toBeFalsy();
  });

  it("narrows to chosen agents", () => {
    const groups = build({ agents: [IC_ROUTE_R2] });
    const lanes = groups.flatMap((g) => g.lanes);
    expect(lanes.filter((l) => l.kind === "agent").map((l) => l.sessionId)).toEqual([IC_ROUTE_R2]);
    expect(lanes.some((l) => l.kind === "env")).toBe(false);
  });

  it("places times and ticks across the window", () => {
    expect(position(NOW - HOUR / 2, NOW - HOUR, NOW)).toBe(50);
    expect(position(NOW + 1, NOW - HOUR, NOW)).toBe(100);
    expect(ticks(NOW - HOUR, NOW).length).toBeGreaterThanOrEqual(4);
    expect(windowStart("1h", NOW, [])).toBe(NOW - HOUR);
  });
});

describe("bar labels", () => {
  const bar = (id: string, startMin: number, endMin: number, patch: Partial<Bar> = {}): Bar => ({
    id,
    kind: "tool",
    label: "pnpm test",
    start: NOW - HOUR + startMin * 60_000,
    end: NOW - HOUR + endMin * 60_000,
    open: false,
    tone: "fail",
    title: id,
    target: null,
    ...patch,
  });
  const from = NOW - HOUR;

  it("draws a label only where it fits inside its bar", () => {
    // 600px for an hour: 10px a minute; "pnpm test" needs ~64px.
    const wide = bar("wide", 0, 10);
    const narrow = bar("narrow", 20, 24);
    expect([...labelledBars([wide, narrow], from, NOW, 600).keys()]).toEqual(["wide"]);
    // On a phone's track the same bar is too narrow.
    expect([...labelledBars([wide], from, NOW, 230).keys()]).toEqual([]);
  });

  it("moves a label clear of a later bar, a milestone or the as-of rule, or drops it", () => {
    // "route-r2" needs ~58px; the work bar is 0..300px.
    const work = bar("work", 0, 30, { kind: "work", label: "route-r2" });
    const tool = bar("tool", 2, 4, { label: "" });
    expect(labelledBars([work, tool], from, NOW, 600).get("work")).toBe(40);
    // A bar drawn before it sits underneath: the label stays at the start.
    expect(labelledBars([tool, work], from, NOW, 600).get("work")).toBe(0);
    const mark = bar("mark", 3, 3, { kind: "milestone", label: "opened" });
    expect(labelledBars([work, mark], from, NOW, 600).get("work")).toBe(36);
    expect(labelledBars([work], from, NOW, 600, NOW - HOUR + 2 * 60_000).get("work")).toBe(22);
    expect(labelledBars([work], from, NOW, 600, NOW - HOUR + 20 * 60_000).get("work")).toBe(0);
    // Covered end to end: no label.
    const cover = bar("cover", 0, 30, { label: "" });
    expect(labelledBars([work, cover], from, NOW, 600).has("work")).toBe(false);
  });
});

function Where() {
  const loc = useLocation();
  return <p data-testid="where">{loc.pathname + loc.search}</p>;
}

describe("TimelineView", () => {
  it("renders lanes with waits and attempt markers, and a bar opens the agent at that time", () => {
    const groups = build();
    const fid = round3Snapshot().factory.id;
    render(
      <MemoryRouter initialEntries={["/x"]}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <TimelineView factoryId={fid} groups={groups} from={NOW - 3 * HOUR} to={NOW} />
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    const group = screen.getByTestId("timeline-group-db-version");
    expect(
      within(group)
        .getAllByTestId("timeline-bar-attempt")
        .map((b) => b.textContent),
    ).toEqual(["r0 ✗", "r1 ✗", "r2 ✗"]);
    expect(within(group).getAllByTestId("timeline-bar-wait").length).toBeGreaterThan(0);
    expect(within(group).getByLabelText("exhausted")).toHaveTextContent("⛔");
    const tool = within(group).getAllByTestId("timeline-bar-tool")[0]!;
    fireEvent.click(tool);
    expect(screen.getByTestId("where").textContent).toMatch(
      new RegExp(`^/factory/${fid}/agents/${IC_ROUTE_R2}\\?at=\\d+$`),
    );
  });
});
