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
  tier2Plain,
  trialProgress,
  verdictText,
} from "./improvementsModel";
import { rowsOf } from "./ProposeChangeDialog";
import { ScorecardLine, ScorecardTable } from "./Scorecard";

const FID = "f4f4ba9c77725331b12f0fa7072dc4d3";

function byRef(ref: string) {
  return learnProposals().find((p) => p.ref === ref)!;
}

describe("improvements model", () => {
  it("says why Tier 2 is off without server setting names", () => {
    expect(tier2Plain("OMNIGENT_FACTORY_SMITH is not 1")).toBe("not switched on for this server");
    expect(tier2Plain("no factory GitHub identity configured (OMNIGENT_FACTORY_SMITH_LOGIN)")).toBe(
      "no factory GitHub identity configured",
    );
    expect(tier2Plain("caffeinelabs/omnigent#staging is not protected")).toBe(
      "caffeinelabs/omnigent#staging is not protected",
    );
    expect(tier2Plain(null)).toBeNull();
  });

  it("edits every op of a proposal, a rule keeping its project and id", () => {
    const rows = rowsOf(
      [
        { op: "replace", path: "cadence.lead", value: 7200 },
        { op: "add", path: "rules", value: { role: "ic", project: "api-version", text: "T" } },
        { op: "replace", path: "rules.R-3", value: { text: "Shorter." } },
        { op: "remove", path: "rules.R-4" },
      ],
      {
        rules: [
          { id: "R-3", role: "pl", project: "db", text: "Long.", origin: null, state: "active" },
        ],
      },
    );
    expect(rows).toMatchObject([
      { kind: "setting", path: "cadence.lead", value: "7200" },
      { kind: "rule", op: "add", path: "rules", role: "ic", project: "api-version", text: "T" },
      { kind: "rule", op: "replace", path: "rules.R-3", ruleId: "R-3", role: "pl", project: "db" },
      { kind: "fixed", change: { op: "remove", path: "rules.R-4" }, label: "Remove rule R-4" },
    ]);
  });

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
    expect(effectText(byRef("C-14"))).toBe("heartbeat $ / day ↓ by $0.30");
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
    expect(verdictText(card)).toBe("on track to keep · 31 of 24 heartbeat hours");
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
    expect(verdictText(card)).toBe("reverted · guard regressed: human prompts / day");
  });
});
