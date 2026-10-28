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
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
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

/** api-version, ready: every section on one page (charter, progress, PRs, demos, …). */
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
export const Prs = page({ search: "#prs" });

export const PrsDark = page({ search: "#prs", theme: "dark" });

/** The docs project: one merged PR, folded to a line. */
export const PrsMerged = page({ slug: "docs-factory-envs", search: "#prs" });

/** A project still planning: no PRs yet. */
export const PrsEmpty = page({ slug: "db-version", search: "#prs", snapshot: planning() });

// ── Demos ──────────────────────────────────────────────────────

/** One card per PR with its newest demo; #9630 has an earlier FAIL run. */
export const Demos = page({ search: "#demos" });

export const DemosDark = page({ search: "#demos", theme: "dark" });

export const DemosMobile = phone(page({ search: "#demos" }));

/** The poster was clicked: the recording plays inline. */
export const DemoPlaying = page({ search: "#demos" }, async ({ canvasElement }) => {
  const screen = screenOf(canvasElement);
  await userEvent.click(await screen.findByTestId(`demo-play-${UX_PR_NUMBER}`));
  await screen.findByTestId(`demo-video-${UX_PR_NUMBER}`);
});

/** Steps unfolded: skip reasons and API calls per step. */
export const DemoSteps = page({ search: "#demos" }, async ({ canvasElement }) => {
  const screen = screenOf(canvasElement);
  await userEvent.click(await screen.findByTestId(`demo-steps-toggle-${UX_PR_NUMBER}`));
});

/** #9630's earlier FAIL run shown in its card, steps open on the failure. */
export const DemoEarlierRun = page({ search: "#demos" }, async ({ canvasElement }) => {
  const screen = screenOf(canvasElement);
  await userEvent.click(await screen.findByTestId("demo-earlier-9630"));
  await userEvent.click(await screen.findByTestId(`demo-run-${EARLIER_9630_DEMO_ID}`));
});

export const DemoEarlierRunDark = page(
  { search: "#demos", theme: "dark" },
  async ({ canvasElement }) => {
    const screen = screenOf(canvasElement);
    await userEvent.click(await screen.findByTestId("demo-earlier-9630"));
    await userEvent.click(await screen.findByTestId(`demo-run-${EARLIER_9630_DEMO_ID}`));
  },
);

/** Previews on, nothing recorded yet; #9631 waits for its preview to pass verify. */
export const DemosEmpty = page({ slug: "db-version", search: "#demos" });

export const DemosEmptyDark = page({ slug: "db-version", search: "#demos", theme: "dark" });

/** Previews and demos are off for the docs project. */
export const DemosPreviewsOff = page({ slug: "docs-factory-envs", search: "#demos" });

export const DemosLoading = page({ search: "#demos", demos: "pending" });

export const DemosError = page({ search: "#demos", demos: { error: 503 } });

/** An old `?review=` link on a project page lands on that PR's page. */
export const LegacyReviewLink = page({ search: `?review=${UX_PR_NUMBER}` });

/** An old `?tab=demos` link scrolls to the Demos section. */
export const LegacyTabLink = page({ search: "?tab=demos" });

// ── Previews & envs ────────────────────────────────────────────

/** The dev env's two truths, then each PR preview with its shas, resources, Open and Redeploy. */
export const Previews = page({ search: "#previews" });

export const PreviewsDark = page({ search: "#previews", theme: "dark" });

export const PreviewsMobile = phone(page({ search: "#previews" }));

/** db-version: #9631's preview runs an older commit; one slot free. */
export const PreviewsBehindHead = page({ slug: "db-version", search: "#previews" });

/** The docs project needs no env and has previews off. */
export const PreviewsOff = page({ slug: "docs-factory-envs", search: "#previews" });

// ── Activity ───────────────────────────────────────────────────

export const Activity = page({ search: "#activity" });

export const ActivityDark = page({ search: "#activity", theme: "dark" });

// ── Page states ────────────────────────────────────────────────

export const Loading = page({ routes: { [SNAPSHOT_PATH]: "pending" } });

export const LoadingDark = page({ routes: { [SNAPSHOT_PATH]: "pending" }, theme: "dark" });

/** The first read failed: an alert with Retry. */
export const LoadError = page({ routes: { [SNAPSHOT_PATH]: { error: 503 } } });

/** A slug this factory doesn't have (or whose PL the viewer can't open). */
export const NotOnBoard = page({ slug: "nope" });

/** Unknown factory, or one the viewer can't read. */
export const FactoryMissing = page({ routes: { [SNAPSHOT_PATH]: { error: 404 } } });

// ── Round 3: work graph, loops, people ─────────────────────────

/** db-version on the round-3 factory: tasks 2/5, a loop at 3/3, D-2 for Mia, Mia here. */
export const Round3 = page({ slug: "db-version", snapshot: round3Snapshot() });

export const Round3Dark = page({ slug: "db-version", snapshot: round3Snapshot(), theme: "dark" });

export const Round3Mobile = phone(page({ slug: "db-version", snapshot: round3Snapshot() }));

/** A viewer: the banner, Edit charter, Pause, Redeploy and the decision's options disabled. */
export const Viewer = page({ slug: "db-version", snapshot: round3Snapshot("viewer") });

export const ViewerDark = page({
  slug: "db-version",
  snapshot: round3Snapshot("viewer"),
  theme: "dark",
});

/** A long title, goal and scope wrap without pushing the page sideways. */
export const LongText = page({
  snapshot: (() => {
    const { snapshot } = projectScenario();
    const api = snapshot.projects.find((p) => p.slug === "api-version")!;
    api.title =
      "API version endpoint with a very long project title that keeps going past the header width";
    api.charter!.goal = `${api.charter!.goal} `.repeat(4).trim();
    api.charter!.scope_paths = [
      ...api.charter!.scope_paths,
      "services/caffeine-ux/src/settings/components/about/very/deep/nested/path/**",
    ];
    return snapshot;
  })(),
});

export const LongTextMobile = phone(LongText);
