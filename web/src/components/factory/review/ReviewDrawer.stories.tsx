import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import { scenarioApiRoutes } from "./__fixtures__/reviewFixtures";
import { ReviewDrawer } from "./ReviewDrawer";
import { ReviewStory, type ReviewStoryOptions } from "./reviewStoryKit";

const meta = {
  title: "Factory/Review drawer",
  component: ReviewDrawer,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof ReviewDrawer>;

export default meta;
type Story = StoryObj<typeof meta>;

function review(options: ReviewStoryOptions): Story {
  // The drawer reads everything from the cockpit page; its own args are unused here.
  return {
    args: { factoryId: "", pr: null, demo: null, onClose: () => {} },
    render: () => <ReviewStory {...options} />,
  };
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

/** The preview was parked to free a slot; Restore preview sends the control. */
export const PreviewParked = review({ state: "preview-parked" });

/** The preview still runs an older commit: amber "preview behind head". */
export const PreviewMismatch = review({ state: "preview-mismatch" });

/** GitHub reported the merge: the action bar becomes the merged banner. */
export const Merged = review({ state: "merged" });

/** A docs-only PR (factory:no-env): no preview, no demo needed. */
export const DocsOnly = review({ state: "docs-only" });

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

/** Phone: the drawer is full-screen, panels stack, the action buttons go full width. */
export const Mobile: Story = {
  ...review({ state: "ready-with-video" }),
  globals: { viewport: { value: "mobile2" } },
};

export const MobileDark: Story = {
  ...review({ state: "ready-with-video", theme: "dark" }),
  globals: { viewport: { value: "mobile2" } },
};
