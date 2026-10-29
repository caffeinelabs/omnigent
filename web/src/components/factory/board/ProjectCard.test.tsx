import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SANDBOX_STAGE_LABELS } from "@/pages/ChatIndicators";
import { LIVE_RUN_NOW_S } from "../__fixtures__/liveRun";
import { CARD_STATES, type CardState, cardScenario } from "./__fixtures__/boardFixtures";
import { renderBoard } from "./__fixtures__/renderBoard";
import { requestChanges } from "../shell/__fixtures__/shellFixtures";
import { DoneProjectRow, ProjectCard } from "./ProjectCard";

function renderCard(state: CardState) {
  const { snapshot, project } = cardScenario(state);
  const handlers = { onReview: vi.fn(), onDecision: vi.fn() };
  renderBoard(
    <ProjectCard snapshot={snapshot} project={project} nowS={LIVE_RUN_NOW_S} {...handlers} />,
  );
  const card = screen.getByTestId(`project-card-${project.slug}`);
  return { card, project, snapshot, ...handlers };
}

const EXPECTED: Record<
  Exclude<CardState, "done">,
  { stage: string; overlay: string; current: string }
> = {
  "queued-waiting": { stage: "queued", overlay: "none", current: "queued" },
  "queued-provisioning": { stage: "queued", overlay: "none", current: "queued" },
  planning: { stage: "planning", overlay: "none", current: "planning" },
  building: { stage: "building", overlay: "none", current: "building" },
  review: { stage: "review", overlay: "none", current: "review" },
  verify: { stage: "verify", overlay: "none", current: "verify" },
  ready: { stage: "ready", overlay: "none", current: "ready" },
  blocked: { stage: "building", overlay: "blocked", current: "building" },
  paused: { stage: "ready", overlay: "paused", current: "ready" },
  failed: { stage: "building", overlay: "failed", current: "building" },
  merged: { stage: "merged", overlay: "none", current: "merged" },
  "pr-closed": { stage: "planning", overlay: "pr_closed", current: "planning" },
  "claim-mismatch": { stage: "building", overlay: "none", current: "building" },
  "launch-drift": { stage: "building", overlay: "none", current: "building" },
};

