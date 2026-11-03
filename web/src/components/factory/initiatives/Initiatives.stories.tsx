import type { Meta, StoryObj } from "@storybook/react-vite";
import type { AsksResponse } from "@/lib/factoryAsksApi";
import type { FactorySnapshot } from "@/lib/factoryApi";
import type { InitiativeView } from "@/lib/factoryInitiativesApi";
import { InitiativePage } from "@/pages/factory/InitiativePage";
import type { StoryRoutes } from "../factoryStoryFixtures";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import { homeRoutes, homeSnapshot } from "../home/__fixtures__/homeFixtures";
import {
  type SampleState,
  sampleInitiativeLine,
  sampleInitiativeView,
} from "./__fixtures__/initiativeFixtures";

// Initiatives in the new cockpit over the round-3 sample factory (every
// product word is the sample's own data): Home's initiative card above the
// project rows, and the initiative page in its three states (in progress with
// the walking skeleton on the base env, an acceptance step failing, done);
// light, dark and a 390 px phone.

function routesFor(snapshot: FactorySnapshot, views: InitiativeView[]): StoryRoutes {
  const routes = homeRoutes(snapshot);
  const own = `/v1/factory/factories/${snapshot.factory.id}`;
  const asks = routes[`${own}/asks`] as { body: AsksResponse };
  const out: StoryRoutes = {
    ...routes,
    [`${own}/asks`]: { body: { ...asks.body, initiatives: views.map(sampleInitiativeLine) } },
  };
  for (const view of views) out[`${own}/initiatives/${view.ref}`] = { body: view };
  return out;
}

function story(
  state: SampleState,
  path: string,
  frame: CockpitStoryOptions = {},
  phone = false,
): Story {
  const snapshot = homeSnapshot();
  const view = sampleInitiativeView(state);
  return {
    render: () => (
      <CockpitStory
        snapshot={snapshot}
        path={path}
        routes={routesFor(snapshot, [view])}
        {...frame}
      />
    ),
    ...(phone ? { globals: { viewport: { value: "mobile2" } } } : {}),
  };
}

const PAGE = "/initiatives/I-3";

const meta = {
  title: "Factory/Cockpit v2/Initiatives",
  component: InitiativePage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof InitiativePage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Home: the initiative card above the projects (skeleton on the base env, a merge waits). */
export const HomeCard = story("in_progress", "", { theme: "dark" });

export const HomeCardLight = story("in_progress", "");

export const HomeCardMobile = story("in_progress", "", { theme: "dark" }, true);

/** Home: an acceptance step fails on the base env (the card turns red). */
export const HomeCardFailing = story("failing", "");

/** The page in progress: the skeleton is on the base env, a part waits for a merge. */
export const InProgress = story("in_progress", PAGE);

export const InProgressDark = story("in_progress", PAGE, { theme: "dark" });

export const InProgressMobile = story("in_progress", PAGE, {}, true);

/** Step 3 fails on the base env; it came back to its project as T-14. */
export const AcceptanceFailing = story("failing", PAGE);

export const AcceptanceFailingDark = story("failing", PAGE, { theme: "dark" });

export const AcceptanceFailingMobile = story("failing", PAGE, { theme: "dark" }, true);

/** Every project merged and the last run passed (the sign-in step cannot run in envs). */
export const Done = story("done", PAGE);

export const DoneDark = story("done", PAGE, { theme: "dark" });

export const DoneMobile = story("done", PAGE, {}, true);
