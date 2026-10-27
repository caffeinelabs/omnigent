import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import type { DecisionView } from "@/lib/factoryApi";
import { LIVE_RUN_NOW_S } from "../__fixtures__/liveRun";
import { DocumentTheme } from "../board/boardStoryKit";
import { FactoryStoryFrame, type StoryRoutes } from "../factoryStoryFixtures";
import {
  agentDecision,
  answeredAndDelivered,
  answeredDecision,
  blockedApprovalDecision,
  lateQuestion,
  relaunchFailedDecision,
} from "./__fixtures__/decisionFixtures";
import { DecisionCard } from "./DecisionCard";

const meta = {
  title: "Factory/Decisions/Decision card",
  component: DecisionCard,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof DecisionCard>;

export default meta;
type Story = StoryObj<typeof meta>;

function answerPath(decision: DecisionView): string {
  return `/v1/factory/decisions/${decision.id}/answer`;
}

/** Cards at the rail's width (18 rem) and at the Inbox's, light or dark. */
function card(
  decision: DecisionView,
  options: { theme?: "light" | "dark"; routes?: StoryRoutes; highlighted?: boolean } = {},
): Story {
  return {
    args: { decision },
    render: () => (
      <FactoryStoryFrame mode="viewport" theme={options.theme} routes={options.routes}>
        <DocumentTheme theme={options.theme ?? "light"} />
        <div className="flex flex-wrap items-start gap-8 overflow-y-auto p-6">
          <div className="w-72 shrink-0">
            <DecisionCard decision={decision} highlighted={options.highlighted} />
          </div>
          <div className="w-full max-w-xl">
            <DecisionCard decision={decision} factoryName="Atlas factory" />
          </div>
        </div>
      </FactoryStoryFrame>
    ),
  };
}

async function pickFirstOption(canvasElement: HTMLElement, ref: string) {
  const body = within(canvasElement.ownerDocument.body);
  const [option] = await body.findAllByTestId(`decision-option-${ref}-0`);
  await userEvent.click(option!);
}

/** An agent's blocking question: options (★ recommended), context, free text. */
export const OpenBlocking = card(agentDecision());

export const OpenBlockingDark = card(agentDecision(), { theme: "dark" });

/** The supervisor's relaunch-failed decision: its options run server actions. */
export const ServerRelaunchFailed = card(relaunchFailedDecision());

export const ServerRelaunchFailedDark = card(relaunchFailedDecision(), { theme: "dark" });

/** A blocked approval: the recommended option is a link to the Inbox. */
export const ServerLinkOption = card(blockedApprovalDecision());

/** A question waiting 40 minutes: the wait time is red. */
export const WaitingLong = card(lateQuestion());

/** Answered with one click; the server hasn't confirmed yet. */
export const Answering: Story = {
  ...card(agentDecision(), { routes: { [answerPath(agentDecision())]: "pending" } }),
  play: ({ canvasElement }) => pickFirstOption(canvasElement, "D-2"),
};

/** Answered: the mail to the asker is queued. */
export const Delivering = card(answeredDecision());

/** The receipt: delivered to the asker, with the time. */
export const Delivered = card(answeredAndDelivered());

export const DeliveredDark = card(answeredAndDelivered(), { theme: "dark" });

/** Someone answered first: 409 names them. */
export const Conflict: Story = {
  ...card(agentDecision(), {
    routes: {
      [answerPath(agentDecision())]: {
        status: 409,
        body: {
          error: { code: "decision_not_open", message: "D-2 is already answered" },
          status: "answered",
          answered_by: "Ana",
          answered_at: LIVE_RUN_NOW_S - 10,
        },
      },
    },
  }),
  play: ({ canvasElement }) => pickFirstOption(canvasElement, "D-2"),
};

/** The answer POST failed: the options come back with the error. */
export const SendFailed: Story = {
  ...card(agentDecision(), {
    routes: { [answerPath(agentDecision())]: { error: 503, message: "mailer unavailable" } },
  }),
  play: ({ canvasElement }) => pickFirstOption(canvasElement, "D-2"),
};

/** `?decision=D-2`: highlighted in the rail. */
export const Highlighted = card(agentDecision(), { highlighted: true });
