import { describe, expect, it } from "vitest";
import { liveRunSnapshot } from "../__fixtures__/liveRun";
import {
  DEFAULT_MERGE_POLICY,
  deliveryOf,
  demoMissing,
  demoPathsTouched,
  globRegExp,
  mergePolicyText,
} from "./deliveryModel";
import { SAMPLE_DEMO_PATHS, sampleProduct } from "./__fixtures__/sampleProduct";

function pr(number: number) {
  return liveRunSnapshot().prs.find((p) => p.number === number)!;
}

const delivery = (fields: Parameters<typeof sampleProduct>[0]) => sampleProduct(fields).delivery;

describe("merge policy", () => {
  it("says the profile's words, and that the factory never merges", () => {
    expect(mergePolicyText(null)).toBe(DEFAULT_MERGE_POLICY);
    expect(mergePolicyText(delivery({ merge_policy: "  " }))).toBe(DEFAULT_MERGE_POLICY);
    expect(mergePolicyText(delivery({ merge_policy: "Release managers merge after QA" }))).toBe(
      "Release managers merge after QA. The factory never merges.",
    );
    expect(
      mergePolicyText(delivery({ merge_policy: "Maintainers merge; the factory never merges." })),
    ).toBe("Maintainers merge; the factory never merges.");
  });

  it("reads the delivery block from the snapshot when the server sends one", () => {
    const snapshot = liveRunSnapshot();
    expect(deliveryOf(snapshot)).toBeNull();
    snapshot.factory.product = sampleProduct({ merge_policy: "Owners merge" });
    expect(deliveryOf(snapshot)?.merge_policy).toBe("Owners merge");
  });
});

describe("paths that need a demo", () => {
  it("match globs the way scope paths read", () => {
    expect(globRegExp("web/**").test("web/src/App.tsx")).toBe(true);
    expect(globRegExp("web/**").test("website/x.ts")).toBe(false);
    expect(globRegExp("web/*.css").test("web/a.css")).toBe(true);
    expect(globRegExp("web/*.css").test("web/x/a.css")).toBe(false);
    expect(globRegExp("**/*.tsx").test("App.tsx")).toBe(true);
    expect(globRegExp("**/*.tsx").test("a/b/App.tsx")).toBe(true);
    expect(globRegExp("docs").test("docs/a.md")).toBe(true);
    expect(globRegExp("docs/").test("docs")).toBe(true);
    expect(globRegExp("a+b/c.md").test("a+b/c.md")).toBe(true);
  });

  it("want a demo of the head only where the profile says so", () => {
    const ui = pr(9630);
    const api = pr(9631);
    const profile = delivery({ demo_paths: SAMPLE_DEMO_PATHS });
    expect(demoPathsTouched(ui, profile)).toEqual(SAMPLE_DEMO_PATHS);
    expect(demoMissing(ui, profile)).toBe(false);
    ui.demo = null;
    expect(demoMissing(ui, profile)).toBe(true);
    expect(demoMissing(api, profile)).toBe(false);
    // No paths in the profile: no demo expectation at all.
    expect(demoMissing(ui, null)).toBe(false);
    expect(demoMissing(ui, delivery({}))).toBe(false);
  });
});
