import { describe, expect, it } from "vitest";
import type { FactorySnapshot, NotifiedView } from "@/lib/factoryApi";
import type { StatusSummary } from "@/lib/factoryStatusApi";
import { round3IssueList } from "../../agents/__fixtures__/opsFixtures";
import { ROUND3_NOW_S, round3Snapshot } from "../../shell/__fixtures__/shellFixtures";
import serverJson from "./nowSummary.round3.json";
import { statusSummaryOf } from "./nowFixtures";

// What the server's builder says over the round-3 snapshot, per viewer role,
// and over the live factory of 2 Nov (written by tests/server/factory/test_status_summary.py).
const SERVER = serverJson as unknown as {
  now_s: number;
  cases: Record<string, StatusSummary>;
  live: {
    snapshot: FactorySnapshot;
    notified: Record<string, NotifiedView[]>;
    owners: Record<string, string>;
    summary: StatusSummary;
  };
};

/** Everything but the 24 h spend, which the server reads from the cost ledger. */
function comparable(summary: StatusSummary) {
  const { last_24h_usd: _day, today_usd: _today, ...spend } = summary.spend;
  return { ...summary, spend };
}

describe("statusSummaryOf", () => {
  it("reads the round-3 snapshot at the server's time", () => {
    expect(SERVER.now_s).toBe(ROUND3_NOW_S);
  });

  it.each(["creator", "operator", "viewer"] as const)(
    "says what the server says for the round-3 %s",
    (role) => {
      const built = statusSummaryOf(round3Snapshot(role), { incidents: round3IssueList() });
      expect(comparable(built)).toEqual(comparable(SERVER.cases[role]!));
    },
  );

  it("says what the server says for the live factory: holds, start reasons, receipts, links", () => {
    const { snapshot, notified, owners, summary } = SERVER.live;
    const built = statusSummaryOf(snapshot, { notified, owners });
    expect(comparable(built)).toEqual(comparable(summary));
    expect(built.headline).toBe("5 things wait on you · 2 projects in progress · 2 queued");
  });
});
