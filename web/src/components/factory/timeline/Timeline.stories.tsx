import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import { TimelinePage } from "@/pages/factory/TimelinePage";
import { IC_ROUTE_R2, PL_DB, scaleAgentsResponse } from "../agents/__fixtures__/opsFixtures";
import { CockpitStory, type CockpitStoryOptions, SNAPSHOT_PATH } from "../board/boardStoryKit";
import { ROUND3_NOW_S, round3Events, round3Snapshot } from "../shell/__fixtures__/shellFixtures";

const SPANS_PATH = `${SNAPSHOT_PATH}/spans`;

function timeline(options: CockpitStoryOptions = {}, play?: Story["play"]): Story {
  return {
    render: () => (
      <CockpitStory
        snapshot={round3Snapshot()}
        events={round3Events()}
        path="/timeline"
        {...options}
      />
    ),
    play,
  };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

const meta = {
  title: "Factory/Timeline",
  component: TimelinePage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof TimelinePage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The last 6 hours: PL, IC and env lanes, waits, T-4's r0..r2 ✗ ⛔, !9631's milestones. */
export const Live = timeline({ search: "?window=6h" });

export const LiveDark = timeline({ search: "?window=6h", theme: "dark" });

export const LiveMobile = phone(timeline({ search: "?window=6h" }));

export const LiveMobileDark = phone(timeline({ search: "?window=6h", theme: "dark" }));

/** The last hour (the default window). */
export const LastHour = timeline();

/** Replaying as of 2h ago: later spans gone, open ones cut at the rule; Graph as of then. */
export const Replay = timeline({ search: `?window=6h&at=${(ROUND3_NOW_S - 2 * 3600) * 1000}` });

export const ReplayDark = timeline({
  search: `?window=6h&at=${(ROUND3_NOW_S - 2 * 3600) * 1000}`,
  theme: "dark",
});

/** Show on Timeline from the fleet: two agents only. */
export const TwoAgents = timeline({ search: `?window=6h&agents=${IC_ROUTE_R2},${PL_DB}` });

/** One project. */
export const OneProject = timeline({ search: "?window=6h&focus=db-version" });

/** Heartbeats shown (hidden by default). */
export const WithHeartbeats = timeline({ search: "?window=6h" }, async ({ canvasElement }) => {
  const screen = within(canvasElement.ownerDocument.body);
  await userEvent.click(await screen.findByTestId("timeline-show-heartbeats"));
});

/** Flat: every lane in one group. */
export const Flat = timeline({ search: "?window=6h&group=none" });

/** A viewer reads the same Timeline (it has no levers). */
export const Viewer = timeline({ search: "?window=6h", snapshot: round3Snapshot("viewer") });

const scale = scaleAgentsResponse(60);

/** 60 agents: running ones each get a live lane. */
export const Scale60 = timeline({ search: "?window=6h", snapshot: scale.snapshot });

export const Empty = timeline({
  routes: { [SPANS_PATH]: { body: { data: [], has_more: false, next_since: null } } },
  snapshot: (() => {
    const snapshot = round3Snapshot();
    snapshot.members = snapshot.members.filter((m) => !m.running && m.role !== "project_lead");
    return snapshot;
  })(),
});

export const Loading = timeline({ routes: { [SPANS_PATH]: "pending" } });

export const LoadError = timeline({
  routes: { [SPANS_PATH]: { error: 503, message: "unavailable" } },
});

export const LoadErrorMobile = phone(LoadError);
