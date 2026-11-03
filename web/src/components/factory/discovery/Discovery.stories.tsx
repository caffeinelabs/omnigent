import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { DiscoveryPage } from "@/pages/factory/DiscoveryPage";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import {
  type DiscoveryRouteOptions,
  discoverySnapshot,
  discoveryRoutes,
} from "./__fixtures__/discoveryFixtures";

// Discovery in the new cockpit over the sample factory (every product word is
// the sample's own data): learning, a cold start, discovery off (in the
// factory, on the server, for an operator), each view, and each side panel;
// light, dark and a 390 px phone.

function discovery(
  view = "",
  routes: DiscoveryRouteOptions = {},
  frame: CockpitStoryOptions & { snapshot?: FactorySnapshot } = {},
): Story {
  const snapshot = frame.snapshot ?? discoverySnapshot();
  return {
    render: () => (
      <CockpitStory
        snapshot={snapshot}
        path={`/discovery${view ? `/${view}` : ""}`}
        routes={discoveryRoutes(snapshot, routes)}
        {...frame}
      />
    ),
  };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

const meta = {
  title: "Factory/Cockpit v2/Discovery",
  component: DiscoveryPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof DiscoveryPage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The live sample: Scout, sources, the chain, the goal, and the opportunities by band. */
export const LiveSample = discovery("", {}, { theme: "dark" });

export const LiveSampleLight = discovery();

export const LiveSampleMobile = phone(discovery("", {}, { theme: "dark" }));

/** Cold start: no opportunity yet, the checklist, signals waiting. */
export const ColdStart = discovery("", { state: "cold" });

export const ColdStartDark = discovery("", { state: "cold" }, { theme: "dark" });

/** Discovery off in the factory, on a server that runs it: an owner reviews the starter. */
export const DiscoveryOff = discovery("", { state: "off" });

export const DiscoveryOffDark = discovery("", { state: "off" }, { theme: "dark" });

export const DiscoveryOffMobile = phone(discovery("", { state: "off" }));

/** An owner reviewed the starter: the staged change, its effect and who applies it. */
export const DiscoveryOffStarterStaged: Story = {
  ...discovery("", { state: "off" }, { theme: "dark" }),
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(await body.findByTestId("discovery-starter-review"));
    await body.findByTestId("discovery-starter-preview");
  },
};

/** An operator sees what discovery does and that an owner turns it on. */
export const DiscoveryOffOperator = discovery(
  "",
  { state: "off" },
  { snapshot: discoverySnapshot("operator") },
);

/** This server does not run discovery: it explains, and offers nothing to press. */
export const DiscoveryOffOnThisServer = discovery("", { state: "server-off" });

/** Kill with a reason from the seeded list. */
export const KillWithReason: Story = {
  ...discovery(),
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.hover(await body.findByTestId("opportunity-row-O-7"));
    await userEvent.click(await body.findByTestId("opportunity-kill-O-7"));
    await userEvent.click(await body.findByTestId("discovery-kill-reason-solved_elsewhere"));
  },
};

/** Flow: the loop as fixed lanes. */
export const Flow = discovery("flow", {}, { theme: "dark" });

/** Flow with an opportunity selected: its chain lit, the rest dimmed, written out below. */
export const FlowSelected = discovery("flow", {}, { theme: "dark", search: "?sel=O-15" });

export const FlowSelectedLight = discovery("flow", {}, { search: "?sel=O-15" });

export const FlowMobile = phone(discovery("flow", {}, { search: "?sel=O-15" }));

/** Map: value against confidence, with the rubric's dividers. */
export const MapView = discovery("map");

export const MapViewDark = discovery("map", {}, { theme: "dark" });

/** Impact: a won readout, bets waiting for theirs, the prediction ledger. */
export const Impact = discovery("impact", {}, { theme: "dark" });

export const ImpactLight = discovery("impact");

/** Impact on a server without readouts yet: every bet says "not measured yet". */
export const ImpactWithoutReadouts = discovery("impact", { noReadouts: true });

/** Signals: the unsorted queue, and a planted injection that is never actionable. */
export const SignalsWithInjection = discovery("signals");

export const SignalsWithInjectionDark = discovery("signals", {}, { theme: "dark" });

export const SignalsMobile = phone(discovery("signals"));

/** Hunches and what came of them. */
export const Hunches = discovery("hunches");

/** The opportunity panel: moderate evidence, a bet that waits for you. */
export const OpportunityPeekModerate = discovery("", {}, { theme: "dark", search: "?peek=O-15" });

export const OpportunityPeekModerateLight = discovery("", {}, { search: "?peek=O-15" });

/** Weak evidence from a single source type, a test co-signing. */
export const OpportunityPeekWeakSingleSource = discovery("", {}, { search: "?peek=O-12" });

/** Parked under a non-goal. */
export const OpportunityPeekParked = discovery("", {}, { search: "?peek=O-21" });

/** An id that is not in this factory. */
export const OpportunityPeekNotFound = discovery("", {}, { search: "?peek=O-99" });

export const OpportunityPeekMobile = phone(discovery("", {}, { search: "?peek=O-15" }));

/** The bet panel: the bet beside the case against, Approve, Edit and approve, Reject. */
export const BetPeek = discovery("", {}, { theme: "dark", search: "?peek=C-31" });

export const BetPeekLight = discovery("", {}, { search: "?peek=C-31" });

export const BetPeekMobile = phone(discovery("", {}, { search: "?peek=C-31" }));

/** An active source: what Scout reads, the three read-only layers, usage, the access log. */
export const SourcePeekActive = discovery("", {}, { search: "?peek=source:repo" });

/** A source an agent proposed, waiting for an owner. */
export const SourcePeekPending = discovery("", {}, { theme: "dark", search: "?peek=source:ana" });

export const SourcePeekMobile = phone(discovery("", {}, { search: "?peek=source:ana" }));

/** A readout: the band and the actual, guardrails, verdict and decision. */
export const ReadoutPeek = discovery("impact", {}, { search: "?peek=readout:R-1" });
