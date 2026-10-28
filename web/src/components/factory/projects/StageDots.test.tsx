import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ROUND3_NOW_S, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { StageDots } from "./StageDots";

describe("StageDots", () => {
  it("says a project held on its dependencies is waiting, and on what", () => {
    const project = {
      ...round3Snapshot().projects[0]!,
      stage: "queued" as const,
      overlay: "waiting" as const,
      depends_on: ["api-version", "db-version"],
    };
    render(<StageDots project={project} nowS={ROUND3_NOW_S} />);
    const dots = screen.getByTestId(`stage-dots-${project.slug}`);
    expect(dots).toHaveTextContent("Waiting");
    expect(dots).toHaveAttribute("title", "Waiting on api-version, db-version");
  });

  it("names the stage otherwise", () => {
    const project = { ...round3Snapshot().projects[0]!, stage: "review" as const, overlay: null };
    render(<StageDots project={project} nowS={ROUND3_NOW_S} />);
    expect(screen.getByTestId(`stage-dots-${project.slug}`)).not.toHaveTextContent("Waiting");
  });
});
