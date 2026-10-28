import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import { InsightsPage } from "@/pages/factory/InsightsPage";
import type { ViewerRole } from "@/lib/factoryApi";
import {
  BoardFrame,
  CockpitStory,
  type CockpitStoryOptions,
  SNAPSHOT_PATH,
} from "../board/boardStoryKit";
import { round3Events, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import {
  type LearnRouteOptions,
  allMetrics,
  fixtureDays,
  insightsResponse,
  learnRoutes,
  metricsByDay,
} from "./__fixtures__/learnFixtures";
import { KPI_SPECS, daySeries } from "./insightsModel";
import { KpiTile } from "./KpiTile";

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
          ...learnRoutes(round3Snapshot().factory.id, {
            owner: role !== "viewer" && role !== "operator",
            ...learn,
          }),
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
  title: "Factory/Insights",
  component: InsightsPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof InsightsPage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The round-3 factory over 7 days: every tile with n and a delta, then every section. */
export const Populated = page("/insights");

export const PopulatedDark = page("/insights", { theme: "dark" });

export const Mobile = phone(page("/insights"));

export const MobileDark = phone(page("/insights", { theme: "dark" }));

/** Nothing measured yet: tiles read "n=0", sections say what fills them. */
export const Empty = page("/insights", { learn: { kind: "empty" } });

export const EmptyDark = page("/insights", { theme: "dark", learn: { kind: "empty" } });

/** Two merged PRs: numbers show but read "n=2 — inconclusive" with faint lines. */
export const SmallN = page("/insights", { learn: { kind: "small" } });

export const SmallNMobile = phone(page("/insights", { learn: { kind: "small" } }));

export const Loading = page("/insights", { learn: { insights: "pending" } });

export const LoadError = page("/insights", {
  learn: { insights: { error: 503, message: "metrics unavailable" } },
});

/** The version markers can't be read: the row says so with Retry (not "none yet"). */
export const VersionsError = page("/insights", {
  routes: {
    [`/v1/factory/factories/${round3Snapshot().factory.id}/versions`]: {
      error: 503,
      message: "unavailable",
    },
  },
});

/** Ops (viewer): every number, no per-person table, Propose change disabled. */
export const Viewer = page("/insights", { role: "viewer" });

export const ViewerDark = page("/insights", { role: "viewer", theme: "dark" });

/** Mia (operator): may propose changes for the owners. */
export const Operator = page("/insights", { role: "operator" });

/** Grouped by project: the KPI table under the tiles. */
export const GroupByProject = page("/insights", { search: "?group=project" });

export const GroupByProjectMobile = phone(page("/insights", { search: "?group=project" }));

/** Measured only on definition v7 (its marker pressed). */
export const DefinitionVersionFilter = page("/insights", { search: "?def=7&window=30d" });

/** A 60-agent factory: where the time went across 16 projects, 48 changes. */
export const Scale = page("/insights", { learn: { scale: true } });

export const ScaleMobile = phone(page("/insights", { learn: { scale: true } }));

/** Propose change from the $/PR tile (owner: applies as a human C-n). */
export const ProposeFromTile = page("/insights", {}, async ({ canvasElement }) => {
  await userEvent.click(
    await within(canvasElement.ownerDocument.body).findByTestId("kpi-cost_per_merged_pr-propose"),
  );
});

export const ProposeFromTileOperator = page(
  "/insights",
  { role: "operator" },
  ProposeFromTile.play,
);

/** Export menu (NDJSON or CSV, never transcripts). */
export const ExportMenu = page("/insights", {}, async ({ canvasElement }) => {
  await userEvent.click(
    await within(canvasElement.ownerDocument.body).findByTestId("insights-export"),
  );
});

// ── Tiles on their own ─────────────────────────────────────────

function Tiles({
  kind,
  theme,
}: {
  kind: "populated" | "small" | "empty";
  theme?: "light" | "dark";
}) {
  const kpis = insightsResponse(kind).kpis;
  const byDay = metricsByDay(kind);
  const days = fixtureDays();
  return (
    <BoardFrame theme={theme} width={1100} routes={learnRoutes(SNAPSHOT_PATH.split("/").pop()!)}>
      <div className="grid grid-cols-2 gap-2 p-4 sm:grid-cols-4">
        {KPI_SPECS.map((spec) => (
          <KpiTile
            key={spec.name}
            spec={spec}
            metric={kpis[spec.name] ?? allMetrics(kind)[spec.name]}
            series={daySeries(byDay[spec.name]?.groups, days)}
            marks={[{ key: "def-7", at: 0.82, title: "def v7" }]}
          />
        ))}
      </div>
    </BoardFrame>
  );
}

export const KpiTiles: Story = { render: () => <Tiles kind="populated" /> };

export const KpiTilesDark: Story = { render: () => <Tiles kind="populated" theme="dark" /> };

export const KpiTilesSmallN: Story = { render: () => <Tiles kind="small" /> };

export const KpiTilesEmpty: Story = { render: () => <Tiles kind="empty" /> };
