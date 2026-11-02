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

describe("StageDots while queued", () => {
  const base = { ...round3Snapshot().projects[0]!, stage: "queued" as const, overlay: null };

  it("shows a starting PL with its step instead of a bare Queued", () => {
    const project = {
      ...base,
      start: {
        state: "starting" as const,
        phase: "connect",
        label: "Waiting for the agent to connect",
        since: ROUND3_NOW_S - 150,
      },
    };
    render(<StageDots project={project} nowS={ROUND3_NOW_S} />);
    const dots = screen.getByTestId(`stage-dots-${project.slug}`);
    expect(dots).toHaveTextContent("Starting PL2mWaiting for the agent to connect");
    expect(dots).toHaveAttribute("data-start", "starting");
    expect(dots).not.toHaveTextContent("Queued");
  });

  it("says how many projects wait ahead for a pod", () => {
    const project = {
      ...base,
      start: {
        state: "queued" as const,
        phase: "pod_cap",
        label: "All 12 pods in use",
        since: ROUND3_NOW_S - 60,
        ahead: 3,
      },
    };
    render(<StageDots project={project} nowS={ROUND3_NOW_S} />);
    const dots = screen.getByTestId(`stage-dots-${project.slug}`);
    expect(screen.getByTestId(`stage-detail-${project.slug}`)).toHaveTextContent(
      "3 projects ahead",
    );
    expect(dots).toHaveAttribute("title", "Queued · All 12 pods in use · 3 projects ahead");
  });
});
