import { describe, expect, it } from "vitest";
import type { StatusSummary } from "@/lib/factoryStatusApi";
import { ROUND3_NOW_S } from "../shell/__fixtures__/shellFixtures";
import { busyNow, idleNow, pausedNow, waitingOnYouNow } from "./__fixtures__/nowScenarios";
import {
  attentionText,
  clockText,
  idleSpendText,
  nowHeadline,
  spendText,
  splitHealth,
  splitWaiting,
  waitedFor,
  waitedLong,
  waitingEmptyText,
  workingNote,
  workingStage,
} from "./nowCardModel";

/** Epoch seconds of a local wall-clock time, so the expectations hold in any time zone. */
const local = (y: number, m: number, d: number, h: number, min: number) =>
  new Date(y, m - 1, d, h, min).getTime() / 1000;

describe("clockText", () => {
  it("says only the time today and adds the day otherwise", () => {
    const now = local(2026, 11, 2, 15, 0);
    expect(clockText(local(2026, 11, 2, 9, 12), now)).toBe("09:12");
    expect(clockText(local(2026, 10, 30, 9, 12), now)).toMatch(/^.*30.*, 09:12$/);
  });
});

describe("nowHeadline", () => {
  it("counts what is in flight and what is queued", () => {
    const summary = busyNow();
    expect(nowHeadline(summary, ROUND3_NOW_S)).toBe("Working on 4 projects · 1 queued");
    expect(nowHeadline({ ...summary, counts: { ...summary.counts, queued: 0 } }, 0)).toBe(
      "Working on 4 projects",
    );
  });

  it("says since when an idle factory has had nothing to do", () => {
    const summary = idleNow();
    const since = summary.spend.idle_since!;
    expect(nowHeadline(summary, ROUND3_NOW_S)).toBe(
      `Idle since ${clockText(since, ROUND3_NOW_S)} — no active projects`,
    );
    const unknown: StatusSummary = { ...summary, spend: { ...summary.spend, idle_since: null } };
    expect(nowHeadline(unknown, ROUND3_NOW_S)).toBe("Idle — no active projects");
  });

  it("names who paused the factory", () => {
    expect(nowHeadline(pausedNow(), ROUND3_NOW_S)).toBe("Paused by alice");
    const summary = pausedNow();
    summary.health.items[0]!.by = null;
    expect(nowHeadline(summary, ROUND3_NOW_S)).toBe("Paused");
    expect(nowHeadline({ ...summary, state: "done" }, ROUND3_NOW_S)).toBe(
      "Finished: every project is closed",
    );
  });
});

describe("waiting", () => {
  it("splits the viewer's from everyone else's, keeping the server's order", () => {
    const split = splitWaiting(busyNow());
    expect(split.mine.map((i) => i.ref)).toEqual([
      "D-35",
      "D-33",
      "D-3",
      "INC-12",
      "D-31",
      "D-34",
      "INC-13",
      "PR #9630",
    ]);
    expect(split.others.map((i) => [i.ref, i.who])).toEqual([["D-2", "mia"]]);
  });

  it("says nothing waits on you, and how much waits on others", () => {
    expect(waitingEmptyText(splitWaiting(idleNow()))).toBe("Nothing waiting on you");
    const split = splitWaiting(busyNow());
    expect(waitingEmptyText({ mine: [], others: split.mine })).toBe(
      "Nothing waiting on you · 8 things waiting on others",
    );
  });

  it("ages a wait against the cockpit clock", () => {
    const [d19] = splitWaiting(waitingOnYouNow()).mine;
    expect(d19).toMatchObject({ ref: "D-19", text: "approve Atlas's proposal" });
    expect(waitedFor(d19!.since, ROUND3_NOW_S)).toBe("2d 18h");
    expect(waitedFor(0, ROUND3_NOW_S)).toBeNull();
    expect(waitedLong(d19!.since, ROUND3_NOW_S)).toBe(true);
    expect(waitedLong(ROUND3_NOW_S - 3600, ROUND3_NOW_S)).toBe(false);
  });
});

describe("working", () => {
  it("reads the stage, the PR and the problem or the lead's own words", () => {
    const [cache, , api] = busyNow().working;
    expect(workingStage(cache!)).toBe("Building · PR #9655");
    expect(workingNote(cache!)).toBe("its lead agent stopped · relaunch failed 3×");
    expect(workingStage(api!)).toBe("Ready · PR #9630");
    expect(workingNote(api!)).toBe("READY FOR HUMAN MERGE #9630");
  });
});

describe("spend", () => {
  it("says the last 24 h and the rate", () => {
    expect(spendText(busyNow().spend)).toBe("$41.20 in the last 24 h · $6.10/h now");
    expect(spendText({ ...busyNow().spend, last_24h_usd: null, usd_per_h: null })).toBe(
      "No priced spend in the last 24 h",
    );
  });

  it("calls out an idle factory that still spends", () => {
    expect(idleSpendText(idleNow().spend)).toBe("Idle, but still spending $2.71/h");
    expect(idleSpendText(busyNow().spend)).toBeNull();
  });
});

describe("health", () => {
  it("folds the problems of listed projects into one line and keeps the rest", () => {
    const summary = pausedNow();
    const split = splitHealth(summary.health.items, summary.working);
    expect(split.listed.map((i) => i.kind)).toEqual(["paused"]);
    expect(attentionText(split.attention)).toBe(
      "2 projects need attention: cache-warm, db-version",
    );
    expect(attentionText(split.attention.slice(0, 1))).toBe(
      "1 project needs attention: cache-warm",
    );
    // A project the card does not list keeps its own line.
    const unlisted = splitHealth(summary.health.items, summary.working.slice(2));
    expect(unlisted.attention).toEqual([]);
    expect(unlisted.listed.map((i) => i.project ?? i.kind)).toEqual([
      "paused",
      "cache-warm",
      "db-version",
    ]);
  });
});
