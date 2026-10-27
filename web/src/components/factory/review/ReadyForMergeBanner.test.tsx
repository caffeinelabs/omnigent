import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LIVE_RUN_NOW_S, liveRunSnapshot } from "../__fixtures__/liveRun";
import { renderBoard } from "../board/__fixtures__/renderBoard";
import { ProjectCard } from "../board/ProjectCard";
import { ReadyForMergeBanner } from "./ReadyForMergeBanner";
import { reviewScenario } from "./__fixtures__/reviewFixtures";

function card(mutate?: (s: ReturnType<typeof liveRunSnapshot>) => void) {
  const snapshot = liveRunSnapshot();
  mutate?.(snapshot);
  const project = snapshot.projects.find((p) => p.slug === "api-version")!;
  const onReview = vi.fn();
  renderBoard(
    <ProjectCard snapshot={snapshot} project={project} nowS={LIVE_RUN_NOW_S} onReview={onReview} />,
  );
  return { card: screen.getByTestId("project-card-api-version"), onReview };
}

describe("ReadyForMergeBanner", () => {
  it("leads a ready PR on its card: gates, Sentinel, demo thumbnail, review and merge", () => {
    const { card: el, onReview } = card();
    const banner = within(el).getByTestId("ready-banner-9630");
    expect(banner).toHaveAttribute("data-variant", "card");
    expect(banner).toHaveTextContent(
      "Ready for your review · 7/7 gates · Sentinel verified 26m ago",
    );
    expect(within(banner).getByTestId("demo-thumb-9630")).toHaveAttribute("data-kind", "video");
    expect(within(banner).getByTestId("preview-pill-9630")).toHaveAttribute("data-head", "match");
    expect(within(banner).getByTestId("merge-link-9630")).toHaveAttribute(
      "href",
      "https://github.com/caffeinelabs/app/pull/9630#partial-pull-merging",
    );
    fireEvent.click(within(banner).getByTestId("review-open-9630"));
    expect(onReview).toHaveBeenCalledWith(9630);
    fireEvent.click(within(banner).getByTestId("demo-thumb-9630"));
    expect(onReview).toHaveBeenCalledTimes(2);
    expect(within(banner).getByTestId("i-merged-it-9630")).toBeInTheDocument();
    expect(within(banner).queryByTestId("ready-banner-demo-missing-9630")).toBeNull();
  });

  it("says 'Ready for your review' once: the card's tag stays short", () => {
    const { card: el } = card();
    expect(el.textContent!.match(/Ready for your review/g)).toHaveLength(1);
  });

  it("keeps each separator on the line of the part before it", () => {
    const { card: el } = card();
    const banner = within(el).getByTestId("ready-banner-9630");
    expect(banner.textContent).not.toMatch(/(^| )·/);
  });

  it("shows a backend demo without a still as an API tile", () => {
    const { snapshot } = reviewScenario("backend-api-demo");
    const project = snapshot.projects.find((p) => p.slug === "db-version")!;
    renderBoard(
      <ProjectCard
        snapshot={snapshot}
        project={project}
        nowS={LIVE_RUN_NOW_S}
        onReview={vi.fn()}
      />,
    );
    const thumb = within(screen.getByTestId("ready-banner-9631")).getByTestId("demo-thumb-9631");
    expect(thumb).toHaveAttribute("data-kind", "api");
    expect(thumb).toHaveTextContent("API · 4 calls");
  });

  it("warns when a caffeine-ux change has no demo", () => {
    const { card: el } = card((s) => {
      const pr = s.prs.find((p) => p.number === 9630)!;
      pr.demo = null;
    });
    expect(within(el).getByTestId("ready-banner-demo-missing-9630")).toHaveTextContent(
      "No demo, and this PR changes caffeine-ux",
    );
  });

  it("stays out of the way of a paused project", () => {
    const { card: el } = card((s) => {
      const project = s.projects.find((p) => p.slug === "api-version")!;
      project.overlay = "paused";
    });
    expect(within(el).queryByTestId("ready-banner-9630")).toBeNull();
    expect(within(el).getByTestId("review-open-9630")).toBeInTheDocument();
  });

  it("renders nothing for a PR that is not ready", () => {
    const pr = liveRunSnapshot().prs.find((p) => p.number === 9631)!;
    renderBoard(<ReadyForMergeBanner pr={pr} nowS={LIVE_RUN_NOW_S} />);
    expect(screen.queryByTestId("drawer-ready-banner-9631")).toBeNull();
  });

  it("reads as one line in drawers", () => {
    const pr = liveRunSnapshot().prs.find((p) => p.number === 9630)!;
    renderBoard(<ReadyForMergeBanner pr={pr} nowS={LIVE_RUN_NOW_S} />);
    expect(screen.queryByTestId("ready-banner-9630")).toBeNull();
    const banner = screen.getByTestId("drawer-ready-banner-9630");
    expect(banner).toHaveAttribute("data-variant", "line");
    expect(banner).toHaveTextContent("Ready for human merge · 7/7 gates");
    expect(within(banner).queryByRole("button")).toBeNull();
  });
});
