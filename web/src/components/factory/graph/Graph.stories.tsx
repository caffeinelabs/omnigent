import type { Meta, StoryObj } from "@storybook/react-vite";
import { fireEvent, within } from "storybook/test";
import type { ViewerRole } from "@/lib/factoryApi";
import { FactoryLayout } from "@/pages/factory/FactoryLayout";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import {
  ROOM_FID,
  ROOM_NOW_S,
  type RoomRouteOptions,
  orgGraphOf,
  roomRoutes,
  scaleSnapshot,
  workGraph40,
} from "../room/__fixtures__/roomFixtures";
import { round3Events, round3Snapshot } from "../shell/__fixtures__/shellFixtures";

const NOW = new Date(ROOM_NOW_S * 1000);
const GRAPH = `/v1/factory/factories/${ROOM_FID}/graph`;

function graph(
  search: string,
  options: CockpitStoryOptions & { room?: RoomRouteOptions; role?: ViewerRole } = {},
  play?: Story["play"],
): Story {
  const { room, role = "creator", routes, ...rest } = options;
  return {
    render: () => {
      try {
        window.localStorage.removeItem(`omnigent.factory.room.lastThread.${ROOM_FID}`);
      } catch {
        // Storage off: the dock starts on #factory anyway.
      }
      return (
        <CockpitStory
          snapshot={options.snapshot ?? round3Snapshot(role)}
          events={round3Events()}
          path="/graph"
          search={search}
          now={NOW}
          routes={{ ...roomRoutes(ROOM_FID, { role, ...room }), ...routes }}
          {...rest}
        />
      );
    },
    play,
  };
}

const phone = (story: Story): Story => ({ ...story, globals: { viewport: { value: "mobile2" } } });

function body(canvasElement: HTMLElement) {
  return within(canvasElement.ownerDocument.body);
}

const meta = {
  title: "Factory/Graph",
  component: FactoryLayout,
  parameters: { layout: "fullscreen" },
  args: { children: null },
} satisfies Meta<typeof FactoryLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

// ── Work ───────────────────────────────────────────────────────

/** The round-3 tasks: waves, the critical path bold, T-4 blocked on D-31. */
export const Work = graph("");

export const WorkDark = graph("", { theme: "dark" });

export const WorkMobile = phone(graph(""));

/** 40 tasks over 4 projects: 5 waves, ready rings, a serial pair, a project dependency. */
export const Work40 = graph("", { room: { work: workGraph40() } });

export const Work40Dark = graph("", { theme: "dark", room: { work: workGraph40() } });

/** A task selected: its inspector with Edit card, + Dependency, Split, Cancel. */
export const WorkTaskSelected = graph("?task=T-4");

export const WorkTaskSelectedDark = graph("?task=T-4", { theme: "dark" });

export const WorkTaskSelectedMobile = phone(graph("?task=T-4"));

/** A viewer: the task's edits disabled, with who can. */
export const WorkViewer = graph("?task=T-4", { role: "viewer" });

/** + Dependency: T-4 waiting on T-6 (which waits on T-4) is a cycle, blocked before it is sent. */
export const WorkAddDependency = graph("?task=T-4", {}, async ({ canvasElement }) => {
  const page = body(canvasElement);
  fireEvent.click(await page.findByTestId("graph-task-dependency", {}, { timeout: 6000 }));
  const dialog = await page.findByTestId("task-edit-dependency", {}, { timeout: 6000 });
  fireEvent.keyDown(within(dialog).getByTestId("task-edit-dep"), { key: "ArrowDown" });
  fireEvent.click(await page.findByRole("option", { name: /^T-6\b/ }, { timeout: 6000 }));
  await within(dialog).findByTestId("task-edit-check", {}, { timeout: 6000 });
});

/** + Dependency, nothing picked yet: the picker. */
export const WorkAddDependencyPicker = graph("?task=T-4", {}, async ({ canvasElement }) => {
  fireEvent.click(
    await body(canvasElement).findByTestId("graph-task-dependency", {}, { timeout: 6000 }),
  );
  await body(canvasElement).findByTestId("task-edit-dependency", {}, { timeout: 6000 });
});

