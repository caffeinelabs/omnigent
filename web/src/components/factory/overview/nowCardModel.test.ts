import { describe, expect, it } from "vitest";
import type { StatusSummary, StatusWaitingItem } from "@/lib/factoryStatusApi";
import { ROUND3_NOW_S } from "../shell/__fixtures__/shellFixtures";
import {
  busyNow,
  cantStartNow,
  idleNow,
  idleWaitingNow,
  liveNow,
  pausedNow,
  waitingOnYouNow,
} from "./__fixtures__/nowScenarios";
import {
  WAITING_SHOWN,
  attentionText,
  checkInText,
  clockText,
  idleSpendText,
  idleText,
  leadParts,
  leadWho,
  nowHeadline,
  nowShownKeys,
  receiptText,
  shownWaiting,
  spendText,
  splitHealth,
  splitWaiting,
  staleText,
  waitedFor,
  waitedLong,
  waitingDetail,
  waitingEmptyText,
  workingNote,
  workingNoteQuoted,
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
  it("is the server's headline, the one the Slack digest opens with", () => {
    expect(nowHeadline(liveNow())).toBe("4 PRs wait on you · 1 project in progress");
    expect(nowHeadline(busyNow())).toBe("8 things wait on you · 2 projects in progress · 1 queued");
    expect(nowHeadline(idleNow())).toBe("Idle, yet still spending");
    expect(nowHeadline(idleWaitingNow())).toBe("Idle, yet still spending · 4 PRs wait on you");
    expect(nowHeadline(cantStartNow())).toBe(
      "Nothing in progress · 1 project can't start · 2 queued",
    );
    expect(nowHeadline(pausedNow())).toBe("Paused by alice · 8 things wait on you");
    // An older server ended it with a period.
    expect(nowHeadline({ ...idleNow(), headline: "Idle: no active projects." })).toBe(
      "Idle: no active projects",
    );
  });

  it("says since when an idle factory has had nothing to do, under Working on", () => {
    const summary = idleNow();
    const since = summary.spend.idle_since!;
    expect(idleText(summary.spend, ROUND3_NOW_S)).toBe(
      `No active projects since ${clockText(since, ROUND3_NOW_S)}`,
    );
    expect(idleText({ ...summary.spend, idle_since: null }, ROUND3_NOW_S)).toBe(
      "No active projects",
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

  it("lists a ready-for-merge project once: its PR waits on you, it is not in progress", () => {
    const summary = liveNow();
    expect(summary.waiting.map((i) => [i.ref, i.project, i.holds])).toEqual([
      ["PR #9826", "version-endpoint", true],
      ["PR #9829", "settings-row", true],
      ["PR #9828", "env-badge", true],
      ["PR #9827", "cache-warm", true],
    ]);
    expect(summary.working.map((w) => w.project)).toEqual(["validate-build-tier"]);
  });

  it("names the card's lines for the Needs-you panel to leave out", () => {
    const busy = busyNow();
    const shown = shownWaiting(splitWaiting(busy));
    expect(shown).toHaveLength(WAITING_SHOWN);
    expect([...nowShownKeys(busy)]).toEqual(
      shown.map((i) => i.key.replace(/^incident:/, "issue:")),
    );
    expect([...nowShownKeys(busy)]).toContain(`issue:${shown[3]!.key.split(":")[1]}`);
    // None of yours: the first two of others' are the lines.
    const others = { ...busy, waiting: busy.waiting.filter((i) => !i.for_you) };
    expect([...nowShownKeys(others)]).toEqual(["decision:" + others.waiting[0]!.key.slice(9)]);
  });

  it("says nothing waits on you, and how much waits on others", () => {
    expect(waitingEmptyText(splitWaiting(idleNow()))).toBe("Nothing waiting on you");
    const split = splitWaiting(busyNow());
    expect(waitingEmptyText({ mine: [], others: split.mine })).toBe(
      "Nothing waiting on you · 8 things waiting on others",
    );
  });

  it("puts a PR's or incident's project before its detail", () => {
    const [pr] = liveNow().waiting;
    expect(waitingDetail(pr!)).toBe("version-endpoint: Add GET /version");
    const incident = busyNow().waiting.find((i) => i.ref === "INC-12")!;
    expect(waitingDetail(incident)).toBe("cache-warm");
    const decision = busyNow().waiting.find((i) => i.ref === "D-3")!;
    expect(waitingDetail(decision)).toBe(decision.detail);
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

describe("Slack receipts", () => {
  const at = (h: number, m: number) => local(2026, 11, 2, h, m);
  const now = at(15, 0);

  it("says when the viewer was first DM'd and the newest reminder", () => {
    const slack: StatusWaitingItem["slack"] = [
      { at: at(14, 4), stage: "r4h" },
      { at: at(10, 4), stage: "ready" },
    ];
    expect(receiptText(slack, now)).toBe("DM'd 10:04 · reminded 14:04");
    expect(receiptText([{ at: at(10, 4), stage: "new" }], now)).toBe("DM'd 10:04");
    expect(
      receiptText(
        [
          { at: at(9, 0), stage: "new" },
          { at: at(13, 0), stage: "r4h" },
          { at: at(14, 30), stage: "deadline" },
        ],
        now,
      ),
    ).toBe("DM'd 09:00 · last call 14:30");
  });

  it("ages a DM from an earlier day, and says nothing without one", () => {
    expect(receiptText([{ at: now - 2 * 86_400, stage: "opened" }], now)).toBe("DM'd 2d ago");
    expect(receiptText([], now)).toBeNull();
    expect(receiptText(undefined, now)).toBeNull();
  });

  it("reads the live factory's receipts", () => {
    const [pr] = liveNow().waiting;
    expect(pr!.slack).toHaveLength(2);
    expect(receiptText(pr!.slack, ROUND3_NOW_S)).toMatch(/^DM'd .+ · reminded .+$/);
  });
});

describe("working", () => {
  it("reads the stage and a plain status from facts, never the lead's raw label", () => {
    const [vbt] = liveNow().working;
    expect(workingStage(vbt!)).toBe("Verify");
    expect(workingNote(vbt!)).toBe(
      "PR #9774 merged · working on T-9: Check the build tier on a preview",
    );
    expect(workingNoteQuoted(vbt!)).toBe(false);
    const [cache, badge] = busyNow().working;
    expect(workingStage(cache!)).toBe("Building");
    expect(workingNote(cache!)).toBe("its lead agent stopped · relaunch failed 3×");
    expect(workingNote(badge!)).toBe("PR #9653: in agent review");
    const json = JSON.stringify(liveNow().working.map(workingNote));
    expect(json).not.toContain("READY FOR HUMAN MERGE");
  });

  it("quotes the lead's own label only when nothing structured says anything", () => {
    const [vbt] = liveNow().working;
    const label = { ...vbt!, status: "deploying the preview", status_source: "lead" as const };
    expect(workingNoteQuoted(label)).toBe(true);
    expect(workingNoteQuoted({ ...label, problem: "its lead agent stopped" })).toBe(false);
    // An older server sent no status: the raw label is not shown instead.
    expect(workingNote({ ...vbt!, status: undefined, doing: "READY FOR HUMAN MERGE" })).toBeNull();
  });
});

describe("spend", () => {
  it("says the last 24 h and the rate", () => {
    expect(spendText(liveNow().spend)).toBe("$61.44 in the last 24 h · $3.10/h now");
    expect(spendText({ ...busyNow().spend, last_24h_usd: null, usd_per_h: null })).toBe(
      "No spend recorded in the last 24 h",
    );
  });

  it("shows the header's rate when the card has the live snapshot", () => {
    const spend = liveNow().spend;
    expect(spendText(spend, 4.2)).toBe("$61.44 in the last 24 h · $4.20/h now");
    // The header shows none (e.g. a reader who sees part of the factory): neither does the card.
    expect(spendText(spend, null)).toBe("$61.44 in the last 24 h");
    expect(spendText(spend, undefined)).toBe("$61.44 in the last 24 h · $3.10/h now");
  });

  it("calls out an idle factory that still spends, saying the rate once", () => {
    expect(idleSpendText(idleNow().spend)).toBe("Idle, but still spending $2.71/h");
    expect(spendText(idleNow().spend)).toBe("$65.04 in the last 24 h");
    expect(idleSpendText(busyNow().spend)).toBeNull();
  });
});

describe("health", () => {
  it("folds the problems of listed projects into one line and keeps the rest", () => {
    const summary = pausedNow();
    const split = splitHealth(summary.health.items, summary.working);
    expect(split.listed.map((i) => i.project ?? i.kind)).toEqual(["paused", "db-version"]);
    expect(attentionText(split.attention)).toBe("1 project needs attention: cache-warm");
    // A project the card does not list keeps its own line.
    const unlisted = splitHealth(summary.health.items, []);
    expect(unlisted.attention).toEqual([]);
    expect(unlisted.listed.map((i) => i.project ?? i.kind)).toEqual([
      "paused",
      "cache-warm",
      "db-version",
    ]);
  });

  it("drops a project's line when the wait that holds it is listed", () => {
    const summary = pausedNow();
    const d2 = summary.waiting.find((i) => i.ref === "D-2")!;
    expect(d2.holds).toBe(true);
    const split = splitHealth(summary.health.items, summary.working, [d2]);
    expect(split.listed.map((i) => i.project ?? i.kind)).toEqual(["paused"]);
    expect(attentionText(split.attention)).toBe("1 project needs attention: cache-warm");
  });
});

describe("lead and check-in", () => {
  it("frames Atlas's one-liner as what it said, and how fresh it is", () => {
    const lead = liveNow().lead!;
    expect(leadWho(lead, ROUND3_NOW_S)).toBe("Atlas said 52m ago");
    expect(leadWho({ ...lead, since: null }, ROUND3_NOW_S)).toBe("Atlas said");
  });

  it("links the ids the note names, trimmed at a word", () => {
    const lead = liveNow().lead!;
    const parts = leadParts(lead);
    expect(parts.map((p) => p.text).join("")).toBe(lead.text);
    expect(parts.filter((p) => p.href).map((p) => p.text)).toEqual([
      "#9826",
      "#9827",
      "#9828",
      "#9829",
    ]);
    expect(lead.text.length).toBeLessThanOrEqual(140);
    expect(lead.text).toMatch(/[a-z]…$/);
    // An older server sent no parts: the whole note is one run of text.
    const old: StatusSummary["lead"] = { ...lead, parts: undefined };
    expect(leadParts(old!)).toEqual([{ text: lead.text, href: null, at: 0 }]);
  });

  it("says when Atlas checks in again, or that it is due", () => {
    const checkIn = busyNow().check_in!;
    expect(checkInText(checkIn, ROUND3_NOW_S)).toBe(
      `Atlas checks in again at ${clockText(checkIn.at, ROUND3_NOW_S)}`,
    );
    expect(checkInText(checkIn, checkIn.at + 60)).toBe("Atlas is due to check in");
  });

  it("says how old a card is that could not refresh", () => {
    const at = ROUND3_NOW_S - 300;
    expect(staleText(at, ROUND3_NOW_S)).toBe(
      `Showing ${clockText(at, ROUND3_NOW_S)} — couldn’t refresh`,
    );
  });
});
