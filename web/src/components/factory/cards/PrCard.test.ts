import { describe, expect, it } from "vitest";
import { liveRunSnapshot } from "../__fixtures__/liveRun";
import { prStateView } from "./PrCard";

describe("prStateView", () => {
  it("says who merges a ready PR", () => {
    const pr = liveRunSnapshot().prs.find((p) => p.number === 9630)!;
    expect(prStateView(pr).label).toBe("Ready for human merge");
    expect(prStateView({ ...pr, auto_merge: true })).toEqual({
      label: "Ready · merges automatically when checks pass",
      tone: "pass",
    });
  });
});