describe("ProjectCard", () => {
  it.each(CARD_STATES.filter((s) => s !== "done"))(
    "renders the %s state with its stage, overlay and stepper",
    (state) => {
      const { card } = renderCard(state);
      const expected = EXPECTED[state as Exclude<CardState, "done">];
      expect(card).toHaveAttribute("data-stage", expected.stage);
      expect(card).toHaveAttribute("data-overlay", expected.overlay);
      expect(within(card).getByTestId("stage-stepper")).toHaveAttribute(
        "data-current",
        expected.current,
      );
    },
  );

  it("queued without a PL waits for the lead and dims the stepper", () => {
    const { card } = renderCard("queued-waiting");
    expect(within(card).getByTestId("queued-note")).toHaveTextContent(
      "Waiting for Atlas to spawn the PL",
    );
    expect(within(card).getByTestId("stage-stepper")).toHaveClass("opacity-50");
    expect(within(card).queryByTestId("team-strip")).toBeNull();
  });

  it("queued with a PL shows the provisioning timeline with the sandbox labels", () => {
    const { card } = renderCard("queued-provisioning");
    const timeline = within(card).getByTestId("provisioning-timeline");
    expect(timeline).toHaveAttribute("data-step", "cloning");
    const steps = within(timeline).getAllByRole("listitem");
    expect(steps.map((s) => s.getAttribute("data-state"))).toEqual([
      "done",
      "current",
      "todo",
      "todo",
      "todo",
    ]);
    for (const stage of ["provisioning", "cloning", "starting", "connecting"]) {
      expect(timeline).toHaveTextContent(SANDBOX_STAGE_LABELS[stage]!);
    }
    expect(timeline).toHaveTextContent("Briefed");
  });

  it("planning without PRs says when the first PR usually lands", () => {
    const { card } = renderCard("planning");
    expect(card).toHaveTextContent("Planning · the first PR usually lands in 15–30 min");
    expect(within(card).getByTestId("pl-beacon-db-version")).toHaveTextContent(
      "Reading caffeine-db migrations",
    );
  });

  it("animates the current step while the PL is running", () => {
    const { card } = renderCard("building");
    expect(within(card).getByTestId("stage-stepper")).toHaveAttribute("data-running", "true");
    const { card: idle } = renderCard("ready");
    expect(within(idle).getByTestId("stage-stepper")).toHaveAttribute("data-running", "false");
  });

  it("ready shows the review banner, a green edge, the demo and a merge link", () => {
    const { card, onReview } = renderCard("ready");
    expect(card).toHaveTextContent("Ready for your review");
    expect(within(card).getByTestId("project-edge-api-version")).toHaveAttribute(
      "data-tone",
      "pass",
    );
    expect(within(card).getByTestId("demo-thumb-9630")).toHaveAttribute("data-kind", "video");
    expect(within(card).getByTestId("merge-link-9630")).toHaveAttribute(
      "href",
      "https://github.com/caffeinelabs/app/pull/9630#partial-pull-merging",
    );
    fireEvent.click(within(card).getByTestId("review-open-9630"));
    expect(onReview).toHaveBeenCalledWith(9630);
  });

  it("a human change request takes the PR out of ready until a new head", () => {
    const { snapshot, project } = cardScenario("ready");
    requestChanges(snapshot, 9630);
    renderBoard(
      <ProjectCard
        snapshot={snapshot}
        project={project}
        nowS={LIVE_RUN_NOW_S}
        onReview={vi.fn()}
        onDecision={vi.fn()}
      />,
    );
    const card = screen.getByTestId("project-card-api-version");
    expect(card).toHaveAttribute("data-stage", "building");
    expect(card).not.toHaveTextContent("Ready for your review");
    const row = within(card).getByTestId("pr-row-9630");
    expect(row).toHaveAttribute("data-ready", "false");
    expect(within(row).queryByTestId("merge-link-9630")).toBeNull();
    expect(within(row).getByTestId("changes-requested-9630")).toHaveTextContent(
      /^Changes requested by luka@caffeine\.ai$/,
    );
    expect(within(row).getByTestId("pr-not-ready-9630")).toHaveTextContent("waits on a new head");
    cleanup();

    // The IC pushed a new head: the old change request no longer holds it.
    snapshot.prs.find((p) => p.number === 9630)!.head_sha = "9".repeat(40);
    renderBoard(
      <ProjectCard
        snapshot={snapshot}
        project={project}
        nowS={LIVE_RUN_NOW_S}
        onReview={vi.fn()}
        onDecision={vi.fn()}
      />,
    );
    expect(screen.getByTestId("changes-requested-9630")).toHaveTextContent(
      "Changes requested by luka@caffeine.ai (old head)",
    );
    expect(screen.queryByTestId("pr-not-ready-9630")).toBeNull();
  });

  it("blocked links to its decision and has an amber edge", () => {
    const { card, onDecision } = renderCard("blocked");
    fireEvent.click(within(card).getByTestId("blocked-link-db-version"));
    expect(onDecision).toHaveBeenCalledWith("D-2");
    expect(within(card).getByTestId("project-edge-db-version")).toHaveAttribute(
      "data-tone",
      "warning",
    );
  });

  it("paused says who paused it and what stays up", () => {
    const { card } = renderCard("paused");
    expect(within(card).getByTestId("paused-note")).toHaveTextContent(
      "Paused by luka · 1 preview kept up · dev env down",
    );
    expect(card).not.toHaveTextContent("Ready for your review");
    expect(within(card).queryByTestId("project-edge-api-version")).toBeNull();
  });

  it("failed shows why with a red edge and a dead PL chip", () => {
    const { card } = renderCard("failed");
    expect(card).toHaveTextContent("PL down · relaunch failed 3×");
    expect(within(card).getByTestId("project-edge-db-version")).toHaveAttribute(
      "data-tone",
      "fail",
    );
    expect(
      within(card).getByTestId("team-member-a2fc64b9e92e54c8b2f0f78038df202d"),
    ).toHaveAttribute("data-status", "down");
  });

  it("merged folds the PR into one line", () => {
    const { card } = renderCard("merged");
    expect(card).toHaveTextContent("Merged");
    const row = within(card).getByTestId("pr-row-9630");
    expect(row).toHaveAttribute("data-state", "merged");
    expect(row).toHaveTextContent("merged by @luka · 5m ago");
    expect(within(card).queryByTestId("review-open-9630")).toBeNull();
  });

  it("a PR closed without merge says so", () => {
    const { card } = renderCard("pr-closed");
    expect(card).toHaveTextContent("PR closed without merge");
    expect(within(card).getByTestId("pr-row-9631")).toHaveTextContent("closed without merge");
  });

  it("flags a PL claim that disagrees with the gates", () => {
    const { card } = renderCard("claim-mismatch");
    expect(within(card).getByTestId("claim-mismatch-db-version")).toHaveTextContent(
      "PL says review · gates say building",
    );
    cleanup();
    const { card: honest } = renderCard("building");
    expect(within(honest).queryByTestId("claim-mismatch-db-version")).toBeNull();
  });

  it("shows an amber chip on a team member whose launch mode drifted", () => {
    const { card } = renderCard("launch-drift");
    const chip = within(card).getByTestId("launch-drift-chip-c849bd6aaca659b9b67b3b970caa2316");
    expect(chip).toHaveTextContent("old launch mode");
    expect(chip).toHaveAttribute(
      "title",
      "Launch mode differs from spec: permission mode auto → bypassPermissions",
    );
    expect(within(card).getAllByTestId(/^launch-drift-chip-/)).toHaveLength(1);
  });

  it("shows team, dev env, demos, cost and elapsed in the footer and links to the page", () => {
    const { card, snapshot } = renderCard("ready");
    const page = `/factory/${snapshot.factory.id}/projects/api-version`;
    expect(within(card).getByTestId("team-strip")).toHaveTextContent("PL");
    expect(within(card).getByTestId("dev-env-pill")).toHaveTextContent("main@8c1d2e0");
    expect(card).toHaveTextContent("$7.10");
    expect(within(card).getByTestId("project-open-api-version")).toHaveAttribute("href", page);
    expect(within(card).getByTestId("project-details-api-version")).toHaveAttribute("href", page);
    const demos = within(card).getByTestId("project-demos-api-version");
    expect(demos).toHaveTextContent("2 demos");
    expect(demos).toHaveAttribute("href", `${page}#demos`);
  });

  it("leaves the demos link out while no PR has a demo", () => {
    const { card } = renderCard("planning");
    expect(within(card).queryByTestId(/^project-demos-/)).toBeNull();
  });

  it("done is one line in the Done group, linking to the project page", () => {
    const { snapshot, project } = cardScenario("done");
    renderBoard(<DoneProjectRow snapshot={snapshot} project={project} nowS={LIVE_RUN_NOW_S} />);
    const row = screen.getByTestId("project-card-docs-factory-envs");
    expect(row).toHaveTextContent("merged #9632 by @luka");
    expect(row).toHaveAttribute(
      "href",
      `/factory/${snapshot.factory.id}/projects/docs-factory-envs`,
    );
  });
});
