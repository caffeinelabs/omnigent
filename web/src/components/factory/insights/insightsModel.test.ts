import { describe, expect, it } from "vitest";
import {
  MIN_N,
  cumulative,
  daySeries,
  deltaTone,
  formatDelta,
  formatValue,
  headline,
  markPosition,
  metricLabel,
  nState,
  nText,
  promptBand,
  splitText,
  versionMarks,
  windowDays,
  windowSeconds,
} from "./insightsModel";
import type { EventView } from "@/lib/factoryApi";
import { allMetrics, versionsResponse } from "./__fixtures__/learnFixtures";
import { hourlyCounts } from "./healthSparks";
import { sparkTotal } from "./Sparkline";

describe("headline", () => {
  it("reads value, then p50, then sums a per-key breakdown", () => {
    expect(headline({ value: 0.79, n: 14 })).toBe(0.79);
    expect(headline({ p50: 11_400, p90: 32_400, n: 14 })).toBe(11_400);
    expect(headline({ by: { a: { value: 2, n: 1 }, b: { value: 3, n: 1 } }, n: 2 })).toBe(5);
    expect(headline({ bounce_rate: 0.01, n: 388 })).toBe(0.01);
    expect(headline({ value: null, n: 0 })).toBeNull();
    expect(headline(undefined)).toBeNull();
  });
});

describe("formatting", () => {
  it.each([
    [11_400, "s", "3h 10m"],
    [0.79, "share", "79%"],
    [8.2, "usd", "$8.20"],
    [0.004, "usd", "<$0.01"],
    [34, "per_day", "34/day"],
    [1.57, "per_day", "1.6/day"],
    [4.1, "rating", "4.1★"],
    [1.35, "ratio", "1.35×"],
    [6, "count", "6"],
    [2.36, "acts", "2.4"],
    [null, "share", "—"],
  ])("formats %s %s as %s", (value, unit, text) => {
    expect(formatValue(value as number | null, unit)).toBe(text);
  });

  it("formats deltas with a sign in the metric's unit", () => {
    expect(formatDelta(0.06, "share")).toBe("+6 pp");
    expect(formatDelta(-2_500, "s")).toBe("−41m");
    expect(formatDelta(1.1, "usd")).toBe("+$1.10");
    expect(formatDelta(-2, "count")).toBe("−2");
    expect(formatDelta(0, "share")).toBeNull();
    expect(formatDelta(null, "share")).toBeNull();
  });

  it("colors a delta by the metric's good direction", () => {
    expect(deltaTone(0.06, "up")).toBe("pass");
    expect(deltaTone(1.1, "down")).toBe("warning");
    expect(deltaTone(-2, "down")).toBe("pass");
    expect(deltaTone(5, null)).toBe("muted");
  });
});

describe("small n", () => {
  it("calls n below the minimum inconclusive and n=0 no data", () => {
    expect(MIN_N).toBe(3);
    expect(nState(0)).toBe("none");
    expect(nState(2)).toBe("small");
    expect(nState(3)).toBe("ok");
    expect(nText(0)).toBe("no data yet");
    expect(nText(2)).toBe("n=2 — inconclusive");
    expect(nText(14)).toBe("n=14");
  });
});

describe("day series and windows", () => {
  it("covers every UTC day of the window and leaves gaps null", () => {
    const since = Date.UTC(2026, 9, 20, 12) / 1000;
    const until = Date.UTC(2026, 9, 23, 6) / 1000;
    const days = windowDays(since, until);
    expect(days).toEqual(["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]);
    const series = daySeries(
      { "2026-10-21": { value: 2, n: 1 }, "2026-10-23": { value: 5, n: 1 } },
      days,
    );
    expect(series).toEqual([null, 2, null, 5]);
    expect(cumulative(series)).toEqual([0, 2, 2, 7]);
    expect(windowSeconds("24h")).toBe(86_400);
    expect(windowSeconds("7d")).toBe(604_800);
  });
});

describe("version markers", () => {
  it("keeps the versions in force when the window opened and every one after", () => {
    const { definition, platform } = versionsResponse();
    const since = definition.find((d) => d.version === 6)!.created_at - 60;
    const marks = versionMarks(definition, platform, since);
    expect(marks.map((m) => m.label)).toEqual([
      "img e15e04c",
      "def v5",
      "def v6",
      "img 0bb15c1",
      "def v7",
    ]);
    const v7 = marks.find((m) => m.version === 7)!;
    expect(v7.current).toBe(true);
    expect(v7.title).toContain("C-14");
    expect(markPosition(since, since, since + 100)).toBe(0);
    expect(markPosition(since + 50, since, since + 100)).toBe(0.5);
    expect(markPosition(since - 50, since, since + 100)).toBe(0);
  });
});

describe("the human-load band", () => {
  it("reads per day, the 30–50 band and the lead/PL/other split", () => {
    const band = promptBand(allMetrics().human_prompts_per_day);
    expect(band.perDay).toBe(34);
    expect(band.band).toEqual([30, 50]);
    expect(band.inBand).toBe(true);
    expect(splitText(band.split)).toBe("L58 P36 O6");
  });
});

describe("health sparks", () => {
  const H = 3_600_000;
  const now = 1000 * H;
  const at = (hoursAgo: number): EventView =>
    ({ id: `e${hoursAgo}`, ts: now - hoursAgo * H + 1, kind: "message.sent" }) as EventView;

  it("counts events per hour, oldest hour first", () => {
    const series = hourlyCounts([at(1), at(1), at(3)], now);
    expect(series).toHaveLength(24);
    expect(series[23]).toBe(2);
    expect(series[21]).toBe(1);
    expect(sparkTotal(series)).toBe(3);
  });

  it("leaves the hours a capped read did not reach unknown, not zero", () => {
    const series = hourlyCounts([at(1), at(3)], now, 24, now - 5 * H + 10);
    expect(series.slice(0, 20).every((v) => v === null)).toBe(true);
    expect(series.slice(20)).toEqual([0, 1, 0, 1]);
  });
});

describe("metricLabel", () => {
  it("reads metric keys as people say them", () => {
    expect(metricLabel("cost_per_merged_pr")).toBe("$ / merged PR");
    expect(metricLabel("heartbeat_usd_per_day")).toBe("heartbeat $ / day");
    expect(metricLabel("loop_exhaustion_rate")).toBe("loop exhaustion rate");
  });
});
