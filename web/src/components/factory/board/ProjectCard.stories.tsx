import type { Meta, StoryObj } from "@storybook/react-vite";
import { LIVE_RUN_NOW_S } from "../__fixtures__/liveRun";
import { CARD_STATES, type CardState, cardScenario } from "./__fixtures__/boardFixtures";
import { BoardFrame } from "./boardStoryKit";
import { ProjectCard } from "./ProjectCard";

const noop = () => {};

function Card({ state }: { state: CardState }) {
  const { snapshot, project } = cardScenario(state);
  return (
    <ProjectCard
      snapshot={snapshot}
      project={project}
      nowS={LIVE_RUN_NOW_S}
      onOpenProject={noop}
      onReview={noop}
      onDecision={noop}
    />
  );
}

function card(state: CardState, theme: "light" | "dark" = "light"): Story {
  return {
    render: () => (
      <BoardFrame theme={theme}>
        <Card state={state} />
      </BoardFrame>
    ),
  };
}

const meta = {
  title: "Factory/Board/Project card",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Every state, top to bottom in board order. */
export const AllStates: Story = {
  render: () => (
    <BoardFrame>
      {CARD_STATES.filter((s) => s !== "done").map((state) => (
        <div key={state} className="flex flex-col gap-1">
          <p className="font-mono text-sm text-muted-foreground">{state}</p>
          <Card state={state} />
        </div>
      ))}
    </BoardFrame>
  ),
};

export const QueuedWaiting = card("queued-waiting");
export const QueuedProvisioning = card("queued-provisioning");
export const Planning = card("planning");
export const Building = card("building");
export const Review = card("review");
export const Verify = card("verify");
export const Ready = card("ready");
export const ReadyDark = card("ready", "dark");
export const Blocked = card("blocked");
export const BlockedDark = card("blocked", "dark");
export const Paused = card("paused");
export const Failed = card("failed");
export const Merged = card("merged");
export const PrClosed = card("pr-closed");
export const ClaimMismatch = card("claim-mismatch");
export const LaunchDrift = card("launch-drift");
