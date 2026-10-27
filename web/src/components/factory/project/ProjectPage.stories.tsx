import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { ProjectPage } from "@/pages/factory/ProjectPage";
import { SNAPSHOT_PATH } from "../board/boardStoryKit";
import {
  EARLIER_9630_DEMO_ID,
  UX_PR_NUMBER,
  projectScenario,
} from "./__fixtures__/projectFixtures";
import { ProjectStory, type ProjectStoryOptions } from "./projectStoryKit";

function page(options: ProjectStoryOptions = {}, play?: Story["play"]): Story {
  return { render: () => <ProjectStory {...options} />, play };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

/** db-version back in planning, before its first PR. */
function planning(): FactorySnapshot {
  const { snapshot } = projectScenario();
  const db = snapshot.projects.find((p) => p.slug === "db-version")!;
  Object.assign(db, {
    stage: "planning",
    agent_stage: "planning",
    overlay: null,
    overlay_reason: null,
    claim_mismatch: false,
    pr_ids: [],
  });
  snapshot.prs = snapshot.prs.filter((pr) => pr.project_slug !== "db-version");
  return snapshot;
}

function screenOf(canvasElement: HTMLElement) {
  return within(canvasElement.ownerDocument.body);
}

const meta = {
  title: "Factory/Project page",
  component: ProjectPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof ProjectPage>;

export default meta;
type Story = StoryObj<typeof meta>;

// ── Overview ───────────────────────────────────────────────────

/** api-version, ready: charter with its settings, the team, decisions and the plan. */
export const Overview = page();

export const OverviewDark = page({ theme: "dark" });

export const OverviewMobile = phone(page());

/** db-version, blocked on D-2: the open decision answers in one click (first on a phone). */
export const OverviewBlocked = page({ slug: "db-version" });

export const OverviewBlockedDark = page({ slug: "db-version", theme: "dark" });

/** A link to withdrawn D-4: the Withdrawn / expired fold opens with it marked. */
export const OverviewClosedDecisions = page({ search: "?decision=D-4" });

export const OverviewClosedDecisionsDark = page({ search: "?decision=D-4", theme: "dark" });

export const OverviewClosedDecisionsMobile = phone(page({ search: "?decision=D-4" }));

// ── PRs ────────────────────────────────────────────────────────

/** Both PRs with gates, Sentinel, preview, demo and Review; #9630 ready for merge. */
export const Prs = page({ search: "?tab=prs" });

export const PrsDark = page({ search: "?tab=prs", theme: "dark" });

/** The docs project: one merged PR, folded to a line. */
export const PrsMerged = page({ slug: "docs-factory-envs", search: "?tab=prs" });

/** A project still planning: no PRs yet. */
export const PrsEmpty = page({ slug: "db-version", search: "?tab=prs", snapshot: planning() });

// ── Demos ──────────────────────────────────────────────────────

/** One card per PR with its newest demo; #9630 has an earlier FAIL run. */
export const Demos = page({ search: "?tab=demos" });

export const DemosDark = page({ search: "?tab=demos", theme: "dark" });

export const DemosMobile = phone(page({ search: "?tab=demos" }));

/** The poster was clicked: the recording plays inline. */
export const DemoPlaying = page({ search: "?tab=demos" }, async ({ canvasElement }) => {
  const screen = screenOf(canvasElement);
  await userEvent.click(await screen.findByTestId(`demo-play-${UX_PR_NUMBER}`));
  await screen.findByTestId(`demo-video-${UX_PR_NUMBER}`);
});

/** Steps unfolded: skip reasons and API calls per step. */
export const DemoSteps = page({ search: "?tab=demos" }, async ({ canvasElement }) => {
  const screen = screenOf(canvasElement);
  await userEvent.click(await screen.findByTestId(`demo-steps-toggle-${UX_PR_NUMBER}`));
});

/** #9630's earlier FAIL run shown in its card, steps open on the failure. */
export const DemoEarlierRun = page({ search: "?tab=demos" }, async ({ canvasElement }) => {
  const screen = screenOf(canvasElement);
  await userEvent.click(await screen.findByTestId("demo-earlier-9630"));
  await userEvent.click(await screen.findByTestId(`demo-run-${EARLIER_9630_DEMO_ID}`));
});

export const DemoEarlierRunDark = page(
  { search: "?tab=demos", theme: "dark" },
  async ({ canvasElement }) => {
    const screen = screenOf(canvasElement);
    await userEvent.click(await screen.findByTestId("demo-earlier-9630"));
    await userEvent.click(await screen.findByTestId(`demo-run-${EARLIER_9630_DEMO_ID}`));
  },
);

/** Previews on, nothing recorded yet; #9631 waits for its preview to pass verify. */
export const DemosEmpty = page({ slug: "db-version", search: "?tab=demos" });

export const DemosEmptyDark = page({ slug: "db-version", search: "?tab=demos", theme: "dark" });

/** Previews and demos are off for the docs project. */
export const DemosPreviewsOff = page({ slug: "docs-factory-envs", search: "?tab=demos" });

export const DemosLoading = page({ search: "?tab=demos", demos: "pending" });

export const DemosError = page({ search: "?tab=demos", demos: { error: 503 } });

/** Open review from a demo card: the review drawer over the project page. */
export const DemoReview = page({ search: `?tab=demos&review=${UX_PR_NUMBER}` });

// ── Previews & envs ────────────────────────────────────────────

/** The dev env's two truths, then each PR preview with its shas, resources, Open and Redeploy. */
export const Previews = page({ search: "?tab=previews" });

export const PreviewsDark = page({ search: "?tab=previews", theme: "dark" });

export const PreviewsMobile = phone(page({ search: "?tab=previews" }));

/** db-version: #9631's preview runs an older commit; one slot free. */
export const PreviewsBehindHead = page({ slug: "db-version", search: "?tab=previews" });

/** The docs project needs no env and has previews off. */
export const PreviewsOff = page({ slug: "docs-factory-envs", search: "?tab=previews" });

// ── Activity ───────────────────────────────────────────────────

export const Activity = page({ search: "?tab=activity" });

export const ActivityDark = page({ search: "?tab=activity", theme: "dark" });

// ── Page states ────────────────────────────────────────────────

export const Loading = page({ routes: { [SNAPSHOT_PATH]: "pending" } });

export const LoadingDark = page({ routes: { [SNAPSHOT_PATH]: "pending" }, theme: "dark" });

/** The first read failed: an alert with Retry. */
export const LoadError = page({ routes: { [SNAPSHOT_PATH]: { error: 503 } } });

/** A slug this factory doesn't have (or whose PL the viewer can't open). */
export const NotOnBoard = page({ slug: "nope" });

/** Unknown factory, or one the viewer can't read. */
export const FactoryMissing = page({ routes: { [SNAPSHOT_PATH]: { error: 404 } } });
