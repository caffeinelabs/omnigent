import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LEARN_NOW_S, learnProposals, scorecard } from "../insights/__fixtures__/learnFixtures";
import {
  changeLines,
  effectText,
  evidenceHref,
  improvementCounts,
  issueText,
  onTrial,
  proposed,
  trialProgress,
  verdictText,
} from "./improvementsModel";
import { ScorecardLine, ScorecardTable } from "./Scorecard";

const FID = "f4f4ba9c77725331b12f0fa7072dc4d3";

function byRef(ref: string) {
  return learnProposals().find((p) => p.ref === ref)!;
}

describe("improvements model", () => {
  it("counts and sorts the tabs", () => {
    // The page reads Tier-1 kinds only (projects proposals live in the Room).
    const all = learnProposals().filter((p) => p.kind !== "projects");
    expect(improvementCounts(all)).toMatchObject({ trial: 1, proposed: 3, kept: 2, reverted: 1 });
    expect(onTrial(all).map((p) => p.ref)).toEqual(["C-14"]);
    expect(proposed(all).map((p) => p.ref)).toEqual(["C-15", "C-16", "C-18"]);
  });

  it("reads a trial's progress and overdue state", () => {
    const progress = trialProgress(byRef("C-14"), LEARN_NOW_S)!;
    expect(progress.elapsedH).toBe(31);
    expect(progress.totalH).toBe(48);
    expect(progress.overdue).toBe(false);
    expect(trialProgress(byRef("C-15"), LEARN_NOW_S)).toBeNull();
  });

  it("lists what a change does: settings before → after, rules added", () => {
    expect(changeLines(byRef("C-14"))).toEqual([
      { sign: "~", path: "cadence.project_lead", before: "7200", after: "10800" },
    ]);
    const [rule] = changeLines(byRef("C-15"));
    expect(rule).toMatchObject({ sign: "+", path: "rules", before: null });
    expect(rule!.after).toContain("role=project_lead");
    expect(effectText(byRef("C-14"))).toBe("heartbeat usd per day ↓ by 0.3");
  });

  it("links evidence ids to their pages", () => {
    expect(evidenceHref(FID, "F-9")).toBe(`/factory/${FID}/alerts/F-9`);
    expect(evidenceHref(FID, "INC-13")).toBe(`/factory/${FID}/alerts/13`);
    expect(evidenceHref(FID, "D-2")).toBe(`/factory/${FID}/decisions?decision=D-2`);
    expect(evidenceHref(FID, "C-11")).toBe(`/factory/${FID}/improvements/C-11`);
    expect(evidenceHref(FID, "!9631")).toBe(`/factory/${FID}/prs/9631`);
    expect(evidenceHref(FID, "#db-version")).toBe(`/factory/${FID}/projects/db-version`);
    expect(evidenceHref(FID, "span:abc")).toBeNull();
  });

  it("writes a code proposal as an issue body", () => {
    const text = issueText(byRef("C-16"), "Atlas factory", "https://omnigent.example");
    expect(text).toContain("## C-16: Promote rule R-3 into the IC skill text");
    expect(text).toContain("Tier 2 is off");
    expect(text).toContain("- C-9");
    expect(text).toContain(`https://omnigent.example/factory/${FID}/improvements/C-16`);
  });
});

describe("scorecard", () => {
  it("renders the target and every guard against the baseline", () => {
    const card = scorecard("C-14")!;
    expect(verdictText(card)).toBe("on track to keep · n=31/24");
    render(<ScorecardTable card={card} testId="card" />);

    const table = screen.getByTestId("card");
    expect(within(table).getAllByRole("row")).toHaveLength(1 + 1 + card.guards.length);
    expect(table).toHaveTextContent("heartbeat share (target ↓)");
    expect(table).toHaveTextContent("31%");
    expect(table).toHaveTextContent("19%");
    expect(table).toHaveTextContent("≥ baseline − 10 pp");
    expect(screen.getByTestId("card-verdict")).toHaveAttribute("data-outcome", "kept");
  });

  it("marks the regressed guard", () => {
    const card = scorecard("C-12")!;
    render(<ScorecardLine card={card} testId="line" />);

    expect(screen.getByTestId("line-guard-human_prompts_per_day")).toHaveAttribute(
      "data-status",
      "regressed",
    );
    expect(screen.getByTestId("line-guard-loop_exhaustion_rate")).toHaveAttribute(
      "data-status",
      "no_data",
    );
    expect(verdictText(card)).toBe("reverted · guard regressed: human_prompts_per_day");
  });
});
