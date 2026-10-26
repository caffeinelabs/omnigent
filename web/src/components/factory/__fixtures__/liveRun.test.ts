import { describe, expect, it } from "vitest";
import {
  LIVE_RUN_FACTORY_ID,
  allDoneSnapshot,
  factoryList,
  githubDisconnectedSnapshot,
  liveRunSnapshot,
  liveRunSummary,
  tenProjectsFactoryList,
  tenProjectsSnapshot,
} from "./liveRun";

// The cockpit packages build their stories and tests on these variants, so
// keep them internally consistent.
describe("live-run fixtures", () => {
  it("rolls the live run up into its index card", () => {
    const summary = liveRunSummary();
    expect(summary.id).toBe(LIVE_RUN_FACTORY_ID);
    expect(summary.stage_counts).toMatchObject({ ready: 1, building: 1, done: 1, queued: 0 });
    expect(summary.decisions_open).toBe(2);
    expect(summary.prs_ready).toBe(1);
    expect(summary.members_running).toBe(3);
  });

  it("hands out independent copies", () => {
    const first = liveRunSnapshot();
    first.factory.name = "changed";
    expect(liveRunSnapshot().factory.name).toBe("Atlas factory");
  });

  it("finishes everything in the all-done variant", () => {
    const done = allDoneSnapshot();
    expect(done.factory.state).toBe("done");
    expect(done.projects.every((p) => p.stage === "done")).toBe(true);
    expect(done.prs.every((pr) => pr.state === "merged" && pr.merged)).toBe(true);
    expect(done.decisions.some((d) => d.status === "open")).toBe(false);
    expect(done.members.some((m) => m.running)).toBe(false);
  });

  it("marks GitHub disconnected with a poll error on every open PR", () => {
    const snapshot = githubDisconnectedSnapshot();
    expect(snapshot.health.github.state).toBe("disconnected");
    const open = snapshot.prs.filter((pr) => pr.state === "open");
    expect(open.length).toBeGreaterThan(0);
    expect(open.every((pr) => pr.poll_error)).toBe(true);
  });

  it("builds ten uniquely named projects under their own factory", () => {
    const snapshot = tenProjectsSnapshot();
    expect(snapshot.projects).toHaveLength(10);
    expect(new Set(snapshot.projects.map((p) => p.slug)).size).toBe(10);
    expect(snapshot.factory.id).not.toBe(LIVE_RUN_FACTORY_ID);
  });

  it.each([factoryList, tenProjectsFactoryList])("lists factories under unique ids", (list) => {
    const ids = list().data.map((f) => f.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
