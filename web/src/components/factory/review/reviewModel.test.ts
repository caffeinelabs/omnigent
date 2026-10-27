import { describe, expect, it } from "vitest";
import type { EventView } from "@/lib/factoryApi";
import { liveRunSnapshot } from "../__fixtures__/liveRun";
import { apiCalls9630, reviewScenario } from "./__fixtures__/reviewFixtures";
import {
  apiCallPath,
  bodyPreview,
  demoDownloads,
  demoRecorder,
  demoScreenshots,
  gateTally,
  olderDemos,
  parseApiCalls,
  prettyBody,
  previewHealth,
  uxDemoMissing,
} from "./reviewModel";

function pr(number: number) {
  return liveRunSnapshot().prs.find((p) => p.number === number)!;
}

describe("parseApiCalls", () => {
  it("reads the harness's api.json", () => {
    const [call] = parseApiCalls(apiCalls9630());
    expect(call).toMatchObject({
      i: 0,
      step: 2,
      method: "GET",
      status: 200,
      ms: 38,
      response: { contentType: "application/json; charset=utf-8", bytes: 112, truncated: false },
    });
    expect(call?.response?.headers["x-factory-commit"]).toMatch(/^7767f0d/);
  });

  it("tolerates the older shape (response body inline) and drops junk", () => {
    const calls = parseApiCalls([
      {
        method: "get",
        url: "https://api.x/version",
        status: 200,
        ms: 3,
        response: { gitSha: "abc" },
      },
      { method: "GET" },
      "nope",
    ]);
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ i: 0, method: "GET", step: null });
    expect(calls[0]?.response?.body).toBe('{"gitSha":"abc"}');
    expect(parseApiCalls({ calls: [{ url: "/a" }] })).toHaveLength(1);
    expect(parseApiCalls(null)).toEqual([]);
  });

  it("formats paths and bodies for the table", () => {
    expect(apiCallPath("https://api-x.envs.sandbox.caffeinelabs.io/db/version?x=1")).toBe(
      "/db/version?x=1",
    );
    expect(apiCallPath("/relative")).toBe("/relative");
    expect(prettyBody('{"a":1}')).toBe('{\n  "a": 1\n}');
    expect(prettyBody("plain")).toBe("plain");
    expect(bodyPreview("x".repeat(100), 10)).toBe("xxxxxxxxx…");
  });
});

describe("gates and demos", () => {
  it("counts a ready PR's gates, demo included", () => {
    expect(gateTally(pr(9630))).toEqual({ passing: 7, total: 7 });
    expect(gateTally(pr(9631))).toEqual({ passing: 2, total: 7 });
  });

  it("wants a demo of the head for caffeine-ux changes only", () => {
    const ux = pr(9630);
    expect(uxDemoMissing(ux)).toBe(false);
    ux.demo = null;
    expect(uxDemoMissing(ux)).toBe(true);
    const api = pr(9631);
    expect(uxDemoMissing(api)).toBe(false);
  });

  it("orders screenshots by step and lists downloads by kind", () => {
    const demo = pr(9630).demo!;
    expect(demoScreenshots(demo).map((f) => f.step)).toEqual([1, 3, 3]);
    expect(demoDownloads(demo).map((f) => f.kind)).toEqual(["script", "har", "api"]);
  });

  it("reads the preview's health against the head", () => {
    expect(previewHealth(pr(9630), pr(9630).preview!).label).toBe("healthy");
    expect(previewHealth(pr(9631), pr(9631).preview!)).toMatchObject({
      label: "behind head",
      tone: "warning",
      reachable: true,
    });
    const parked = reviewScenario("preview-parked").pr;
    expect(previewHealth(parked, parked.preview!)).toMatchObject({ reachable: false });
  });
});

describe("olderDemos", () => {
  const base: Omit<EventView, "id" | "ts" | "summary" | "data"> = {
    kind: "demo.published",
    severity: "notice",
    project_slug: "api-version",
    from_id: null,
    to_id: null,
    from_title: null,
    to_title: null,
    subject: "pr:caffeinelabs/app#9630",
  };

  it("lists other demos of the PR, newest first, once each", () => {
    const events: EventView[] = [
      {
        ...base,
        id: "1",
        ts: 1_000_000,
        summary: "demo FAIL of caffeinelabs/app#9630 at aaaaaaa: x",
        data: { demo_id: "d1", status: "FAIL" },
      },
      {
        ...base,
        id: "2",
        ts: 2_000_000,
        summary: "demo PASS of caffeinelabs/app#9630 at bbbbbbb: x",
        data: { demo_id: "d2", status: "PASS" },
      },
      {
        ...base,
        id: "3",
        ts: 3_000_000,
        summary: "demo PASS at ccccccc",
        data: { demo_id: "current" },
      },
      {
        ...base,
        id: "4",
        ts: 4_000_000,
        subject: "pr:caffeinelabs/app#9631",
        summary: "at ddddddd",
        data: { demo_id: "other-pr" },
      },
      { ...base, id: "5", ts: 5_000_000, summary: "dup at bbbbbbb", data: { demo_id: "d2" } },
    ];
    expect(olderDemos(events, pr(9630), "current")).toEqual([
      { demoId: "d2", sha7: "bbbbbbb", status: "PASS", at: 2000 },
      { demoId: "d1", sha7: "aaaaaaa", status: "FAIL", at: 1000 },
    ]);
  });
});

describe("demoRecorder", () => {
  const member = (title: string, project: string | null, running = true) => ({
    ...liveRunSnapshot().members.find((m) => m.role === "demo")!,
    session_id: title,
    title,
    project,
    running,
  });

  it("picks the running demo agent of the PR's project", () => {
    const p = pr(9630);
    expect(demoRecorder(p, [member("demo:other", "db-version")])).toBeNull();
    expect(demoRecorder(p, [member("demo:x", "api-version", false)])).toBeNull();
    expect(demoRecorder(p, [member("demo:x", "api-version")])?.title).toBe("demo:x");
  });

  it("prefers the one named after the PR's task, and won't guess between others", () => {
    const p = { ...pr(9630), task: "version-endpoint" };
    const named = member("demo:demo-version-endpoint", "api-version");
    const other = member("demo:demo-settings-row", "api-version");
    expect(demoRecorder(p, [other, named])?.title).toBe(named.title);
    expect(demoRecorder(p, [other, member("demo:demo-third", "api-version")])).toBeNull();
  });
});
