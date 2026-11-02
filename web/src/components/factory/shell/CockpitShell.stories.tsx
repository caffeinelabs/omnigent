import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import type { FactorySnapshot, NotifyMeView } from "@/lib/factoryApi";
import { FactoryLayout } from "@/pages/factory/FactoryLayout";
import { preflightOk } from "../__fixtures__/liveRun";
import { round3IssueList } from "../agents/__fixtures__/opsFixtures";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import { definitionResponse } from "../insights/__fixtures__/learnFixtures";
import { notifyMe } from "../notify/__fixtures__/notifyFixtures";
import { statusSummaryOf } from "../overview/__fixtures__/nowFixtures";
import { round3Snapshot } from "./__fixtures__/shellFixtures";

// The new cockpit's shell over the round-3 sample factory: the server offers
// it (preflight `cockpit_v2`) and the person chose it (`ui.cockpit`). The
// product name comes from the sample's product profile, never from code.

const PRODUCT = "Sample app (sample)";
const ALERT_USD_PER_H = 15;

function meChoosing(): NotifyMeView {
  const me = notifyMe();
  return { ...me, settings: { ...me.settings, ui: { cockpit: "v2" } } };
}

/** Nothing is broken and nothing waits: no stopped project or lead, no open asks. */
function calm(snapshot: FactorySnapshot, keepDecisions = false): FactorySnapshot {
  return {
    ...snapshot,
    projects: snapshot.projects.map((p) =>
      p.overlay === "failed" ? { ...p, overlay: null, overlay_reason: null } : p,
    ),
    members: snapshot.members.map((m) =>
      m.health === "dead" || m.status === "failed" ? { ...m, health: null, status: "idle" } : m,
    ),
    decisions: keepDecisions ? snapshot.decisions : [],
    prs: snapshot.prs.map((pr) => ({ ...pr, ready: false })),
  };
}

function cockpit(
  options: CockpitStoryOptions & { calmness?: "red" | "amber" | "green" } = {},
  play?: Story["play"],
): Story {
  const { calmness = "red", routes, snapshot: given, ...rest } = options;
  const base = given ?? round3Snapshot();
  const snapshot =
    calmness === "green" ? calm(base) : calmness === "amber" ? calm(base, true) : base;
  const own = `/v1/factory/factories/${snapshot.factory.id}`;
  const definition = definitionResponse();
  return {
    render: () => (
      <CockpitStory
        snapshot={snapshot}
        routes={{
          "/v1/factory/preflight": { body: { ...preflightOk(), cockpit_v2: true } },
          [`${own}/notify/me`]: { body: meChoosing() },
          [`${own}/definition`]: {
            body: {
              ...definition,
              effective: { ...definition.effective, product: { name: PRODUCT } },
            },
          },
          // A calm factory has no open incidents either.
          ...(calmness === "red"
            ? {}
            : { [`${own}/issues`]: { body: { data: [], count: 0, mine: [] } } }),
          [`${own}/status-summary`]: {
            body: statusSummaryOf(snapshot, {
              incidents: calmness === "red" ? round3IssueList() : [],
              usdPerHAlert: ALERT_USD_PER_H,
            }),
          },
          ...routes,
        }}
        {...rest}
      />
    ),
    play,
  };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

function body(canvasElement: HTMLElement) {
  return within(canvasElement.ownerDocument.body);
}

async function openHealth({ canvasElement }: { canvasElement: HTMLElement }) {
  await userEvent.click(await body(canvasElement).findByTestId("cockpit-verdict"));
  await body(canvasElement).findByTestId("health-peek");
}

const meta = {
  title: "Factory/Cockpit v2/Shell",
  component: FactoryLayout,
  parameters: { layout: "fullscreen" },
  args: { children: null },
} satisfies Meta<typeof FactoryLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

// ── The shell on Home (1440, 1024 and 390, light and dark) ──────

/** One top bar: switcher with the product, verdict, five places, search, Room, theme, you. */
export const Home = cockpit();

export const HomeDark = cockpit({ theme: "dark" });

export const HomeMobile = phone(cockpit());

export const HomeMobileDark = phone(cockpit({ theme: "dark" }));

// ── The verdict ────────────────────────────────────────────────

/** Red: a project stopped and a critical incident is open. */
export const VerdictRed = cockpit({}, openHealth);

export const VerdictRedDark = cockpit({ theme: "dark" }, openHealth);

/** Amber: nothing is broken, decisions wait on you. */
export const VerdictAmber = cockpit({ calmness: "amber" }, openHealth);

export const VerdictAmberDark = cockpit({ calmness: "amber", theme: "dark" }, openHealth);

/** Green: running, nothing broken, nothing waiting. */
export const VerdictGreen = cockpit({ calmness: "green" }, openHealth);

export const VerdictGreenDark = cockpit({ calmness: "green", theme: "dark" }, openHealth);

/** The Health panel as a bottom sheet. */
export const HealthMobile = phone(cockpit({}, openHealth));

// ── The Room drawer ────────────────────────────────────────────

/** `?room=factory`: the Room over the page, not a dock beside it. */
export const RoomDrawer = cockpit({ search: "?room=factory" });

export const RoomDrawerDark = cockpit({ search: "?room=factory", theme: "dark" });

export const RoomDrawerMobile = phone(cockpit({ search: "?room=factory" }));

// ── Viewer, Debug, Steer, Discovery, Settings ──────────────────

/** A viewer reads everything; the banner says who can change things. */
export const Viewer = cockpit({ snapshot: round3Snapshot("viewer") });

export const ViewerMobile = phone(cockpit({ snapshot: round3Snapshot("viewer") }));

/** Debug › Timeline: today's views one level down. */
export const DebugTimeline = cockpit({ path: "/debug/timeline" });

export const DebugIncidentsDark = cockpit({ path: "/debug/incidents", theme: "dark" });

/** Steer › Budgets and autonomy (today's forms, one place to change how the factory works). */
export const SteerBudgets = cockpit({ path: "/steer/budgets" });

export const SteerBudgetsMobile = phone(cockpit({ path: "/steer/budgets" }));

/** Discovery while the factory has not turned it on. */
export const DiscoveryOff = cockpit({ path: "/discovery" });

/** Settings is personal and access only. */
export const SettingsIntegrations = cockpit({ path: "/settings/integrations" });

/** The avatar menu: Settings, Debug views, theme, the classic cockpit. */
export const YouMenu = cockpit({}, async ({ canvasElement }) => {
  await userEvent.click(await body(canvasElement).findByTestId("cockpit-you"));
});

// ── The classic cockpit, offering the switch ───────────────────

/** The server offers the new cockpit; this person has not chosen it. */
export const ClassicOffersSwitch: Story = {
  render: () => (
    <CockpitStory
      snapshot={round3Snapshot()}
      routes={{
        "/v1/factory/preflight": { body: { ...preflightOk(), cockpit_v2: true } },
        [`/v1/factory/factories/${round3Snapshot().factory.id}/notify/me`]: { body: notifyMe() },
      }}
    />
  ),
};
