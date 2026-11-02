import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import type { EnvView } from "@/lib/factoryApi";
import { LIVE_RUN_NOW_S, liveRunSnapshot } from "../__fixtures__/liveRun";
import { BoardFrame } from "./boardStoryKit";
import { DevEnvPill } from "./DevEnvPill";

const IDLE_NOTE = "idle: every PR waits for a human merge";
const NO_URLS = { app: null, api: null, minio: null, tilt_ui: null };
const GONE = { urls: NO_URLS, deployed: { sha: null, ref: null }, resources: [] };

/** The api-version dev env of the live run, with its report and probe changed. */
function devEnv(
  reported: Partial<NonNullable<EnvView["reported"]>>,
  probe?: Partial<NonNullable<EnvView["probe"]>>,
): EnvView {
  const env = liveRunSnapshot().projects[0]!.env!;
  return {
    reported: { ...env.reported!, ...reported },
    probe: probe ? { ...env.probe!, ...probe } : env.probe,
  };
}

const failedProbe = {
  app: { code: null, ms: 3000 },
  api: { code: null, ms: 3000 },
  health: "down" as const,
  fails: 3,
};

/** Dev env states and what the pill shows for each. */
const STATES: [string, EnvView][] = [
  ["ready", devEnv({})],
  // env_keeper moved it to a new base; the next probe judges the new env.
  ["re-reported", devEnv({ at: LIVE_RUN_NOW_S - 20 })],
  // DOWN reason=idle: the prober saw the namespace go before env_keeper reported.
  [
    "idle down",
    devEnv(
      { ...GONE, state: "down", note: IDLE_NOTE, at: LIVE_RUN_NOW_S - 300 },
      { ...failedProbe, at: LIVE_RUN_NOW_S - 420 },
    ),
  ],
  // UP after the idle stretch: the probe from before the DOWN is stale.
  [
    "coming back",
    devEnv(
      { ...GONE, state: "starting", at: LIVE_RUN_NOW_S - 60 },
      { ...failedProbe, at: LIVE_RUN_NOW_S - 900 },
    ),
  ],
  // The ready env stopped answering: the probe is newer than the report.
  ["unreachable", devEnv({}, { ...failedProbe, at: LIVE_RUN_NOW_S - 10 })],
  ["failed", devEnv({ state: "failed", at: LIVE_RUN_NOW_S - 30 })],
];

const meta = {
  title: "Factory/Board/Dev env pill",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

function Gallery({ theme }: { theme: "light" | "dark" }) {
  return (
    <BoardFrame width={520} theme={theme}>
      {STATES.map(([name, env]) => (
        <div key={name} className="flex min-w-0 items-center gap-3">
          <span className="w-28 shrink-0 font-mono text-sm text-muted-foreground">{name}</span>
          <DevEnvPill env={env} nowS={LIVE_RUN_NOW_S} />
        </div>
      ))}
    </BoardFrame>
  );
}

export const AllStates: Story = { render: () => <Gallery theme="light" /> };
export const AllStatesDark: Story = { render: () => <Gallery theme="dark" /> };

function idleDown(theme: "light" | "dark"): Story {
  return {
    render: () => (
      <BoardFrame width={520} theme={theme}>
        <DevEnvPill env={STATES[2]![1]} nowS={LIVE_RUN_NOW_S} />
      </BoardFrame>
    ),
    play: async ({ canvasElement }) => {
      const pill = await within(canvasElement.ownerDocument.body).findByTestId("dev-env-pill");
      await userEvent.hover(pill);
    },
  };
}

/** Hover: taken down while every PR waits for a merge; the old probe is marked as older. */
export const IdleDownDetails = idleDown("light");
export const IdleDownDetailsDark = idleDown("dark");
