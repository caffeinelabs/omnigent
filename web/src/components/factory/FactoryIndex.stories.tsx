import type { Meta, StoryObj } from "@storybook/react-vite";
import { FactoryIndexPage } from "@/pages/factory/FactoryIndexPage";
import { FACTORIES_QUERY_KEY } from "@/hooks/useFactory";
import {
  emptyFactoryList,
  factoryList,
  preflightAllFailing,
  preflightGithubDisconnected,
  tenProjectsFactoryList,
} from "./__fixtures__/liveRun";
import { FactoryStoryFrame, type FactoryFrameOptions } from "./factoryStoryFixtures";

// The index page over the whole story viewport, so its breakpoints follow
// the captured width (1280 desktop, 390 phone).
function indexStory(frame: FactoryFrameOptions = {}): Story {
  return {
    render: () => (
      <FactoryStoryFrame mode="viewport" route="/factory" {...frame}>
        <FactoryIndexPage />
      </FactoryStoryFrame>
    ),
  };
}

const meta = {
  title: "Factory/Index",
  component: FactoryIndexPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof FactoryIndexPage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The live run (Atlas, #9630 ready, two decisions open) beside a finished factory. */
export const List = indexStory();

export const ListDark = indexStory({ theme: "dark" });

/** A ten-project factory first (perf and dense counts). */
export const TenProjects = indexStory({
  routes: { "/v1/factory/factories": { body: tenProjectsFactoryList() } },
});

export const Loading = indexStory({ routes: { "/v1/factory/factories": "pending" } });

/** No factory yet, everything ready to start one. */
export const EmptyWithPreflight = indexStory({
  routes: { "/v1/factory/factories": { body: emptyFactoryList() } },
});

export const EmptyGithubDisconnected = indexStory({
  routes: {
    "/v1/factory/factories": { body: emptyFactoryList() },
    "/v1/factory/preflight": { body: preflightGithubDisconnected() },
  },
});

/** Nothing set up: every check fails with its fix, and the start button waits. */
export const EmptyPreflightFailing = indexStory({
  routes: {
    "/v1/factory/factories": { body: emptyFactoryList() },
    "/v1/factory/preflight": { body: preflightAllFailing() },
  },
});

export const EmptyPreflightFailingDark = indexStory({
  theme: "dark",
  routes: {
    "/v1/factory/factories": { body: emptyFactoryList() },
    "/v1/factory/preflight": { body: preflightAllFailing() },
  },
});

export const EmptyPreflightLoading = indexStory({
  routes: {
    "/v1/factory/factories": { body: emptyFactoryList() },
    "/v1/factory/preflight": "pending",
  },
});

/** The first load failed: an alert with Retry, never the empty state. */
export const LoadError = indexStory({
  routes: { "/v1/factory/factories": { error: 503, message: "Service unavailable" } },
});

// Cards fetched at 14:02, then a refresh that fails.
const staleThenFailing: FactoryFrameOptions = {
  routes: { "/v1/factory/factories": { error: 503 } },
  seed: (client) =>
    client.setQueryData(FACTORIES_QUERY_KEY, factoryList(), {
      updatedAt: new Date(2026, 9, 26, 14, 2).getTime(),
    }),
  afterMount: (client) => void client.invalidateQueries({ queryKey: FACTORIES_QUERY_KEY }),
};

/** A refresh failed: the last cards stay under the alert, with their age. */
export const RefreshErrorKeepsCards = indexStory(staleThenFailing);

export const RefreshErrorKeepsCardsDark = indexStory({ ...staleThenFailing, theme: "dark" });
