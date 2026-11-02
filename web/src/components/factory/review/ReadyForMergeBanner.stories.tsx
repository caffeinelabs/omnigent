import type { Meta, StoryObj } from "@storybook/react-vite";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { liveRunSnapshot } from "../__fixtures__/liveRun";
import { BoardFrame } from "../board/boardStoryKit";
import { ProjectCard } from "../board/ProjectCard";
import { FactoryNowContext } from "../factoryClock";
import { ReadyForMergeBanner } from "./ReadyForMergeBanner";
import { reviewScenario } from "./__fixtures__/reviewFixtures";
import { SAMPLE_DEMO_PATHS, sampleProduct } from "./__fixtures__/sampleProduct";
import { PHONE_390, withReviewMedia } from "./reviewStoryKit";

const meta = {
  title: "Factory/Review/Ready banner",
  component: ReadyForMergeBanner,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof ReadyForMergeBanner>;

export default meta;
type Story = StoryObj<typeof meta>;

function onCard(snapshot: FactorySnapshot, slug: string, theme: "light" | "dark" = "light"): Story {
  const project = snapshot.projects.find((p) => p.slug === slug)!;
  const pr = snapshot.prs.find((p) => project.pr_ids.includes(p.id))!;
  return {
    args: { pr },
    render: () => (
      <BoardFrame theme={theme}>
        <ProjectCard
          snapshot={snapshot}
          project={project}
          nowS={snapshot.server_time}
          onReview={() => {}}
        />
      </BoardFrame>
    ),
  };
}

/** The ready PR on its card: gates, Sentinel, demo thumbnail, review and merge. */
export const OnCard = onCard(withReviewMedia(liveRunSnapshot()), "api-version");

export const OnCardDark = onCard(withReviewMedia(liveRunSnapshot()), "api-version", "dark");

/** (sample) #9630 with no demo, under a product profile whose *demoPaths* need one. */
function withoutDemo(demoPaths: string[], mergePolicy: string | null = null): FactorySnapshot {
  const snapshot = withReviewMedia(liveRunSnapshot());
  snapshot.factory.product = sampleProduct({ demo_paths: demoPaths, merge_policy: mergePolicy });
  snapshot.prs.find((p) => p.number === 9630)!.demo = null;
  return snapshot;
}

/**
 * The PR touches a path the product profile says needs a demo, and has
 * none: the banner warns before merging and names the paths.
 */
export const DemoPathMatch = onCard(withoutDemo(SAMPLE_DEMO_PATHS), "api-version");

export const DemoPathMatchDark = onCard(withoutDemo(SAMPLE_DEMO_PATHS), "api-version", "dark");

export const DemoPathMatchPhone: Story = {
  ...onCard(withoutDemo(SAMPLE_DEMO_PATHS), "api-version"),
  ...PHONE_390,
};

/** The profile's paths that need a demo are elsewhere: no warning, whatever the PR changes. */
export const NoDemoPathMatch = onCard(withoutDemo(["web/**"]), "api-version");

export const NoDemoPathMatchDark = onCard(withoutDemo(["web/**"]), "api-version", "dark");

/** The profile's own merge policy on the merge link's hover text. */
export const CustomMergePolicy = onCard(
  withoutDemo([], "Release managers merge into main after QA"),
  "api-version",
);

/** A backend PR whose demo is its API transcript (no video). */
export const BackendPr = onCard(
  withReviewMedia(reviewScenario("backend-api-demo").snapshot),
  "db-version",
);

// The live run's "now", so ages read as they did then.
const LIVE_RUN_NOW = new Date(liveRunSnapshot().server_time * 1000);

/** The one-line form the drawers use. */
export const Line: Story = {
  args: { pr: liveRunSnapshot().prs.find((p) => p.number === 9630)! },
  render: (args) => (
    <BoardFrame width={640}>
      <FactoryNowContext.Provider value={LIVE_RUN_NOW}>
        <ReadyForMergeBanner pr={args.pr} />
      </FactoryNowContext.Provider>
    </BoardFrame>
  ),
};
