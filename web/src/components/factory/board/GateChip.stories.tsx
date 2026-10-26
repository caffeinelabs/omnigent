import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import type { PrView } from "@/lib/factoryApi";
import { LIVE_RUN_NOW_S } from "../__fixtures__/liveRun";
import { GATE_STATES, gateScenario } from "./__fixtures__/boardFixtures";
import { BoardFrame } from "./boardStoryKit";
import { GateChip, shownGates } from "./GateChip";

function GateRow({ pr, label }: { pr: PrView; label?: string }) {
  return (
    <div className="flex flex-col gap-1">
      {label && <p className="font-mono text-sm text-muted-foreground">{label}</p>}
      <div className="flex flex-wrap items-center gap-1">
        {shownGates(pr).map((gate) => (
          <GateChip key={gate} pr={pr} gate={gate} nowS={LIVE_RUN_NOW_S} />
        ))}
      </div>
    </div>
  );
}

function gates(state: (typeof GATE_STATES)[number], theme: "light" | "dark" = "light"): Story {
  return {
    render: () => (
      <BoardFrame theme={theme} width={640}>
        <GateRow pr={gateScenario(state)} />
      </BoardFrame>
    ),
  };
}

const meta = {
  title: "Factory/Board/Gate chip",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

export const AllStates: Story = {
  render: () => (
    <BoardFrame width={640}>
      {GATE_STATES.map((state) => (
        <GateRow key={state} pr={gateScenario(state)} label={state} />
      ))}
    </BoardFrame>
  ),
};

export const AllStatesDark: Story = {
  render: () => (
    <BoardFrame width={640} theme="dark">
      {GATE_STATES.map((state) => (
        <GateRow key={state} pr={gateScenario(state)} label={state} />
      ))}
    </BoardFrame>
  ),
};

export const Pass = gates("pass");
export const Pending = gates("pending");
export const Fail = gates("fail");
export const Skip = gates("skip");
export const Unknown = gates("unknown");

/** The CI chip's evidence card (hover or focus). */
export const EvidenceOpen: Story = {
  ...gates("fail"),
  play: async ({ canvasElement }) => {
    const chip = await within(canvasElement.ownerDocument.body).findByTestId("gate-checks-9631");
    await userEvent.hover(chip);
  },
};
