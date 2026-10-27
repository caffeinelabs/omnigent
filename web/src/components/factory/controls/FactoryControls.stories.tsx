import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { userEvent, waitFor, within } from "storybook/test";
import { Toaster } from "@/components/ui/sonner";
import { FactoryPage } from "@/pages/factory/FactoryPage";
import type { EventPage, EventView, FactorySnapshot } from "@/lib/factoryApi";
import { LIVE_RUN_NOW_S, LIVE_RUN_PL_API_ID, LIVE_RUN_PL_DB_ID } from "../__fixtures__/liveRun";
import {
  boardActivityEvents,
  pageScenario,
  storySnapshot,
} from "../board/__fixtures__/boardFixtures";
import { CockpitStory, type CockpitStoryOptions, SNAPSHOT_PATH } from "../board/boardStoryKit";
import { GITHUB_STORY_ROUTES } from "../start/startStoryRoutes";
import { resetFactoryControls } from "./useFactoryControls";

const CONTROL_PATH = `${SNAPSHOT_PATH}/control`;
const NOW_MS = LIVE_RUN_NOW_S * 1000;

/** The db-version PL was launched in an older permission mode than its spec now says. */
function plDriftSnapshot(): FactorySnapshot {
  const snapshot = storySnapshot();
  const drift = {
    fields: ["permission_mode"],
    stored: { permission_mode: "auto" },
    spec: { permission_mode: "bypassPermissions" },
  };
  for (const project of snapshot.projects) {
    if (project.pl?.session_id === LIVE_RUN_PL_DB_ID) {
      project.pl = { ...project.pl, has_launch_drift: true };
    }
  }
  snapshot.members = snapshot.members.map((m) =>
    m.session_id === LIVE_RUN_PL_DB_ID ? { ...m, launch_drift: drift } : m,
  );
  snapshot.health.drifted_members += 1;
  return snapshot;
}

function controlEvent(
  id: string,
  action: string,
  minutesAgo: number,
  slug = "api-version",
  recipient = LIVE_RUN_PL_API_ID,
): EventView {
  return {
    id,
    ts: NOW_MS - minutesAgo * 60_000,
    kind: `control.${action}`,
    severity: "notice",
    project_slug: slug,
    from_id: null,
    to_id: recipient,
    from_title: null,
    to_title: `PL · ${slug}`,
    subject: `project:${slug}`,
    summary: `Luka ${action}d project ${slug}`,
    data: {
      by: "Luka",
      action,
      target: `project:${slug}`,
      options: action === "pause" ? { keep_previews: true } : {},
      recipients: [recipient],
    },
  };
}

function ackFor(control: EventView, minutesAgo: number): EventView {
  return {
    ...control,
    id: `${control.id}-ack`,
    ts: NOW_MS - minutesAgo * 60_000,
    kind: "control.ack",
    severity: "info",
    from_id: control.to_id,
    from_title: control.to_title,
    to_id: null,
    to_title: null,
    summary: "pause acknowledged",
    data: { control_event_id: control.id, action: "pause" },
  };
}

/** The board's activity with `controls` as its only control events, newest first. */
function eventsWith(...controls: EventView[]): EventPage {
  const page = boardActivityEvents();
  const rest = page.data.filter((e) => !e.kind.startsWith("control."));
  return { ...page, data: [...controls, ...rest].sort((a, b) => b.ts - a.ts) };
}

/** The server's answer to a control POST: its `control.<action>` event. */
function acceptedControl(action: string): CockpitStoryOptions["routes"] {
  return {
    [CONTROL_PATH]: {
      status: 202,
      body: { events: [controlEvent(`ev-story-${action}`, action, 0)], mail_ids: {} },
    },
  };
}

/** Clicks from an earlier story would otherwise show as pending chips here. */
function FreshControls({ routes, ...props }: CockpitStoryOptions) {
  useState(() => {
    resetFactoryControls();
    return null;
  });
  return (
    <>
      <CockpitStory routes={{ ...GITHUB_STORY_ROUTES, ...routes }} {...props} />
      <Toaster position="bottom-right" />
    </>
  );
}

function cockpit(options: CockpitStoryOptions = {}, play?: Story["play"]): Story {
  return { render: () => <FreshControls {...options} />, play };
}

function screenOf(canvasElement: HTMLElement) {
  return within(canvasElement.ownerDocument.body);
}

async function openProjectMenu(canvasElement: HTMLElement, slug: string) {
  const screen = screenOf(canvasElement);
  await userEvent.click(await screen.findByTestId(`project-controls-${slug}`));
  await screen.findByTestId("control-pause");
  return screen;
}

async function openHeaderMenu(canvasElement: HTMLElement) {
  const screen = screenOf(canvasElement);
  await userEvent.click(await screen.findByTestId("factory-controls-menu"));
  await screen.findByTestId("control-cadence");
  return screen;
}

