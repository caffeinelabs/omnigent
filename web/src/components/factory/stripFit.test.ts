import { describe, expect, it } from "vitest";
import { planStripFit } from "./stripFit";

const items = [
  { key: "stage", priority: 10, width: 50 },
  { key: "gates", priority: 9, width: 60 },
  { key: "project", priority: 6, width: 80, minWidth: 40 },
  { key: "cost", priority: 2, width: 40 },
];

describe("planStripFit", () => {
  it("hides nothing when everything fits", () => {
    expect(planStripFit(300, 50, items)).toEqual({ hidden: new Set(), clamp: null });
  });

  it("hides the lowest priority first, whole", () => {
    // 50 + 230 = 280; without cost 240.
    expect(planStripFit(250, 50, items)).toEqual({ hidden: new Set(["cost"]), clamp: null });
  });

  it("truncates a text part before hiding it", () => {
    // Without cost: 240; the project may give up to 40 px.
    expect(planStripFit(215, 50, items)).toEqual({
      hidden: new Set(["cost"]),
      clamp: { key: "project", width: 55 },
    });
  });

  it("hides a text part that cannot fit at its minimum", () => {
    expect(planStripFit(190, 50, items)).toEqual({
      hidden: new Set(["cost", "project"]),
      clamp: null,
    });
  });

  it("keeps hiding up to the highest priority and leaves the fixed parts", () => {
    expect(planStripFit(60, 50, items).hidden).toEqual(
      new Set(["cost", "project", "gates", "stage"]),
    );
  });
});
