import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import { LIVE_RUN_NOW_S } from "../__fixtures__/liveRun";
import {
  PREVIEW_STATES,
  type PreviewScenario,
  previewScenario,
} from "./__fixtures__/boardFixtures";
import { BoardFrame } from "./boardStoryKit";
import { PreviewPill } from "./PreviewPill";

function Pill({ state }: { state: PreviewScenario }) {
  const { pr, project } = previewScenario(state);
  return <PreviewPill pr={pr} project={project} nowS={LIVE_RUN_NOW_S} />;
}

function pill(state: PreviewScenario): Story {
  return {
    render: () => (
      <BoardFrame width={520}>
        <Pill state={state} />
      </BoardFrame>
    ),
  };
}

const meta = {
  title: "Factory/Board/Preview pill",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

function Gallery({ theme }: { theme: "light" | "dark" }) {
  return (
    <BoardFrame width={520} theme={theme}>
      {PREVIEW_STATES.map((state) => (
        <div key={state} className="flex items-center gap-3">
          <span className="w-24 shrink-0 font-mono text-sm text-muted-foreground">{state}</span>
          <Pill state={state} />
        </div>
      ))}
    </BoardFrame>
  );
}

export const AllStates: Story = { render: () => <Gallery theme="light" /> };
export const AllStatesDark: Story = { render: () => <Gallery theme="dark" /> };

export const ReadyAtHead = pill("ready-head");
export const BehindHead = pill("behind-head");
export const Deploying = pill("deploying");
export const Parked = pill("parked");
export const Gone = pill("gone");
export const DeployFailed = pill("failed");
export const NotYet = pill("none");
export const NoEnvNeeded = pill("no-env");

/** Hover: head, deployed and observed sha, namespace, resources and URLs. */
export const Details: Story = {
  ...pill("behind-head"),
  play: async ({ canvasElement }) => {
    const pillEl = await within(canvasElement.ownerDocument.body).findByTestId("preview-pill-9630");
    await userEvent.hover(pillEl);
  },
};
