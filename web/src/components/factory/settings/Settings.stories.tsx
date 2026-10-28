import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import { SettingsPage } from "@/pages/factory/SettingsPage";
import type { ViewerRole } from "@/lib/factoryApi";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import { round3Events, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { type LearnRouteOptions, learnRoutes } from "../insights/__fixtures__/learnFixtures";

const FID = round3Snapshot().factory.id;
const BASE = `/v1/factory/factories/${FID}`;

function page(
  path: string,
  options: CockpitStoryOptions & { role?: ViewerRole; learn?: LearnRouteOptions } = {},
  play?: Story["play"],
): Story {
  const { role = "creator", learn, routes, ...rest } = options;
  return {
    render: () => (
      <CockpitStory
        snapshot={round3Snapshot(role)}
        events={round3Events()}
        path={path}
        routes={{
          ...learnRoutes(FID, learn),
          [`${BASE}/alerts/routing`]: {
            body: { cockpit: true, browser: true, slack: false, slack_min_severity: "warning" },
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

const meta = {
  title: "Factory/Settings",
  component: SettingsPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof SettingsPage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Project autonomy and engine mode (owners edit; the rest read). */
export const Autonomy = page("/settings/autonomy");

export const AutonomyDark = page("/settings/autonomy", { theme: "dark" });

export const AutonomyMobile = phone(page("/settings/autonomy"));

/** Money and every loop's budget. */
export const Budgets = page("/settings/budgets");

export const BudgetsDark = page("/settings/budgets", { theme: "dark" });

export const BudgetsMobile = phone(page("/settings/budgets"));

export const BudgetsMobileDark = phone(page("/settings/budgets", { theme: "dark" }));

/** Luka raises implement attempts 3 → 4: the bar lists the change before Save. */
export const BudgetsEdited = page("/settings/budgets", {}, async ({ canvasElement }) => {
  const body = within(canvasElement.ownerDocument.body);
  const input = await body.findByTestId("setting-input-loops.implement.attempts");
  await userEvent.clear(input);
  await userEvent.type(input, "4");
});

/** Out of bounds (attempts ≤ 6): the field says so and Save stays disabled. */
export const BudgetsInvalid = page("/settings/budgets", {}, async ({ canvasElement }) => {
  const body = within(canvasElement.ownerDocument.body);
  const input = await body.findByTestId("setting-input-loops.implement.attempts");
  await userEvent.clear(input);
  await userEvent.type(input, "9");
});

/** Ops (viewer): the values in force, read-only, with who can change them. */
export const BudgetsViewer = page("/settings/budgets", { role: "viewer" });

export const BudgetsViewerMobile = phone(page("/settings/budgets", { role: "viewer" }));

/** Mia (operator): read-only too (settings are for owners). */
export const BudgetsOperator = page("/settings/budgets", { role: "operator" });

/** Thresholds plus the set-only Slack webhook. */
export const Alerts = page("/settings/alerts");

export const AlertsDark = page("/settings/alerts", { theme: "dark" });

export const AlertsSlackSet = page("/settings/alerts", {
  routes: {
    [`${BASE}/alerts/routing`]: {
      body: { cockpit: true, browser: true, slack: true, slack_min_severity: "warning" },
    },
  },
});

export const AlertsViewer = page("/settings/alerts", { role: "viewer" });

export const Rules = page("/settings/rules");

export const RulesDark = page("/settings/rules", { theme: "dark" });

export const RulesMobile = phone(page("/settings/rules"));

export const RulesAdding = page("/settings/rules", {}, async ({ canvasElement }) => {
  await userEvent.click(await within(canvasElement.ownerDocument.body).findByTestId("rules-add"));
});

export const RulesViewer = page("/settings/rules", { role: "viewer" });

/** Versions, diff, revert, bundles, "Run retro now", then cadence, mail, previews, models. */
export const Definition = page("/settings/definition");

export const DefinitionDark = page("/settings/definition", { theme: "dark" });

export const DefinitionMobile = phone(page("/settings/definition"));

/** After saving C-17 the definition is v8. */
export const DefinitionAfterSave = page("/settings/definition", { learn: { afterSave: true } });

export const People = page("/settings/people");

export const Loading = page("/settings/budgets", { learn: { definition: "pending" } });

export const LoadError = page("/settings/budgets", {
  learn: { definition: { error: 503, message: "unavailable" } },
});
