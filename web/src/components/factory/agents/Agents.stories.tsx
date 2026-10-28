import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, userEvent, waitFor, within } from "storybook/test";
import { AgentsPage } from "@/pages/factory/AgentsPage";
import {
  BoardFrame,
  CockpitStory,
  type CockpitStoryOptions,
  SNAPSHOT_PATH,
} from "../board/boardStoryKit";
import {
  ROUND3_NOW_S,
  emptyFactorySnapshot,
  round3Events,
  round3Snapshot,
} from "../shell/__fixtures__/shellFixtures";
import { agentsResponse, scaleAgentsResponse } from "./__fixtures__/opsFixtures";
import { COMMIT_BUDGET_MS, FleetPerfHarness } from "./fleetPerfKit";

const AGENTS_PATH = `${SNAPSHOT_PATH}/agents`;
/** T-4's three attempts, the selection the bulk stories act on. */
const T4 = "?q=task%3AT-4";
const ROUTE_R2 = "9ba28e67c11a5a2fba4b7e0053ab6150";

function fleet(options: CockpitStoryOptions = {}, play?: Story["play"]): Story {
  return {
    render: () => (
      <CockpitStory
        snapshot={round3Snapshot()}
        events={round3Events()}
        path="/agents"
        {...options}
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

/** Select the agents of T-4 (three attempts of the route task). */
async function selectT4(canvasElement: HTMLElement) {
  const screen = body(canvasElement);
  await screen.findByTestId(`fleet-row-${ROUTE_R2}`);
  // Let the table settle on its measured layout before clicking its rows.
  await new Promise((resolve) => {
    setTimeout(resolve, 300);
  });
  /* oxlint-disable no-await-in-loop -- clicks land one after another */
  for (const row of screen.getAllByTestId(/^fleet-row-/)) {
    await userEvent.click(within(row).getByRole("checkbox"));
  }
  /* oxlint-enable no-await-in-loop */
}

const meta = {
  title: "Factory/Agents",
  component: AgentsPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof AgentsPage>;

export default meta;
type Story = StoryObj<typeof meta>;

// ── Live ───────────────────────────────────────────────────────

/** The round-3 fleet as its creator: 41 agents by project, the repeating IC flagged with INC-13. */
export const Live = fleet();

export const LiveDark = fleet({ theme: "dark" });

export const LiveMobile = phone(fleet());

export const LiveMobileDark = phone(fleet({ theme: "dark" }));

/** Mia (operator) with three people here: levers open, presence in the header. */
export const MultiUser = fleet({ snapshot: round3Snapshot("operator") });

/** Ops (viewer) reads the whole fleet. */
export const ViewerFleet = fleet({ snapshot: round3Snapshot("viewer") });

/** Ops (viewer): T-4's agents selected, every bulk lever disabled with who can. */
export const Viewer = fleet(
  { snapshot: round3Snapshot("viewer"), search: T4 },
  async ({ canvasElement }) => {
    await selectT4(canvasElement);
  },
);

export const ViewerDark = fleet(
  { snapshot: round3Snapshot("viewer"), theme: "dark", search: T4 },
  Viewer.play,
);

export const ViewerMobile = phone(
  fleet({ snapshot: round3Snapshot("viewer"), search: T4 }, Viewer.play),
);

// ── Query, saved views and bulk ────────────────────────────────

/** The spec's query: running Bash over $2 on db-* projects finds the looping IC. */
export const Query = fleet({
  search: "?q=state%3Arunning%20tool%3ABash%20cost%3E2%20project%3Adb-*",
});

/** Saved view Looping: repeat flags and loops out of budget. */
export const SavedLooping = fleet({ search: "?q=is%3Alooping" });

/** Saved view Waiting on me, as Mia: D-2 (claimed by her) and D-33 (for operators). */
export const SavedWaiting = fleet({
  snapshot: round3Snapshot("operator"),
  search: "?q=is%3Awaiting",
});

/** A selection the query then hides: the bulk bar acts on the shown ones and says so. */
export const BulkSelectionHidden = fleet({ search: T4 }, async ({ canvasElement }) => {
  await selectT4(canvasElement);
  const input = body(canvasElement).getByTestId("fleet-query");
  await userEvent.clear(input);
  await userEvent.type(input, "flag:repeat");
});

/** Grouped by role instead of project. */
export const GroupedByRole = fleet({ search: "?group=role" });

/** A query that doesn't parse says what it understood. */
export const QueryErrors = fleet({ search: "?q=colour%3Ared%20cost%3E2h%20route" });

/** No agent matches the filter. */
export const NoMatch = fleet({ search: "?q=project%3Anope" });

/** T-4's three workers selected: the bulk bar. */
export const BulkSelected = fleet({ search: T4 }, async ({ canvasElement }) => {
  await selectT4(canvasElement);
});

export const BulkSelectedMobile = phone(fleet({ search: T4 }, BulkSelected.play));

/** Bulk Redispatch: the confirmation lists the task once and posts as the viewer. */
export const BulkRedispatch = fleet({ search: T4 }, async ({ canvasElement }) => {
  await selectT4(canvasElement);
  await userEvent.click(body(canvasElement).getByTestId("fleet-bulk-redispatch"));
  await body(canvasElement).findByTestId("intervention-dialog");
});

/** Bulk Message: the message is required and posted as the viewer, the PL notified. */
export const BulkMessage = fleet({ search: T4 }, async ({ canvasElement }) => {
  await selectT4(canvasElement);
  await userEvent.click(body(canvasElement).getByTestId("fleet-bulk-message"));
  await userEvent.type(
    await body(canvasElement).findByTestId("intervention-note"),
    "Seed knex_migrations in the test setup, then re-run.",
  );
});

export const BulkMessageDark = fleet({ theme: "dark", search: T4 }, BulkMessage.play);

export const BulkMessageMobile = phone(fleet({ search: T4 }, BulkMessage.play));

/** Reassign picks the other vendor. */
export const BulkReassign = fleet({ search: T4 }, async ({ canvasElement }) => {
  await selectT4(canvasElement);
  await userEvent.click(body(canvasElement).getByTestId("fleet-bulk-reassign"));
  await body(canvasElement).findByTestId("intervention-vendor-ic_codex");
});

// ── Scale and perf ─────────────────────────────────────────────

const scale = scaleAgentsResponse(60);

/** 60 agents across 12 projects: virtualized, still dense. */
export const Scale60 = fleet({
  snapshot: scale.snapshot,
  routes: { [AGENTS_PATH]: { body: scale.body } },
});

export const Scale60Dark = fleet({
  snapshot: scale.snapshot,
  routes: { [AGENTS_PATH]: { body: scale.body } },
  theme: "dark",
});

export const Scale60Mobile = phone(
  fleet({ snapshot: scale.snapshot, routes: { [AGENTS_PATH]: { body: scale.body } } }),
);

/**
 * Perf budget: 60 rows, one agent's "now" moving every second, under a React
 * Profiler. Each update commits within one frame and re-renders only that row.
 */
export const Perf60: Story = {
  render: () => (
    <BoardFrame width={1180}>
      <FleetPerfHarness snapshot={scale.snapshot} rows={scale.body.data} nowS={ROUND3_NOW_S} />
    </BoardFrame>
  ),
  play: async ({ canvasElement }) => {
    const perf = await body(canvasElement).findByTestId("fleet-perf");
    await waitFor(() => expect(Number(perf.dataset.tick)).toBeGreaterThanOrEqual(4), {
      timeout: 8000,
    });
    // A static (production) build's Profiler is inert: nothing to hold to the budget.
    if (perf.dataset.measured !== "true") return;
    await expect(Number(perf.dataset.commits)).toBeGreaterThanOrEqual(4);
    await expect(Number(perf.dataset.maxMs)).toBeLessThanOrEqual(COMMIT_BUDGET_MS);
  },
};

export const Perf60Dark: Story = {
  render: () => (
    <BoardFrame width={1180} theme="dark">
      <FleetPerfHarness snapshot={scale.snapshot} rows={scale.body.data} nowS={ROUND3_NOW_S} />
    </BoardFrame>
  ),
};

/** Long titles, tool arguments and summaries truncate in place. */
export const LongText = fleet({
  routes: {
    [AGENTS_PATH]: {
      body: (() => {
        const res = agentsResponse();
        for (const row of res.data) {
          row.title = `${row.title} with an unusually long worker name`;
          if (row.now?.tool_arg)
            row.now.tool_arg = `${row.now.tool_arg} --reporter=verbose --runInBand src/modules/db/version.test.ts`;
          if (row.summary)
            row.summary = `${row.summary}, and then a much longer explanation of what happened next`;
        }
        return res;
      })(),
    },
  },
});

export const LongTextMobile = phone(LongText);

// ── Empty, loading and errors ──────────────────────────────────

/** Atlas just started: only the leads. */
export const Empty = fleet({
  snapshot: emptyFactorySnapshot(),
  routes: { [AGENTS_PATH]: { body: { ...agentsResponse(), data: [], lines: [] } } },
});

export const Loading = fleet({ routes: { [AGENTS_PATH]: "pending" } });

export const LoadingMobile = phone(fleet({ routes: { [AGENTS_PATH]: "pending" } }));

/** The fleet read failed: the error with Retry, and the snapshot's members meanwhile. */
export const LoadError = fleet({
  routes: { [AGENTS_PATH]: { error: 503, message: "fleet unavailable" } },
});

/** A server without the fleet API: the snapshot's members, no task or loop columns. */
export const OlderServer = fleet({
  routes: { [AGENTS_PATH]: { error: 404, message: "not found" } },
});
