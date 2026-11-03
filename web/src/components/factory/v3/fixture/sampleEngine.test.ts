import { describe, expect, it } from "vitest";
import { statusText } from "../statusModel";
import { sampleEngine } from "./sampleNodeSource";

// The sample stands in for the server until the node projection lands, so it
// must answer the API's contract cases (api.md §8) the way the server will.

function page(ref: string, options: Parameters<typeof sampleEngine>[0] = {}) {
  const p = sampleEngine(options).node(ref);
  if (!p) throw new Error(`no node ${ref}`);
  return p;
}

describe("the sample factory as the node API serves it", () => {
  it("keeps every node one level under its parent, in both trees and at scale", () => {
    expect(sampleEngine().checkStructure()).toEqual([]);
    expect(sampleEngine({ scale: true }).checkStructure()).toEqual([]);
  });

  it("assistant-core: not moving, held for C-16, needed by the panel, no D-48 alarm", () => {
    const p = page("assistant-core");
    expect(statusText(p.status)).toBe(
      "Not moving: T-16 → held for C-16 → needs you: Apply C-16 (D-45)",
    );
    expect(p.chips.map((c) => `${c.kind} ${c.title} · ${c.state}`)).toEqual([
      "needed by assistant-panel-ux · waiting",
    ]);
    expect(JSON.stringify(p)).not.toContain('"D-48"');
    expect(p.levels_below.map((l) => `${l.name} ${l.count}`)).toEqual([
      "Tasks 2",
      "Changes 2",
      "Runs 2",
    ]);
  });

  it("the panel: not started for 7 h, the path through assistant-core, the met links noted", () => {
    const p = page("assistant-panel-ux");
    expect(statusText(p.status)).toBe(
      "Not started for 7 h: waits on assistant-core › T-16 → held for C-16 → needs you: Apply C-16 (D-45)",
    );
    expect(p.status.note).toMatch(/3 shortlist projects/);
    expect(p.status.why?.at(-1)).toEqual(expect.objectContaining({ tone: "you" }));
    const met = p.chips.find((c) => c.list);
    expect(met).toEqual(
      expect.objectContaining({ title: "3 met links", state: "3 had no reason", tone: "warn" }),
    );
  });

  it("the product: the top blocker through the assistant, and what else it holds", () => {
    const p = page("product");
    expect(statusText(p.status)).toBe(
      "Not moving: Agent assistant (sample) › assistant-core › T-16 → held for C-16 → needs you: Apply C-16 (D-45) · also holds assistant-panel-ux",
    );
    expect(p.rollup.alarms_closed?.count).toBe(3);
    expect(p.rollup.moving_anywhere).toBe(false);
    expect(p.home).toBe("feature");
    // No goal exists, so the Goal level is hidden and not counted.
    expect(p.levels_below.map((l) => l.level)).toEqual([
      "need",
      "feature",
      "project",
      "task",
      "change",
      "run",
    ]);
  });

  it("a tile ends at the person: the step-down names its way, a hop names the node it waits on", () => {
    const feature = page("I-1");
    const core = feature.children.items.find((k) => k.ref === "assistant-core")!;
    const panel = feature.children.items.find((k) => k.ref === "assistant-panel-ux")!;
    expect(statusText(core.status_compact, { compact: true, aboveId: "D-45" })).toBe(
      "Needs you: Apply C-16 · via T-16 · the ask above",
    );
    expect(statusText(panel.status_compact, { compact: true, aboveId: "D-45" })).toBe(
      "Waits on assistant-core · held for C-16",
    );
    expect(feature.children.columns?.map((c) => c.title)).toEqual(["First", "Then"]);
    expect(feature.children.later).toHaveLength(4);
    expect(feature.children.ghosts.map((g) => g.ref)).toEqual(["live-search-fix"]);
    expect(feature.rollup.progress).toEqual(
      expect.objectContaining({ label: "2/6", what: "acceptance" }),
    );
    expect(Math.round((feature.rollup.progress?.secondary ?? 0) * 100)).toBe(67);
    expect(feature.rollup.cost).toBeCloseTo(51.13);
  });

  it("after Apply C-16, the panel waits on a moving core and the feature ends at D-49", () => {
    const options = { answered: [["D-45", "Apply"]] as [string, string][] };
    expect(statusText(page("assistant-panel-ux", options).status)).toMatch(
      /^Waiting: waits on assistant-core · moving/,
    );
    expect(page("I-1", options).status.decision?.id).toBe("D-49");
  });

  it("an implicit parent with one child answers as that child", () => {
    const p = page("feature:from-C-12");
    expect(p.node.ref).toBe("build-tier-drafts-3");
    expect(p.breadcrumb.filter((c) => c.folded).map((c) => c.ref)).toEqual([
      "need:from-C-12",
      "feature:from-C-12",
    ]);
  });

  it("Home: features grouped under needs, the implicit one folded in place", () => {
    const view = sampleEngine().level("product", "feature")!;
    expect(view.groups.map((g) => g.parent.title)).toEqual([
      "Agents that also do discovery, sales and promotion",
      "From an Atlas proposal",
      "Small fixes from your shortlist",
    ]);
    expect(view.via_fold).toBe(1);
    expect(view.groups[2]!.folded_done).toBe(true);
  });

  it("at scale, a level view groups tasks under their projects and folds all-done groups", () => {
    const view = sampleEngine({ scale: true }).level("product", "task")!;
    expect(view.total).toBeGreaterThan(300);
    expect(view.done_groups?.count).toBeGreaterThan(1);
    expect(view.groups[0]!.parent.title).toBe("assistant-core");
  });
});
