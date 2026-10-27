import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { LIVE_RUN_NOW_S, allDoneSnapshot, tenProjectsSnapshot } from "../__fixtures__/liveRun";
import { pageScenario, storySnapshot } from "./__fixtures__/boardFixtures";
import { renderBoard } from "./__fixtures__/renderBoard";
import { prAnchorId, projectAnchorId } from "./boardModel";
import { FactoryBoard } from "./FactoryBoard";

function renderFactoryBoard(snapshot: FactorySnapshot = storySnapshot()) {
  const handlers = {
    onReview: vi.fn(),
    onDecision: vi.fn(),
    onDecisions: vi.fn(),
    onJump: vi.fn(),
  };
  renderBoard(<FactoryBoard snapshot={snapshot} nowS={LIVE_RUN_NOW_S} {...handlers} />);
  return handlers;
}

function cardSlugs(): string[] {
  return within(screen.getByTestId("factory-cards"))
    .getAllByRole("article")
    .map((card) => card.getAttribute("data-testid")!.replace("project-card-", ""));
}

describe("FactoryBoard", () => {
  it("orders cards ready first and folds finished projects into a closed Done group", () => {
    renderFactoryBoard(pageScenario("queued-provisioning"));
    expect(cardSlugs()).toEqual(["api-version", "db-version", "ux-env-badge"]);
    const done = screen.getByTestId("factory-done-group");
    expect(done).toHaveTextContent("Done (1)");
    expect(screen.queryByTestId("project-card-docs-factory-envs")).toBeNull();
    fireEvent.click(within(done).getByRole("button", { name: /Done/ }));
    expect(screen.getByTestId("project-card-docs-factory-envs")).toBeInTheDocument();
  });

  it("orders ten projects by tier: ready, blocked, in progress, queued, paused", () => {
    renderFactoryBoard(tenProjectsSnapshot());
    const stages = within(screen.getByTestId("factory-cards"))
      .getAllByRole("article")
      .map((card) => `${card.getAttribute("data-stage")}/${card.getAttribute("data-overlay")}`);
    expect(stages[0]).toMatch(/^ready\//);
    expect(stages[1]).toBe("ready/none");
    expect(stages.indexOf("building/blocked")).toBeLessThan(stages.indexOf("planning/none"));
    expect(stages.findIndex((s) => s.startsWith("queued"))).toBeGreaterThan(
      stages.indexOf("verify/none"),
    );
    expect(stages.at(-1)).toMatch(/\/paused$/);
  });

  it("counts projects per stage and filters the cards by one", () => {
    renderFactoryBoard();
    const strip = screen.getByTestId("factory-stage-strip");
    expect(within(strip).getByTestId("stage-filter-building")).toHaveAttribute("data-count", "1");
    expect(within(strip).getByTestId("stage-filter-ready")).toHaveAttribute("data-count", "1");
    expect(within(strip).getByTestId("stage-filter-merged")).toHaveAttribute("data-count", "1");
    expect(within(strip).getByTestId("stage-filter-review")).toBeDisabled();

    fireEvent.click(screen.getByTestId("stage-filter-building"));
    expect(screen.getByTestId("stage-filter-building")).toHaveAttribute("aria-pressed", "true");
    expect(cardSlugs()).toEqual(["db-version"]);
    expect(screen.queryByTestId("factory-done-group")).toBeNull();

    fireEvent.click(screen.getByTestId("stage-filter-merged"));
    expect(screen.queryByTestId("factory-cards")).toBeNull();
    expect(screen.getByTestId("project-card-docs-factory-envs")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("stage-filter-clear"));
    expect(cardSlugs()).toEqual(["api-version", "db-version"]);
  });

  it("counts what needs you and jumps to each target", () => {
    const { onJump, onDecisions } = renderFactoryBoard();
    const strip = screen.getByTestId("factory-needs-you");
    expect(strip).toHaveAttribute("data-count", "5");
    expect(within(strip).getByTestId("needs-you-ready")).toHaveTextContent("1 PR ready for review");
    expect(within(strip).getByTestId("needs-you-decisions")).toHaveTextContent(
      "2 blocking decisions",
    );
    expect(within(strip).getByTestId("needs-you-alerts")).toHaveTextContent(
      "Lead B · Sentinel is down",
    );
    expect(within(strip).getByTestId("needs-you-claims")).toHaveTextContent(
      "db-version: claim ≠ gates",
    );

    fireEvent.click(screen.getByTestId("needs-you-ready"));
    expect(onJump).toHaveBeenLastCalledWith(prAnchorId(9630));
    fireEvent.click(screen.getByTestId("needs-you-claims"));
    expect(onJump).toHaveBeenLastCalledWith(projectAnchorId("db-version"));
    fireEvent.click(screen.getByTestId("needs-you-decisions"));
    expect(onDecisions).toHaveBeenCalledOnce();
  });

  it("clears the stage filter when a chip's target is on a hidden card", () => {
    const { onJump } = renderFactoryBoard();
    onJump.mockImplementation((id: string) => {
      expect(document.getElementById(id)).not.toBeNull();
    });
    fireEvent.click(screen.getByTestId("stage-filter-building"));
    expect(screen.queryByTestId("pr-row-9630")).toBeNull();
    fireEvent.click(screen.getByTestId("needs-you-ready"));
    expect(onJump).toHaveBeenLastCalledWith(prAnchorId(9630));
    expect(screen.getByTestId("pr-row-9630")).toBeInTheDocument();
    expect(screen.getByTestId("stage-filter-building")).toHaveAttribute("aria-pressed", "false");

    // A visible target keeps the filter.
    fireEvent.click(screen.getByTestId("stage-filter-building"));
    fireEvent.click(screen.getByTestId("needs-you-claims"));
    expect(screen.getByTestId("stage-filter-building")).toHaveAttribute("aria-pressed", "true");
  });

  it("opens the Done group when every project is done", () => {
    renderFactoryBoard(allDoneSnapshot());
    expect(screen.getByTestId("factory-all-done")).toHaveTextContent(/projects are done/);
    expect(screen.getByTestId("factory-done-group")).toHaveAttribute("data-state", "open");
  });

  it("anchors the jump targets on the board", () => {
    renderFactoryBoard();
    expect(document.getElementById(prAnchorId(9630))).toBe(screen.getByTestId("pr-row-9630"));
    expect(document.getElementById(projectAnchorId("db-version"))).toBe(
      screen.getByTestId("project-card-db-version"),
    );
  });

  it("says when nothing needs you, and when there are no projects", () => {
    const snapshot = storySnapshot();
    snapshot.projects = [];
    snapshot.prs = [];
    snapshot.decisions = [];
    snapshot.members = snapshot.members.filter((m) => m.health !== "dead");
    renderFactoryBoard(snapshot);
    expect(screen.getByTestId("factory-needs-you")).toHaveTextContent("Nothing right now");
    expect(screen.getByTestId("factory-board-empty")).toHaveTextContent("No projects yet");
    expect(screen.queryByTestId("factory-stage-strip")).toBeNull();
  });

  it("folds the decisions rail under the strip on narrow screens", () => {
    const snapshot = storySnapshot();
    renderBoard(
      <FactoryBoard
        snapshot={snapshot}
        nowS={LIVE_RUN_NOW_S}
        onReview={vi.fn()}
        onDecision={vi.fn()}
        onDecisions={vi.fn()}
        onJump={vi.fn()}
        decisionsPanel={<p>rail</p>}
        decisionsExpanded
      />,
    );
    expect(screen.getByTestId("needs-you-decisions")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("needs-you-decisions-panel")).toHaveTextContent("rail");
  });

  it("slides in a PR that appears after the first paint", () => {
    const snapshot = storySnapshot();
    const later = storySnapshot();
    const extra = { ...later.prs[1]!, id: "new-pr", number: 9700 };
    later.prs.push(extra);
    later.projects.find((p) => p.slug === "db-version")!.pr_ids.push("new-pr");
    const props = {
      nowS: LIVE_RUN_NOW_S,
      onReview: vi.fn(),
      onDecision: vi.fn(),
      onDecisions: vi.fn(),
      onJump: vi.fn(),
    };
    const { rerender } = renderBoard(<FactoryBoard snapshot={snapshot} {...props} />);
    expect(screen.getByTestId("pr-row-9631")).not.toHaveClass("slide-in-from-top-1");
    rerender(<FactoryBoard snapshot={later} {...props} />);
    expect(screen.getByTestId("pr-row-9700")).toHaveClass("slide-in-from-top-1");
    expect(screen.getByTestId("pr-row-9631")).not.toHaveClass("slide-in-from-top-1");
  });

  it("pulses the merge glyph when a PR merges while the board is open", () => {
    const snapshot = storySnapshot();
    const merged = storySnapshot();
    Object.assign(
      merged.prs.find((pr) => pr.number === 9630)!,
      {
        state: "merged",
        ready: false,
        merged: { by: "luka", sha: "abc", at: LIVE_RUN_NOW_S },
      },
    );
    const props = {
      nowS: LIVE_RUN_NOW_S,
      onReview: vi.fn(),
      onDecision: vi.fn(),
      onDecisions: vi.fn(),
      onJump: vi.fn(),
    };
    const pulse = () =>
      screen
        .getByTestId("pr-row-9630")
        .querySelector(".animate-\\[user-msg-flash_800ms_ease-out\\]");
    const { rerender } = renderBoard(<FactoryBoard snapshot={snapshot} {...props} />);
    rerender(<FactoryBoard snapshot={merged} {...props} />);
    expect(screen.getByTestId("pr-row-9630")).toHaveAttribute("data-state", "merged");
    expect(pulse()).not.toBeNull();
  });
});