const meta = {
  title: "Factory/Controls",
  component: FactoryPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof FactoryPage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A project's ⋯ menu: every lever is a server control; the PL's launch matches its spec. */
export const ProjectMenu = cockpit({}, async ({ canvasElement }) => {
  await openProjectMenu(canvasElement, "api-version");
});

export const ProjectMenuDark = cockpit({ theme: "dark" }, async ({ canvasElement }) => {
  await openProjectMenu(canvasElement, "api-version");
});

/** The PL runs an older launch mode: Relaunch is highlighted with the drifted fields. */
export const ProjectMenuDrift = cockpit(
  { snapshot: plDriftSnapshot() },
  async ({ canvasElement }) => {
    await openProjectMenu(canvasElement, "db-version");
  },
);

export const ProjectMenuDriftDark = cockpit(
  { snapshot: plDriftSnapshot(), theme: "dark" },
  async ({ canvasElement }) => {
    await openProjectMenu(canvasElement, "db-version");
  },
);

/** "Switch preview to PR ▸": deploy an open PR's head, or take a live preview down. */
export const ProjectMenuPreviewSwitch = cockpit({}, async ({ canvasElement }) => {
  const screen = await openProjectMenu(canvasElement, "api-version");
  await userEvent.click(screen.getByTestId("control-preview"));
  await screen.findByTestId("control-preview-down-9630");
});

/** The header ⋯: Atlas drifted (the live-run case), so its relaunch leads the menu. */
export const HeaderMenuDrift = cockpit({}, async ({ canvasElement }) => {
  await openHeaderMenu(canvasElement);
});

export const HeaderMenuDriftDark = cockpit({ theme: "dark" }, async ({ canvasElement }) => {
  await openHeaderMenu(canvasElement);
});

/** Cadence ▸ with what each costs in heartbeat turns. */
export const HeaderMenuCadence = cockpit({}, async ({ canvasElement }) => {
  const screen = await openHeaderMenu(canvasElement);
  await userEvent.click(screen.getByTestId("control-cadence"));
  await screen.findByTestId("control-cadence-estimate");
});

/** The team strip's "old launch mode" chip opens the same relaunch confirm. */
export const DriftChipRelaunch = cockpit(
  { snapshot: plDriftSnapshot() },
  async ({ canvasElement }) => {
    const screen = screenOf(canvasElement);
    await userEvent.click(await screen.findByTestId(`launch-drift-chip-${LIVE_RUN_PL_DB_ID}`));
    await screen.findByTestId("control-relaunch-dialog");
  },
);

/** Pause: what happens, and the "also tear down previews" choice (previews stay by default). */
export const PauseDialog = cockpit({}, async ({ canvasElement }) => {
  const screen = await openProjectMenu(canvasElement, "api-version");
  await userEvent.click(screen.getByTestId("control-pause"));
  await screen.findByTestId("control-pause-dialog");
});

export const PauseDialogDark = cockpit({ theme: "dark" }, async ({ canvasElement }) => {
  const screen = await openProjectMenu(canvasElement, "api-version");
  await userEvent.click(screen.getByTestId("control-pause"));
  await screen.findByTestId("control-pause-dialog");
});

/** Pause all from the header. */
export const PauseAllDialog = cockpit({}, async ({ canvasElement }) => {
  const screen = screenOf(canvasElement);
  await userEvent.click(await screen.findByTestId("factory-pause-all"));
  await screen.findByTestId("control-pause-dialog");
});

/** Confirmed: the card says what it waits for, and Undo stays up for 5 s. */
export const Pausing = cockpit({ routes: acceptedControl("pause") }, async ({ canvasElement }) => {
  const screen = await openProjectMenu(canvasElement, "api-version");
  await userEvent.click(screen.getByTestId("control-pause"));
  await userEvent.click(await screen.findByTestId("control-confirm"));
  await waitFor(() =>
    screen.getByTestId("control-pending-api-version").dataset.state === "waiting"
      ? Promise.resolve()
      : Promise.reject(new Error("still sending")),
  );
  await screen.findByTestId("control-undo");
});

export const PausingDark = cockpit(
  { routes: acceptedControl("pause"), theme: "dark" },
  async ({ canvasElement }) => {
    const screen = await openProjectMenu(canvasElement, "api-version");
    await userEvent.click(screen.getByTestId("control-pause"));
    await userEvent.click(await screen.findByTestId("control-confirm"));
    await screen.findByTestId("control-undo");
  },
);

const LATE_PAUSE = controlEvent("ev-story-late", "pause", 20);

/** No reply in 15 min: the chip turns amber and offers a restart. */
export const PauseNoReply = cockpit({ events: eventsWith(LATE_PAUSE) });

const ACKED_PAUSE = controlEvent("ev-story-acked", "pause", 3);

/** The PL replied: the chip confirms, then goes away after a minute. */
export const PauseAcknowledged = cockpit({
  snapshot: pageScenario("paused"),
  events: eventsWith(ACKED_PAUSE, ackFor(ACKED_PAUSE, 0.5)),
});

/** Refused (no edit access): a toast and the chip say why. */
export const ControlRefused = cockpit(
  { routes: { [CONTROL_PATH]: { error: 403, message: "forbidden" } } },
  async ({ canvasElement }) => {
    const screen = await openProjectMenu(canvasElement, "api-version");
    await userEvent.click(screen.getByTestId("control-env-status"));
    await waitFor(() =>
      screen.getByTestId("control-pending-api-version").dataset.state === "error"
        ? Promise.resolve()
        : Promise.reject(new Error("not refused yet")),
    );
  },
);

/** Stop a project: type its slug to confirm. */
export const StopProjectConfirm = cockpit({}, async ({ canvasElement }) => {
  const screen = await openProjectMenu(canvasElement, "api-version");
  await userEvent.click(screen.getByTestId("control-stop"));
  await userEvent.type(await screen.findByTestId("control-confirm-input"), "api-ver");
});

export const StopProjectConfirmDark = cockpit({ theme: "dark" }, async ({ canvasElement }) => {
  const screen = await openProjectMenu(canvasElement, "api-version");
  await userEvent.click(screen.getByTestId("control-stop"));
  await userEvent.type(await screen.findByTestId("control-confirm-input"), "api-version");
});

/** Stop the factory: type its name. */
export const StopFactoryConfirm = cockpit({}, async ({ canvasElement }) => {
  const screen = await openHeaderMenu(canvasElement);
  await userEvent.click(screen.getByTestId("control-stop-factory"));
  await screen.findByTestId("control-confirm-input");
});

/** Restart the PL with a note it reads first. */
export const RestartDialog = cockpit({}, async ({ canvasElement }) => {
  const screen = await openProjectMenu(canvasElement, "db-version");
  await userEvent.click(screen.getByTestId("control-restart"));
  await userEvent.type(
    await screen.findByTestId("control-note"),
    "The migration test was flaky; re-run it first.",
  );
});

/** Relaunch with spec mode, showing the stored and spec launch args. */
export const RelaunchDialog = cockpit(
  { snapshot: plDriftSnapshot() },
  async ({ canvasElement }) => {
    const screen = await openProjectMenu(canvasElement, "db-version");
    await userEvent.click(screen.getByTestId("control-relaunch"));
    await screen.findByTestId("control-drift-table");
  },
);

/** Paused and failed cards carry their next step inline. */
export const PausedCard = cockpit({ snapshot: pageScenario("paused") });

export const FailedCard = cockpit({ snapshot: pageScenario("failed") });

/** Add project: the start form's project section, prefilled with the factory's repo. */
export const AddProject = cockpit({}, async ({ canvasElement }) => {
  const screen = screenOf(canvasElement);
  await userEvent.click(await screen.findByTestId("factory-add-project"));
  await screen.findByTestId("add-project-dialog");
});

export const AddProjectDark = cockpit({ theme: "dark" }, async ({ canvasElement }) => {
  const screen = screenOf(canvasElement);
  await userEvent.click(await screen.findByTestId("factory-add-project"));
  await screen.findByTestId("add-project-dialog");
});

/** Submitted empty: each problem inline, nothing sent. */
export const AddProjectErrors = cockpit({}, async ({ canvasElement }) => {
  const screen = screenOf(canvasElement);
  await userEvent.click(await screen.findByTestId("factory-add-project"));
  await userEvent.type(await screen.findByTestId("add-project-project-title-0"), "API version");
  await userEvent.click(screen.getByTestId("add-project-submit"));
});

/** Edit charter from the project drawer: title, repo and env are fixed once added. */
export const EditCharter = cockpit(
  { search: "?project=api-version" },
  async ({ canvasElement }) => {
    const screen = screenOf(canvasElement);
    await userEvent.click(await screen.findByTestId("charter-edit"));
    await screen.findByTestId("edit-charter-dialog");
  },
);

/** The diff the PL receives, before it is sent. */
export const EditCharterReview = cockpit(
  { search: "?project=api-version" },
  async ({ canvasElement }) => {
    const screen = screenOf(canvasElement);
    await userEvent.click(await screen.findByTestId("charter-edit"));
    const goal = await screen.findByTestId("charter-goal-0");
    await userEvent.clear(goal);
    await userEvent.type(goal, "Expose GET /version on caffeine-api with the short git sha.");
    await userEvent.click(screen.getByTestId("charter-slots-0-3"));
    await userEvent.click(screen.getByTestId("charter-review"));
    await screen.findByTestId("charter-diff");
  },
);

export const EditCharterReviewDark = cockpit(
  { search: "?project=api-version", theme: "dark" },
  async ({ canvasElement }) => {
    const screen = screenOf(canvasElement);
    await userEvent.click(await screen.findByTestId("charter-edit"));
    const goal = await screen.findByTestId("charter-goal-0");
    await userEvent.clear(goal);
    await userEvent.type(goal, "Expose GET /version on caffeine-api with the short git sha.");
    await userEvent.click(screen.getByTestId("charter-review"));
    await screen.findByTestId("charter-diff");
  },
);
