import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AUTONOMY_GROUPS } from "../settings/settingsModel";
import { SAMPLE_AUTONOMY_LEVELS } from "./__fixtures__/steerFixtures";
import { AutonomyLevels, levelDiff } from "./Budgets";

describe("AutonomyLevels", () => {
  it("explains every level in plain words and marks the one in force", () => {
    render(<AutonomyLevels levels={SAMPLE_AUTONOMY_LEVELS} current="standard" />);

    const standard = screen.getByTestId("steer-autonomy-level-standard");
    expect(standard).toHaveAttribute("data-current", "true");
    expect(standard).toHaveTextContent("(in force)");
    expect(within(standard).getByText(/scope paths, constraints, definition of done/)).toBeTruthy();
    // What every level shares is said once, under the levels.
    const every = screen.getByTestId("steer-autonomy-every-level");
    expect(within(every).getByText(/Cancelling, splitting or merging/)).toBeTruthy();
    expect(within(every).getByText(/carries out a person's answer to a decision/)).toBeTruthy();
    expect(within(standard).queryByText(/Cancelling, splitting or merging/)).toBeNull();
    expect(screen.getByTestId("steer-autonomy-level-careful")).not.toHaveAttribute("data-current");
    expect(screen.getByTestId("steer-autonomy-level-tryout")).toHaveTextContent(
      "inside an initiative a person already started",
    );
  });

  it("splits what each level adds from what every level shares", () => {
    const diff = levelDiff(SAMPLE_AUTONOMY_LEVELS);
    expect(diff.adds.careful?.atOnce).toEqual([]);
    expect(diff.adds.standard?.atOnce).toHaveLength(1);
    expect(diff.adds.tryout?.atOnce).toHaveLength(2);
    expect(diff.waits.some((line) => line.startsWith("This level itself"))).toBe(true);
  });

  it("says nothing on a server that does not explain its levels", () => {
    const { container } = render(<AutonomyLevels levels={[]} current="careful" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("puts the level first among the autonomy settings, owners only", () => {
    const [first] = AUTONOMY_GROUPS;
    expect(first?.fields.map((f) => f.path)).toEqual(["autonomy.level"]);
    expect(first?.fields[0]?.options?.map((o) => o.value)).toEqual([
      "careful",
      "standard",
      "tryout",
    ]);
  });
});