/** Split task: two steps the task then waits on. */
export const WorkSplit = graph("?task=T-6", {}, async ({ canvasElement }) => {
  fireEvent.click(
    await body(canvasElement).findByTestId("graph-task-split", {}, { timeout: 6000 }),
  );
  const dialog = await body(canvasElement).findByTestId("task-edit-split", {}, { timeout: 6000 });
  fireEvent.change(within(dialog).getByTestId("task-split-0"), {
    target: { value: "Settings row markup" },
  });
  fireEvent.change(within(dialog).getByTestId("task-split-1"), {
    target: { value: "Settings row data" },
  });
});

/** Edit card with a scope that overlaps another task: warned before saving. */
export const WorkEditCardWarning = graph("?task=T-6", {}, async ({ canvasElement }) => {
  fireEvent.click(await body(canvasElement).findByTestId("graph-task-card", {}, { timeout: 6000 }));
  const dialog = await body(canvasElement).findByTestId("task-edit-card", {}, { timeout: 6000 });
  fireEvent.change(within(dialog).getByTestId("task-edit-scope"), {
    target: { value: "services/caffeine-api/src/modules/db/**" },
  });
});

/** A project's dependencies, proposed as a change in #factory. */
export const WorkProjectDepends = graph("", {}, async ({ canvasElement }) => {
  fireEvent.click(
    await body(canvasElement).findByTestId("graph-depends-ux-badge", {}, { timeout: 6000 }),
  );
  await body(canvasElement).findByTestId("project-depends-dialog", {}, { timeout: 6000 });
});

/** One project's graph. */
export const WorkOneProject = graph("?scope=db-version");

/** No tasks yet. */
export const WorkEmpty = graph("", {
  room: {
    work: {
      ...workGraph40(),
      projects: [],
      tasks: [],
      layers: {},
      edges: [],
      external_edges: [],
      project_edges: [],
      warnings: [],
    },
  },
});

export const Loading = graph("", { routes: { [GRAPH]: "pending" } });

export const LoadError = graph("", { routes: { [GRAPH]: { error: 503 } } });

// ── Bus, flow, org ─────────────────────────────────────────────

/** 12 agents and people: width by traffic, dashed bounces, red waits on humans, ping-pong and orphan. */
export const Bus = graph("?kind=bus&window=6h");

export const BusDark = graph("?kind=bus&window=6h", { theme: "dark" });

export const BusMobile = phone(graph("?kind=bus&window=6h"));

/** Spawn and supervise edges on. */
export const BusStructure = graph("?kind=bus&window=6h", {}, async ({ canvasElement }) => {
  fireEvent.click(await body(canvasElement).findByTestId("graph-structure", {}, { timeout: 6000 }));
});

/** A quiet hour: no messages. */
export const BusEmpty = graph("?kind=bus", {
  room: {
    bus: {
      kind: "bus",
      factory_id: ROOM_FID,
      project: null,
      generated_at: ROOM_NOW_S,
      since_ms: (ROOM_NOW_S - 3600) * 1000,
      until_ms: ROOM_NOW_S * 1000,
      nodes: [],
      edges: [],
    },
  },
});

/** The PR pipeline with counts, p50 dwell and $, and loops back. */
export const Flow = graph("?kind=flow");

export const FlowDark = graph("?kind=flow", { theme: "dark" });

export const FlowMobile = phone(graph("?kind=flow"));

/** 41 agents, every depth, one column per project. */
export const Org = graph("?kind=org");

export const OrgDark = graph("?kind=org", { theme: "dark" });

/** 60 agents. */
export const Org60 = graph("?kind=org", {
  snapshot: scaleSnapshot(60),
  room: { org: orgGraphOf(scaleSnapshot(60)) },
});

export const OrgMobile = phone(graph("?kind=org"));
