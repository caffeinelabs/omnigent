import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import { PrPage } from "@/pages/factory/PrPage";
import { SNAPSHOT_PATH } from "../board/boardStoryKit";
import { viewerOf } from "../shell/__fixtures__/shellFixtures";
import { scenarioApiRoutes } from "./__fixtures__/reviewFixtures";
import { ReviewStory, type ReviewStoryOptions } from "./reviewStoryKit";

const meta = {
  title: "Factory/PR page",
  component: PrPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof PrPage>;

export default meta;
type Story = StoryObj<typeof meta>;

function review(options: ReviewStoryOptions): Story {
  return { render: () => <ReviewStory {...options} /> };
}

const API_JSON = Object.keys(scenarioApiRoutes())[0]!;

/** #9630 ready: the demo video inline, 4 steps with screenshots, the API call, preview = head. */
export const ReadyWithVideo = review({ state: "ready-with-video" });

export const ReadyWithVideoDark = review({ state: "ready-with-video", theme: "dark" });

/** A backend-only PR: no video, the demo is its API transcript (one call expanded). */
export const BackendApiDemo: Story = {
  ...review({ state: "backend-api-demo" }),
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    const row = await body.findByTestId("review-api-call-1");
    await userEvent.click(within(row).getByRole("button"));
  },
};

export const BackendApiDemoDark: Story = {
  ...review({ state: "backend-api-demo", theme: "dark" }),
  play: BackendApiDemo.play,
};

/** A new head was pushed after the demo: the old demo shows under a warning. */
export const DemoOldHead = review({ state: "demo-old-head" });

/** Gates mostly green, the preview runs the head, the demo agent hasn't published yet. */
export const NoDemo = review({ state: "no-demo" });

/** No demo yet, and the project's demo agent is running: "Recording demo…". */
export const DemoRecording = review({ state: "demo-recording" });

/** The preview was parked to free a slot; Restore preview sends the control. */
export const PreviewParked = review({ state: "preview-parked" });

/** The PR's stable preview link landed here (via the old cockpit link) because the preview is parked. */
export const PreviewUnavailable = review({
  state: "preview-parked",
  previewUnavailable: true,
  legacy: true,
});

/** An old `?review=9630` link from a PR body lands on the PR page. */
export const LegacyReviewLink = review({ state: "ready-with-video", legacy: true });

/** The preview still runs an older commit: amber "preview behind head". */
export const PreviewMismatch = review({ state: "preview-mismatch" });

/** env_keeper is deploying the head: the panel names the commit it deploys, not one it ran. */
export const PreviewDeploying = review({ state: "preview-deploying" });

/** GitHub reported the merge: the action bar becomes the merged banner. */
export const Merged = review({ state: "merged" });

/** A docs-only PR (factory:no-env): no preview, no demo needed. */
export const DocsOnly = review({ state: "docs-only" });

/** The project has "Preview & demo" off: no preview, no demo, and the page says why. */
export const PreviewsOff = review({ state: "previews-off" });

/** The demo failed a step: the failure and console errors are called out. */
export const DemoFail = review({ state: "demo-fail" });

/** The demo's api.json is still loading. */
export const ApiCallsLoading = review({
  state: "ready-with-video",
  routes: { [API_JSON]: "pending" },
});

/** The demo's api.json couldn't be read. */
export const ApiCallsError = review({
  state: "ready-with-video",
  routes: { [API_JSON]: { error: 404, message: "demo file not found" } },
});

/** "Older demos" expanded: the PR's earlier heads from its demo.published events. */
export const OlderDemosExpanded: Story = {
  ...review({ state: "ready-with-video" }),
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(await body.findByTestId("review-older-demos"));
    await body.findByTestId("review-older-demo-list");
  },
};

/** Request changes open, a note typed. */
export const RequestChanges: Story = {
  ...review({ state: "ready-with-video" }),
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(await body.findByTestId("review-request-changes"));
    await userEvent.type(
      await body.findByTestId("review-feedback-text"),
      "Show the short sha next to the version, not the full one.",
    );
  },
};

/** Phone: the video first, then the gates, then Approve or Request changes. */
export const Mobile: Story = {
  ...review({ state: "ready-with-video" }),
  globals: { viewport: { value: "mobile2" } },
};

export const MobileDark: Story = {
  ...review({ state: "ready-with-video", theme: "dark" }),
  globals: { viewport: { value: "mobile2" } },
};

/** A viewer: Approve, Request changes, I merged it and Redeploy are disabled with who can. */
export const Viewer = review({
  state: "ready-with-video",
  tweak: (snapshot) => {
    snapshot.viewer = viewerOf("viewer");
  },
});

export const ViewerDark = review({
  state: "ready-with-video",
  theme: "dark",
  tweak: (snapshot) => {
    snapshot.viewer = viewerOf("viewer");
  },
});

/** A long title and branch wrap without pushing the page sideways. */
export const LongText = review({
  state: "ready-with-video",
  tweak: (snapshot) => {
    const pr = snapshot.prs[0]!;
    pr.title =
      "feat(api, ux): expose the deployed commit on GET /version and show it in Settings › About with a copyable short sha and a tooltip with the deploy time";
    pr.head_ref = "factory/api-version/version-endpoint-with-a-very-long-branch-name-r3";
  },
});

export const LongTextMobile: Story = {
  ...LongText,
  globals: { viewport: { value: "mobile2" } },
};

/** Loading the factory, then the page. */
export const Loading = review({
  state: "ready-with-video",
  routes: { [SNAPSHOT_PATH]: "pending" },
});
