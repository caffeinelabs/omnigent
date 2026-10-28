import type { Meta, StoryObj } from "@storybook/react-vite";
import { factoryQueryKey } from "@/hooks/useFactory";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { OverviewPage } from "@/pages/factory/OverviewPage";
import { LIVE_RUN_FACTORY_ID } from "../__fixtures__/liveRun";
import { storySnapshot } from "../board/__fixtures__/boardFixtures";
import { CockpitStory, type CockpitStoryOptions, SNAPSHOT_PATH } from "../board/boardStoryKit";
import {
  emptyFactorySnapshot,
  round3Events,
  round3Snapshot,
  scaleSnapshot,
} from "../shell/__fixtures__/shellFixtures";

const SPANS_PATH = `${SNAPSHOT_PATH}/spans`;

function overview(options: CockpitStoryOptions = {}): Story {
  return {
    render: () => <CockpitStory snapshot={round3Snapshot()} events={round3Events()} {...options} />,
  };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

const meta = {
  title: "Factory/Overview",
  component: OverviewPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof OverviewPage>;

export default meta;
type Story = StoryObj<typeof meta>;

// ── Live ───────────────────────────────────────────────────────

/** Atlas factory on temp-main as its creator: Needs you, 5 projects in flight, the hour, the feed. */
export const Live = overview();

export const LiveDark = overview({ theme: "dark" });

export const LiveMobile = phone(overview());

export const LiveMobileDark = phone(overview({ theme: "dark" }));

/** Mia (operator): D-2 is hers, so Needs you opens on Mine; Stop and Invite stay disabled. */
export const Operator = overview({ snapshot: round3Snapshot("operator") });

/** Ops (viewer): the read-only banner with Request access, every lever disabled. */
export const Viewer = overview({ snapshot: round3Snapshot("viewer") });

export const ViewerDark = overview({ snapshot: round3Snapshot("viewer"), theme: "dark" });

export const ViewerMobile = phone(overview({ snapshot: round3Snapshot("viewer") }));

/** Three people here: Luka on the Overview, Mia on db-version, Ops idle. */
export const MultiUser = overview({
  snapshot: (() => {
    const snapshot = round3Snapshot();
    snapshot.presence = [
      ...(snapshot.presence ?? []),
      {
        user_id: "ops@caffeine.ai",
        name: "Ops",
        location: "pr:9653",
        idle: true,
        since: snapshot.server_time - 600,
        sessions: [],
      },
      {
        user_id: "ana@caffeine.ai",
        name: "Ana",
        location: null,
        idle: false,
        since: snapshot.server_time - 120,
        sessions: ["a2fc64b9e92e54c8b2f0f78038df202d"],
      },
    ];
    return snapshot;
  })(),
});

/** Twelve projects and 60 agents: the table stays dense, the heatmap shows the busiest 8. */
export const Scale60Agents = overview({ snapshot: scaleSnapshot(12, 60) });

export const Scale60AgentsDark = overview({ snapshot: scaleSnapshot(12, 60), theme: "dark" });

export const Scale60AgentsMobile = phone(overview({ snapshot: scaleSnapshot(12, 60) }));

/** A round-2 server: no viewer block, base, engine mode or spans; the heatmap reads events. */
export const RoundTwoServer = overview({
  snapshot: storySnapshot(),
  events: undefined,
  routes: { [SPANS_PATH]: { error: 404, message: "not found" } },
});

/** Long project titles and a long question truncate in place. */
export const LongText = overview({
  snapshot: (() => {
    const snapshot: FactorySnapshot = round3Snapshot();
    for (const project of snapshot.projects) {
      project.title = `${project.title} with a much longer name than anyone would type`;
    }
    snapshot.decisions[0]!.question =
      "Which table is the source of truth for the DB version: knex_migrations (212 rows, newest 2026-10-20) or schema_migrations (3 legacy rows from 2024), and should the endpoint fall back?";
    return snapshot;
  })(),
});

export const LongTextMobile = phone(LongText);

// ── Empty, loading and errors ──────────────────────────────────

/** Atlas just started: no project yet, nothing waiting. */
export const Empty = overview({
  snapshot: emptyFactorySnapshot(),
  events: { data: [], has_more: false },
});

export const EmptyDark = overview({
  snapshot: emptyFactorySnapshot(),
  events: { data: [], has_more: false },
  theme: "dark",
});

export const Loading = overview({ routes: { [SNAPSHOT_PATH]: "pending" } });

export const LoadingMobile = phone(overview({ routes: { [SNAPSHOT_PATH]: "pending" } }));

/** The first read failed: an alert with Retry, no empty page. */
export const LoadError = overview({ routes: { [SNAPSHOT_PATH]: { error: 503 } } });

/** Removed from the roster (or a factory id that doesn't exist). */
export const NoAccess = overview({ routes: { [SNAPSHOT_PATH]: { error: 404 } } });

/** A refresh failed: the page stays under the alert, with its age. */
export const RefreshError = overview({
  routes: { [SNAPSHOT_PATH]: { error: 503 } },
  seed: (client) =>
    client.setQueryData(factoryQueryKey(LIVE_RUN_FACTORY_ID), round3Snapshot(), {
      updatedAt: new Date(2026, 9, 26, 14, 2).getTime(),
    }),
  afterMount: (client) =>
    void client.invalidateQueries({ queryKey: factoryQueryKey(LIVE_RUN_FACTORY_ID) }),
});

/** The activity and live feed failed while the snapshot is fine. */
export const PanelsError = overview({
  routes: {
    [SPANS_PATH]: { error: 503 },
    [`${SNAPSHOT_PATH}/events`]: { error: 503 },
  },
});

/** The activity and live feed still loading. */
export const PanelsLoading = overview({
  routes: { [SPANS_PATH]: "pending", [`${SNAPSHOT_PATH}/events`]: "pending" },
});

/** `?decision=D-2`: the dock highlights it. */
export const DecisionHighlighted = overview({ search: "?decision=D-2" });

/** The push socket is down: the header says so and the page polls. */
export const Reconnecting = overview({ live: false });
