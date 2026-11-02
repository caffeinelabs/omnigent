import { describe, expect, it } from "vitest";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { betProposal, requestedFromOpportunity, sampleBet } from "./__fixtures__/betFixtures";
import {
  betOrigin,
  caseCopy,
  caseState,
  costText,
  expectedText,
  flagText,
  killText,
  numberText,
  readoutText,
  waitsForCosign,
} from "./betModel";

describe("bet words", () => {
  it("says the range, the readout days and the flag in plain words", () => {
    expect(expectedText({ direction: "up", low: 0.01, high: 0.04, unit: "share" })).toBe(
      "expects up 0.01 to 0.04 share",
    );
    expect(expectedText({ direction: "down", low: 5, high: 2, unit: "pp" })).toBe(
      "expects down 2 to 5 pp",
    );
    expect(numberText(1 / 3)).toBe("0.3333");
    expect(readoutText([28, 7])).toBe("days 7 and 28 after release");
    expect(readoutText([28])).toBe("day 28 after release");
    expect(readoutText([7, 14, 28])).toBe("days 7, 14 and 28 after release");
    expect(readoutText([])).toBe("no readout day");
    expect(flagText({ key: "share-draft" })).toBe("share-draft, off by default");
    expect(flagText({ key: "a", convention: "kebab-case" })).toBe("a, off by default (kebab-case)");
    expect(flagText({ none: "no flags yet (sample)" })).toBe("none: no flags yet (sample)");
    expect(killText({ state: "no lift (sample)", date: "day 28" })).toBe(
      "no lift (sample) · by day 28",
    );
    expect(costText({ agent_usd: 120, human_review_min: 45, risk: "low" })).toBe(
      "$120 agent · 45 min review · low risk",
    );
    expect(costText(null)).toBeNull();
    expect(costText({})).toBeNull();
  });

  it("names where the bet comes from", () => {
    expect(betOrigin(betProposal("waiting"))).toBe("from O-12 · under G-1");
    const noGoal = { ...betProposal("waiting"), bet: sampleBet({ goal: null }), opportunity: null };
    expect(betOrigin(noGoal)).toBe("");
  });
});

describe("the case against", () => {
  it.each([
    ["cosigning", "waiting", true, "Case against · not written yet"],
    ["waiting", "written", false, "Case against · Lead B · Sentinel"],
    ["alone", "missing", false, "Case against · none"],
    ["applied", "written", false, "Case against · Lead B · Sentinel"],
  ] as const)("%s reads %s", (fixture, state, waits, heading) => {
    const proposal = betProposal(fixture);
    expect(caseState(proposal)).toBe(state);
    expect(waitsForCosign(proposal)).toBe(waits);
    expect(caseCopy(proposal, round3Snapshot()).heading).toBe(heading);
  });

  it("does not hold back work without a bet", () => {
    expect(waitsForCosign({ ...requestedFromOpportunity(), status: "awaiting_cosign" })).toBe(
      false,
    );
  });
});
