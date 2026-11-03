import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, waitFor, within } from "storybook/test";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { HomePage } from "@/pages/factory/HomePage";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import {
  type HomeRouteOptions,
  SAMPLE_DISCOVERY,
  SAMPLE_OPPORTUNITIES,
  calmSnapshot,
  coldStartProposals,
  discoveryDefinition,
  homeRoutes,
  homeSnapshot,
  manyProjectsSnapshot,
  withBet,
  withReadyPrs,
} from "./__fixtures__/homeFixtures";

// Home in the new cockpit over sample factories (every product word is the
// sample's own data): the round-3 sample with discovery learning, nothing
// waiting, many ready PRs, discovery off (in the factory, or on the server), a
// cold start, 12 projects with 60 agents, and an answer held for Undo (the
// top bar's count leaves it out, as Home does); light, dark and a 390 px phone.

function home(
  snapshot: FactorySnapshot,
  routes: HomeRouteOptions = {},
  frame: CockpitStoryOptions = {},
): Story {
  return {
    render: () => (
      <CockpitStory snapshot={snapshot} routes={homeRoutes(snapshot, routes)} {...frame} />
    ),
  };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

const LEARNING: HomeRouteOptions = {
  definition: discoveryDefinition({ goal: true }),
  discovery: SAMPLE_DISCOVERY,
  opportunities: SAMPLE_OPPORTUNITIES,
};

/** The live sample with a co-signed bet waiting for approval. */
const live = withBet(homeSnapshot());
const LIVE: HomeRouteOptions = { ...LEARNING, proposals: live.proposals };

const cold = coldStartProposals(calmSnapshot(homeSnapshot()));
const COLD: HomeRouteOptions = {
  definition: discoveryDefinition(),
  discovery: { chain: { signals: 64, opportunities: 0 } },
  opportunities: { data: [] },
  proposals: cold.proposals,
};

const meta = {
  title: "Factory/Cockpit v2/Home",
  component: HomePage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof HomePage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The live sample: discovery learning, a stopped lead, questions, a bet and a merge. */
export const LiveSample = home(live.snapshot, LIVE, { theme: "dark" });

export const LiveSampleLight = home(live.snapshot, LIVE);

export const LiveSampleMobile = phone(home(live.snapshot, LIVE, { theme: "dark" }));

export const LiveSampleMobileLight = phone(home(live.snapshot, LIVE));

/**
 * Answers the first question in place and holds its toast open: the row
 * leaves Needs you, and the top bar's count drops with it until Undo.
 */
const answerHeld = (story: Story): Story => ({
  ...story,
  play: async ({ canvasElement }) => {
    const option = await waitFor(() => {
      const found = canvasElement.querySelector<HTMLElement>(
        '[data-testid^="need-"][data-testid*="-option-"]',
      );
      if (!found) throw new Error("no answer in place yet");
      return found;
    });
    await userEvent.click(option);
    const toast = await within(canvasElement.ownerDocument.body).findByTestId("factory-toast");
    await userEvent.hover(toast);
  },
});

export const AnswerHeld = answerHeld(home(live.snapshot, LIVE, { theme: "dark" }));

export const AnswerHeldLight = answerHeld(home(live.snapshot, LIVE));

export const AnswerHeldMobile = phone(answerHeld(home(live.snapshot, LIVE, { theme: "dark" })));

/** Nothing needs you: one calm line, and what the factory handled itself. */
export const NothingNeedsYou = home(calmSnapshot(homeSnapshot()), {
  ...LEARNING,
  incidents: [],
});

export const NothingNeedsYouDark = home(
  calmSnapshot(homeSnapshot()),
  { ...LEARNING, incidents: [] },
  { theme: "dark" },
);

/** Ten more ready PRs fold into one merge row that lists five. */
export const TenReadyPrs = home(withReadyPrs(homeSnapshot(), 10), LEARNING);

export const TenReadyPrsDark = home(withReadyPrs(homeSnapshot(), 10), LEARNING, {
  theme: "dark",
});

/** The merge panel the row opens (`?peek=merge`). */
export const MergePanel = home(withReadyPrs(homeSnapshot(), 10), LEARNING, {
  search: "?peek=merge",
});

/** Discovery off: the loop shows Build and Measure, with a way to turn it on. */
export const DiscoveryOff = home(homeSnapshot());

export const DiscoveryOffDark = home(homeSnapshot(), {}, { theme: "dark" });

export const DiscoveryOffMobile = phone(home(homeSnapshot()));

/** The definition turns discovery on, but this server does not run it: it stays dark. */
export const DiscoveryOffOnThisServer = home(homeSnapshot(), {
  ...LEARNING,
  definition: discoveryDefinition({ goal: true, server: false }),
});

/** Cold start: no goal yet, the checklist, and the profile and goal to review. */
export const DiscoveryColdStart = home(cold.snapshot, COLD);

export const DiscoveryColdStartDark = home(cold.snapshot, COLD, { theme: "dark" });

export const DiscoveryColdStartMobile = phone(home(cold.snapshot, COLD, { theme: "dark" }));

/** 12 projects and 60 agents: every row stays one row, rings cap with +N. */
export const TwelveProjects = home(manyProjectsSnapshot(), { ...LEARNING, incidents: [] });

export const TwelveProjectsDark = home(
  manyProjectsSnapshot(),
  { ...LEARNING, incidents: [] },
  { theme: "dark" },
);

export const TwelveProjectsMobile = phone(
  home(manyProjectsSnapshot(), { ...LEARNING, incidents: [] }),
);
