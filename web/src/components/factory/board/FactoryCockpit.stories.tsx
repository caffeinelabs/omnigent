import type { Meta, StoryObj } from "@storybook/react-vite";
import { factoryQueryKey } from "@/hooks/useFactory";
import { FactoryPage } from "@/pages/factory/FactoryPage";
import {
  LIVE_RUN_FACTORY_ID,
  allDoneSnapshot,
  githubDisconnectedSnapshot,
  tenProjectsSnapshot,
} from "../__fixtures__/liveRun";
import { pageScenario, storySnapshot, withStoryMedia } from "./__fixtures__/boardFixtures";
import { CockpitStory, type CockpitStoryOptions, SNAPSHOT_PATH } from "./boardStoryKit";

/** The Projects page in its Cards view: the round-2 board, reused. */
function cockpit(options: CockpitStoryOptions = {}): Story {
  return {
    render: () => <CockpitStory path="/projects" search="?view=cards" {...options} />,
  };
}

const meta = {
  title: "Factory/Projects cards",
  component: FactoryPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof FactoryPage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The live run: the API PR ready with preview and demo, the DB PR building and blocked on D-2. */
export const Live = cockpit();

export const LiveDark = cockpit({ theme: "dark" });

/** Phone layout: cards stack, gates wrap 3×2, the bottom tabs take over. */
export const Mobile: Story = { ...cockpit(), globals: { viewport: { value: "mobile2" } } };

/** Both PRs ready: the factory waits only on the human. */
export const Ready = cockpit({ snapshot: pageScenario("ready") });

/** db-version blocked on a decision, with a claim that disagrees with the gates. */
export const Blocked = cockpit({ snapshot: pageScenario("blocked") });

/** Every project paused; previews kept up for review. */
export const Paused = cockpit({ snapshot: pageScenario("paused") });

/** A PL died and its relaunch failed; the supervisor tick is late. */
export const Failed = cockpit({ snapshot: pageScenario("failed") });

/** A third project whose PL sandbox is still coming up. */
export const QueuedProvisioning = cockpit({ snapshot: pageScenario("queued-provisioning") });

/** Every project merged: the board is just the Done group. */
export const AllDone = cockpit({ snapshot: withStoryMedia(allDoneSnapshot()) });

/** GitHub's token expired: gates are unknown and every PR offers Connect. */
export const GithubDisconnected = cockpit({
  snapshot: withStoryMedia(githubDisconnectedSnapshot()),
});

/** The push socket is down: the page says so and polls. */
export const Reconnecting = cockpit({ live: false });

export const TenProjects = cockpit({ snapshot: withStoryMedia(tenProjectsSnapshot()) });

/** A factory whose lead has not filed any project yet. */
export const NoProjects = cockpit({
  snapshot: (() => {
    const snapshot = storySnapshot();
    snapshot.projects = [];
    snapshot.prs = [];
    snapshot.decisions = [];
    return snapshot;
  })(),
});

export const Loading = cockpit({ routes: { [SNAPSHOT_PATH]: "pending" } });

/** The first read failed: an alert with Retry, no empty board. */
export const LoadError = cockpit({ routes: { [SNAPSHOT_PATH]: { error: 503 } } });

/** A refresh failed: the board stays under the alert, with its age. */
export const RefreshError = cockpit({
  routes: { [SNAPSHOT_PATH]: { error: 503 } },
  seed: (client) =>
    client.setQueryData(factoryQueryKey(LIVE_RUN_FACTORY_ID), storySnapshot(), {
      updatedAt: new Date(2026, 9, 26, 14, 2).getTime(),
    }),
  afterMount: (client) =>
    void client.invalidateQueries({ queryKey: factoryQueryKey(LIVE_RUN_FACTORY_ID) }),
});

/** `?decision=D-2` (the blocked card's link): the dock highlights it. */
export const DecisionHighlighted = cockpit({ search: "?view=cards&decision=D-2" });

/** The factory's Activity page. */
export const ActivityPage: Story = {
  render: () => <CockpitStory path="/activity" />,
};

export const ActivityPageDark: Story = {
  render: () => <CockpitStory path="/activity" theme="dark" />,
};
