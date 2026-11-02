import type { Meta, StoryObj } from "@storybook/react-vite";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { LIVE_RUN_NOW_S, liveRunSnapshot } from "../__fixtures__/liveRun";
import { PHONE } from "../ui/uiStoryFixtures";
import { BoardFrame } from "./boardStoryKit";
import { HealthStrip } from "./HealthStrip";

const meta = {
  title: "Factory/Board/Health strip",
  component: HealthStrip,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof HealthStrip>;

export default meta;
type Story = StoryObj<typeof meta>;

function strip(snapshot: FactorySnapshot, theme: "light" | "dark" = "light"): Story {
  return {
    args: { snapshot, nowS: LIVE_RUN_NOW_S },
    render: (args) => (
      <BoardFrame theme={theme}>
        <HealthStrip {...args} />
      </BoardFrame>
    ),
  };
}

/** The live run: every part ok, one lead's launch mode drifted. */
export const LiveRun = strip(liveRunSnapshot());

/** (sample) A deployment without an always-deny list: the server says so, red. */
function withConfigProblem(): FactorySnapshot {
  const snapshot = liveRunSnapshot();
  snapshot.health.config = ["no always-deny repos configured (OMNIGENT_FACTORY_ALWAYS_DENY_REPOS)"];
  return snapshot;
}

export const ConfigProblem = strip(withConfigProblem());

export const ConfigProblemDark = strip(withConfigProblem(), "dark");

export const ConfigProblemPhone: Story = { ...strip(withConfigProblem()), ...PHONE };
