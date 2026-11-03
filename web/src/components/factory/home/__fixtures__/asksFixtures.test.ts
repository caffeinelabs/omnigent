import { describe, expect, it } from "vitest";
import type { AsksResponse } from "@/lib/factoryAsksApi";
import { round3IssueList } from "../../agents/__fixtures__/opsFixtures";
import { roomProposals } from "../../room/__fixtures__/roomFixtures";
import { ROUND3_NOW_S, round3Snapshot } from "../../shell/__fixtures__/shellFixtures";
import serverJson from "./asks.round3.json";
import { asksOf } from "./asksFixtures";

// What the server's builder says over the round-3 snapshot, per viewer role
// (written by tests/server/factory/test_asks_dedupe.py).
const SERVER = serverJson as unknown as { now_s: number; cases: Record<string, AsksResponse> };

describe("asksOf", () => {
  it("reads the round-3 snapshot at the server's time", () => {
    expect(SERVER.now_s).toBe(ROUND3_NOW_S);
  });

  it.each(["creator", "operator", "viewer"] as const)(
    "says what the server says for the round-3 %s",
    (role) => {
      const built = asksOf(round3Snapshot(role), {
        incidents: round3IssueList(),
        proposals: roomProposals(),
      });
      expect(built).toEqual(SERVER.cases[role]);
    },
  );
});
