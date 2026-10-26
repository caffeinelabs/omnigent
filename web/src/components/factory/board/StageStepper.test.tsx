import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProvisioningTimeline, StageStepper, stepStates } from "./StageStepper";

describe("StageStepper", () => {
  it("marks passed, current and later steps", () => {
    expect(stepStates("review").map((s) => `${s.stage}:${s.state}`)).toEqual([
      "queued:done",
      "planning:done",
      "building:done",
      "review:current",
      "verify:todo",
      "ready:todo",
      "merged:todo",
    ]);
  });

  it("shows a finished project as past every step", () => {
    expect(stepStates("done").every((s) => s.state === "done")).toBe(true);
    expect(stepStates("merged").at(-1)).toEqual({ stage: "merged", state: "current" });
  });

  it("renders seven labelled steps with the current one marked", () => {
    render(<StageStepper stage="verify" overlay={null} running />);
    const stepper = screen.getByTestId("stage-stepper");
    const steps = within(stepper).getAllByRole("listitem");
    expect(steps.map((s) => s.textContent)).toEqual([
      "Queued",
      "Plan",
      "Build",
      "Review",
      "Verify",
      "Ready",
      "Merged",
    ]);
    expect(steps[4]).toHaveAttribute("aria-current", "step");
    expect(stepper).toHaveAttribute("data-running", "true");
    expect(steps[4]!.querySelector(".animate-ping")).not.toBeNull();
  });

  it("does not animate while the PL is idle", () => {
    render(<StageStepper stage="ready" overlay={null} running={false} />);
    expect(screen.getByTestId("stage-stepper").querySelector(".animate-ping")).toBeNull();
  });

  it("colors the current step by overlay", () => {
    render(<StageStepper stage="building" overlay="blocked" running={false} />);
    const current = screen
      .getByTestId("stage-stepper")
      .querySelector('[data-state="current"] .ring-2');
    expect(current).toHaveClass("bg-warning");
  });

  it("walks the provisioning steps to the brief", () => {
    render(<ProvisioningTimeline stage="briefing" />);
    const steps = within(screen.getByTestId("provisioning-timeline")).getAllByRole("listitem");
    expect(steps.map((s) => s.getAttribute("data-state"))).toEqual([
      "done",
      "done",
      "done",
      "done",
      "current",
    ]);
    expect(steps[4]).toHaveTextContent("Briefing the PL");
  });
});
