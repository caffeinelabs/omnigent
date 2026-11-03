import { describe, expect, it } from "vitest";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { roomCandidateList } from "./roomCandidates";

/** The round-3 factory with Scout seated (discovery runs; sample). */
function withScout(): FactorySnapshot {
  const snapshot = round3Snapshot();
  const lead = snapshot.members.find((m) => m.role === "lead" && m.is_top_level)!;
  snapshot.members = [
    ...snapshot.members,
    { ...lead, session_id: `scout${"0".repeat(27)}`, title: "Scout", role: "scout" },
  ];
  return snapshot;
}

const inserts = (snapshot: FactorySnapshot) => roomCandidateList({ snapshot }).map((c) => c.insert);

describe("roomCandidateList", () => {
  it("offers @scout and #discovery while Scout is a member", () => {
    const offered = inserts(withScout());
    expect(offered).toContain("@scout");
    expect(offered).toContain("#discovery");
    expect(offered.indexOf("#discovery")).toBe(offered.indexOf("#improvements") + 1);
  });

  it("offers neither without discovery", () => {
    const offered = inserts(round3Snapshot());
    expect(offered).not.toContain("@scout");
    expect(offered).not.toContain("#discovery");
  });
});
