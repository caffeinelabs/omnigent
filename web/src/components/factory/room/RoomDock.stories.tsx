import type { Meta, StoryObj } from "@storybook/react-vite";
import { fireEvent, within } from "storybook/test";
import { FactoryLayout } from "@/pages/factory/FactoryLayout";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import { round3Events, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import {
  ROOM_FID,
  ROOM_NOW_S,
  type RoomRouteOptions,
  busyThread,
  roomRoutes,
  scaleSnapshot,
} from "./__fixtures__/roomFixtures";
import { DOCK_MIN_PX, DOCK_WIDTH_KEY, LAST_THREAD_KEY } from "./roomModel";

const NOW = new Date(ROOM_NOW_S * 1000);
const PR_9631 = "8f15c0f9a98e5831a39615fb58f35036";

function dock(
  path: string,
  options: CockpitStoryOptions & { room?: RoomRouteOptions; width?: number } = {},
  play?: Story["play"],
): Story {
  const { room, routes, width, ...rest } = options;
  return {
    render: () => {
      try {
        if (width) window.localStorage.setItem(DOCK_WIDTH_KEY, String(width));
        else window.localStorage.removeItem(DOCK_WIDTH_KEY);
        window.localStorage.removeItem("omnigent.factory.roomDock.tab");
        window.localStorage.removeItem(`${LAST_THREAD_KEY}.${ROOM_FID}`);
      } catch {
        // Storage off: the default width.
      }
      return (
        <CockpitStory
          snapshot={options.snapshot ?? round3Snapshot(room?.role ?? "creator")}
          events={round3Events()}
          path={path}
          now={NOW}
          routes={{ ...roomRoutes(ROOM_FID, room), ...routes }}
          {...rest}
        />
      );
    },
    play,
  };
}

function body(canvasElement: HTMLElement) {
  return within(canvasElement.ownerDocument.body);
}

const meta = {
  title: "Factory/Room dock",
  component: FactoryLayout,
  parameters: { layout: "fullscreen" },
  args: { children: null },
} satisfies Meta<typeof FactoryLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The Overview with the Room docked on #factory (capture at 1440 and at the 1280 breakpoint). */
export const Overview = dock("");

export const OverviewDark = dock("", { theme: "dark" });

/** On a project page the dock binds to #db-version. */
export const ProjectPage = dock("/projects/db-version");

export const ProjectPageDark = dock("/projects/db-version", { theme: "dark" });

/** On a PR page it binds to !9631. */
export const PrPage = dock(`/prs/${PR_9631}`);

/** The thread switcher open: every thread with its unread count. */
export const ThreadSwitcher = dock("", {}, async ({ canvasElement }) => {
  fireEvent.pointerDown(
    await body(canvasElement).findByTestId("room-thread-switcher", {}, { timeout: 6000 }),
    {
      button: 0,
      ctrlKey: false,
    },
  );
  await body(canvasElement).findByTestId("room-switch-project:db-version", {}, { timeout: 6000 });
});

/** The Decisions tab: the rail, this page's project first. */
export const DecisionsTab = dock("/projects/db-version", {}, async ({ canvasElement }) => {
  fireEvent.mouseDown(
    await body(canvasElement).findByTestId("room-dock-tab-decisions", {}, { timeout: 6000 }),
  );
  await body(canvasElement).findByTestId("decision-card-D-2", {}, { timeout: 6000 });
});

/** Dragged to its narrowest: cards and the composer still fit. */
export const Narrow = dock("/projects/db-version", { width: DOCK_MIN_PX });

/** Dragged wide. */
export const Wide = dock("", { width: 520 });

/** A viewer's dock: watch only. */
export const Viewer = dock("", { room: { role: "viewer" } });

/** A busy #factory at 60 agents. */
export const Busy = dock("", {
  snapshot: scaleSnapshot(60),
  room: { messages: busyThread("factory", 120) },
});

/** Below 1280 px (capture at 1024): a Room button with the unread count. */
export const SheetButtonTablet = dock("");

/** The sheet open below 1280 px. */
export const SheetOpenTablet = dock("", {}, async ({ canvasElement }) => {
  fireEvent.click(await body(canvasElement).findByTestId("room-sheet-open", {}, { timeout: 6000 }));
  await body(canvasElement).findByTestId("room-sheet", {}, { timeout: 6000 });
});

export const SheetOpenTabletDark = dock("", { theme: "dark" }, SheetOpenTablet.play);

/** On a phone the bottom tabs carry the Room with its unread badge. */
export const PhoneTabsMobile: Story = {
  ...dock(""),
  globals: { viewport: { value: "mobile2" } },
};
