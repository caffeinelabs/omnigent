import type { ReactNode } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import type { DecisionView, ViewerRole } from "@/lib/factoryApi";
import { CockpitStory, DocumentTheme } from "../board/boardStoryKit";
import { DecisionCard } from "../decisions/DecisionCard";
import { agentDecision, answeredAndDelivered } from "../decisions/__fixtures__/decisionFixtures";
import { FactoryStoryFrame } from "../factoryStoryFixtures";
import { reviewScenario } from "../review/__fixtures__/reviewFixtures";
import { ReviewStory } from "../review/reviewStoryKit";
import { round3Events, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { learnRoutes } from "../insights/__fixtures__/learnFixtures";
import { CLOSURE_REASONS } from "./feedbackModel";
import { CalibrationButtons, DemoThumbs, WrapUpRating } from "./FeedbackWidgets";
import { PrRatingStandalone } from "./PrRating";
import { ReportProblemDialog } from "./ReportProblem";

const FID = round3Snapshot().factory.id;
const FEEDBACK = { "/v1/factory/feedback": { body: { feedback: { id: "fb1" } }, status: 201 } };

const meta = {
  title: "Factory/Feedback",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

function frame(
  children: ReactNode,
  options: { theme?: "light" | "dark"; width?: number } = {},
): Story {
  return {
    render: () => (
      <FactoryStoryFrame mode="viewport" theme={options.theme} routes={FEEDBACK}>
        <DocumentTheme theme={options.theme ?? "light"} />
        <div
          className="flex flex-col gap-6 overflow-y-auto p-6"
          style={{ maxWidth: options.width ?? 640 }}
        >
          {children}
        </div>
      </FactoryStoryFrame>
    ),
  };
}

function click(testId: string): Story["play"] {
  return async ({ canvasElement }) => {
    await userEvent.click(await within(canvasElement.ownerDocument.body).findByTestId(testId));
  };
}

/** A closed PR's decision: the answer needs a closure reason. */
function closedPrDecision(): DecisionView {
  return {
    ...agentDecision(),
    id: "7f6e5d4c3b2a19087f6e5d4c3b2a1908",
    ref: "D-21",
    number: 21,
    source: "pr_closed",
    asked_by_session_id: null,
    asked_by_title: null,
    question: "!9653 was closed without merge. Rework it or abandon the project's PR?",
    context: null,
    options: [
      {
        id: "rework",
        label: "Rework",
        description: null,
        recommended: true,
        action: null,
        href: null,
        target: null,
        reasons: [...CLOSURE_REASONS],
      },
      {
        id: "abandon",
        label: "Abandon",
        description: null,
        recommended: false,
        action: null,
        href: null,
        target: null,
        reasons: [...CLOSURE_REASONS],
      },
    ],
  };
}

// ── PR review: stars and reason tags ───────────────────────────

function prPage(role: ViewerRole = "creator", theme: "light" | "dark" = "light"): Story {
  return {
    render: () => (
      <ReviewStory
        state="ready-with-video"
        theme={theme}
        tweak={(snapshot) => {
          snapshot.viewer = round3Snapshot(role).viewer;
        }}
        routes={{ ...learnRoutes(FID), ...FEEDBACK }}
      />
    ),
  };
}

/** The PR page's review bar with "Rate (optional)": 4★ picked, its reason tags. */
export const PrReviewRating: Story = {
  ...prPage(),
  play: click("pr-rating-stars-4"),
};

export const PrReviewRatingDark: Story = {
  ...prPage("creator", "dark"),
  play: click("pr-rating-stars-2"),
};

export const PrReviewRatingMobile: Story = {
  ...prPage(),
  globals: { viewport: { value: "mobile2" } },
  play: click("pr-rating-stars-4"),
};

/** A merged PR rated on its own. */
export const PrRatingMerged = frame(
  <PrRatingStandalone factoryId={FID} pr={reviewScenario("merged").pr} />,
);

/** The merged PR's rating sent: the thanks line replaces the stars. */
export const PrRatingSent: Story = {
  ...PrRatingMerged,
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(await body.findByTestId("pr-rating-stars-4"));
    await userEvent.click(await body.findByTestId("pr-rating-send"));
    await body.findByTestId("pr-rating-sent");
  },
};

// ── Demo, decision, wrap-up, calibration ───────────────────────

export const DemoUseful = frame(
  <DemoThumbs factoryId={FID} demoId="demo-9630" project="api-version" headSha="57ab671" />,
);

export const DemoUsefulSent: Story = {
  ...frame(<DemoThumbs factoryId={FID} demoId="demo-9630" project="api-version" />),
  play: click("demo-thumbs-up"),
};

/** An answered question: "Unnecessary?" feeds the unneeded-questions metric. */
export const DecisionUnnecessary = frame(
  <div className="w-full max-w-80">
    <DecisionCard decision={answeredAndDelivered()} />
  </div>,
);

export const DecisionUnnecessarySent: Story = {
  ...DecisionUnnecessary,
  play: click("decision-unneeded-D-2"),
};

/** A closed PR: options wait for a closure reason. */
export const ClosureReason = frame(
  <div className="w-full max-w-96">
    <DecisionCard decision={closedPrDecision()} />
  </div>,
);

export const ClosureReasonDark = frame(
  <div className="w-full max-w-96">
    <DecisionCard decision={closedPrDecision()} />
  </div>,
  { theme: "dark" },
);

export const ClosureReasonPicking: Story = {
  ...ClosureReason,
  play: click("decision-closure-D-21"),
};

/** "Superseded" chosen: Rework and Abandon are enabled. */
export const ClosureReasonChosen: Story = {
  ...ClosureReason,
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(await body.findByTestId("decision-closure-D-21"));
    await userEvent.click(await body.findByRole("option", { name: /superseded/i }));
  },
};

export const WrapUp: Story = {
  ...frame(<WrapUpRating factoryId={FID} project="docs-factory-envs" />),
  play: click("wrap-up-stars-5"),
};

export const WrapUpLow: Story = {
  ...frame(<WrapUpRating factoryId={FID} project="docs-factory-envs" />, { theme: "dark" }),
  play: click("wrap-up-stars-2"),
};

export const Calibration = frame(
  <CalibrationButtons factoryId={FID} issueId="f7" label="ci_flaky" testId="calibrate-F-7" />,
);

export const CalibrationCorrecting: Story = {
  ...Calibration,
  play: click("calibrate-F-7-no"),
};

/** "Report a problem" on an agent. */
export const ReportProblem = frame(
  <ReportProblemDialog
    factoryId={FID}
    open
    onOpenChange={() => {}}
    target="member:9c572941"
    targetLabel="ic_codex:version-endpoint"
    project="api-version"
  />,
);

export const ReportProblemDark = frame(
  <ReportProblemDialog
    factoryId={FID}
    open
    onOpenChange={() => {}}
    target="pr:p9630"
    targetLabel="!9630"
    project="api-version"
  />,
  { theme: "dark" },
);

export const ReportProblemMobile: Story = {
  ...ReportProblem,
  globals: { viewport: { value: "mobile2" } },
};

/** A finished project's page with the wrap-up card. */
export const ProjectWrapUp: Story = {
  render: () => (
    <CockpitStory
      snapshot={round3Snapshot()}
      events={round3Events()}
      path="/projects/docs-factory-envs"
      routes={{ ...learnRoutes(FID), ...FEEDBACK }}
    />
  ),
};
