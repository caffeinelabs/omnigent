import type { Meta, StoryObj } from "@storybook/react-vite";
import { LIVE_RUN_NOW_S } from "../__fixtures__/liveRun";
import { launchStorySnapshot, startStorySnapshot } from "../projects/__fixtures__/startFixtures";
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

/** Queued and validating cards from the 2026-11-02 morning, each saying why. */
const START_SLUGS = [
  "ux-status-page",
  "ux-env-badge-copy",
  "api-rate-limit",
  "db-backfill",
  "api-request-id",
  "api-healthz",
  "ux-slash-focus",
  "ux-dark-toggle",
  "ux-theme-tokens",
  "settings-row",
  "validate-build-tier",
  "validate-preview-ttl",
];

function StartCards() {
  const snapshot = startStorySnapshot();
  return (
    <>
      {START_SLUGS.map((slug) => (
        <ProjectCard
          key={slug}
          snapshot={snapshot}
          project={snapshot.projects.find((p) => p.slug === slug)!}
          nowS={snapshot.server_time}
          onReview={noop}
          onDecision={noop}
        />
      ))}
    </>
  );
}

const PHONE_390 = {
  parameters: {
    viewport: {
      options: {
        phone390: {
          name: "Phone 390",
          styles: { width: "390px", height: "844px" },
          type: "mobile",
        },
      },
    },
  },
  globals: { viewport: { value: "phone390" } },
} satisfies Partial<Story>;

/**
 * "Starting PL" / "Restarting PL" with the step, "Queued" with the reason
 * (next pass, pods, Atlas), "Waiting on" a dependency, and Verify on merged PRs.
 */
export const QueuedReasons: Story = {
  render: () => (
    <BoardFrame>
      <StartCards />
    </BoardFrame>
  ),
};

export const QueuedReasonsDark: Story = {
  render: () => (
    <BoardFrame theme="dark">
      <StartCards />
    </BoardFrame>
  ),
};

export const QueuedReasonsPhone: Story = {
  ...PHONE_390,
  render: () => (
    <BoardFrame width={390}>
      <StartCards />
    </BoardFrame>
  ),
};

/** PL sandboxes starting, step by step, then a retry on a new sandbox and two failures. */
function LaunchCards() {
  const snapshot = launchStorySnapshot();
  return (
    <>
      {snapshot.projects.map((project) => (
        <ProjectCard
          key={project.slug}
          snapshot={snapshot}
          project={project}
          nowS={snapshot.server_time}
          onReview={noop}
          onDecision={noop}
        />
      ))}
    </>
  );
}

/**
 * "Starting PL" with what its sandbox waits on and for how long (a node, the
 * image, the clone, the containers, the host), "Retrying PL" on the server's
 * retry on a new sandbox, and "New sandbox" on a sandbox that failed.
 */
export const LaunchPhases: Story = {
  render: () => (
    <BoardFrame>
      <LaunchCards />
    </BoardFrame>
  ),
};

export const LaunchPhasesDark: Story = {
  render: () => (
    <BoardFrame theme="dark">
      <LaunchCards />
    </BoardFrame>
  ),
};

export const LaunchPhasesPhone: Story = {
  ...PHONE_390,
  render: () => (
    <BoardFrame width={390}>
      <LaunchCards />
    </BoardFrame>
  ),
};
