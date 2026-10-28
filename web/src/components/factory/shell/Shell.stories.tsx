import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import { FactoryLayout } from "@/pages/factory/FactoryLayout";
import { CockpitStory, type CockpitStoryOptions, SNAPSHOT_PATH } from "../board/boardStoryKit";
import {
  round3AuditEvents,
  round3Events,
  round3People,
  round3Snapshot,
} from "./__fixtures__/shellFixtures";

const PEOPLE_PATH = `${SNAPSHOT_PATH}/people`;

function shell(options: CockpitStoryOptions = {}, play?: Story["play"]): Story {
  return {
    render: () => <CockpitStory snapshot={round3Snapshot()} events={round3Events()} {...options} />,
    play,
  };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

function body(canvasElement: HTMLElement) {
  return within(canvasElement.ownerDocument.body);
}

/** Open ⌘K from the tabs' button, or from the phone's More sheet. */
async function openPalette(canvasElement: HTMLElement) {
  const screen = body(canvasElement);
  await screen.findByTestId("factory-shell");
  const button = screen.queryByTestId("factory-palette-open");
  if (button) {
    await userEvent.click(button);
  } else {
    await userEvent.click(await screen.findByTestId("factory-phone-more"));
    await userEvent.click(await screen.findByText("Jump or act…"));
  }
  return screen.findByTestId("factory-palette-input");
}

const meta = {
  title: "Factory/Shell",
  component: FactoryLayout,
  parameters: { layout: "fullscreen" },
  args: { children: null },
} satisfies Meta<typeof FactoryLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

// ── ⌘K ─────────────────────────────────────────────────────────

/** ⌘K: pages, projects, PRs, tasks, open decisions and agents from the snapshot. */
export const Palette = shell({}, async ({ canvasElement }) => {
  await openPalette(canvasElement);
});

export const PaletteDark = shell({ theme: "dark" }, Palette.play);

/** Typing narrows across every kind: "db" finds the project, its PR, D-2 and the agents. */
export const PaletteFiltered = shell({}, async ({ canvasElement }) => {
  await userEvent.type(await openPalette(canvasElement), "db");
});

/** `/` lists the slash commands. */
export const PaletteCommands = shell({}, async ({ canvasElement }) => {
  await userEvent.type(await openPalette(canvasElement), "/");
});

/** A viewer's `/pause #db-version`: the command is named but disabled, with who can. */
export const PaletteCommandViewer = shell(
  { snapshot: round3Snapshot("viewer") },
  async ({ canvasElement }) => {
    await userEvent.type(await openPalette(canvasElement), "/pause #db-version");
  },
);

export const PaletteMobile = phone(shell({}, Palette.play));

// ── Share and People ───────────────────────────────────────────

/** Share as the creator: invite, roles, presence, Remove, the credential line, Copy link. */
export const Share = shell({}, async ({ canvasElement }) => {
  await userEvent.click(await body(canvasElement).findByTestId("factory-share"));
  await body(canvasElement).findByTestId("people-roster");
});

export const ShareDark = shell({ theme: "dark" }, Share.play);

export const ShareMobile = phone(shell({}, Share.play));

/** Inviting someone whose domain can't sign in: the admission warning. */
export const ShareInviteBlocked = shell(
  {
    routes: {
      [`${PEOPLE_PATH}/joe%40partner.io`]: {
        body: {
          person: {
            ...round3People().data[2]!,
            user_id: "joe@partner.io",
            name: "joe@partner.io",
            role: "operator",
          },
          created: true,
          previous_role: null,
          granted: 6,
          admission: "blocked",
        },
      },
    },
  },
  async ({ canvasElement }) => {
    const screen = body(canvasElement);
    await userEvent.click(await screen.findByTestId("factory-share"));
    await userEvent.type(await screen.findByTestId("people-invite-email"), "joe@partner.io");
    await userEvent.click(screen.getByTestId("people-invite-submit"));
    await screen.findByTestId("people-invite-blocked");
  },
);

/** Share as an operator: the roster to read, inviting is for owners, Leave is theirs. */
export const ShareOperator = shell(
  {
    snapshot: round3Snapshot("operator"),
    routes: { [PEOPLE_PATH]: { body: round3People("operator") } },
  },
  Share.play,
);

export const ShareLoading = shell(
  { routes: { [PEOPLE_PATH]: "pending" } },
  async ({ canvasElement }) => {
    await userEvent.click(await body(canvasElement).findByTestId("factory-share"));
  },
);

export const ShareError = shell(
  { routes: { [PEOPLE_PATH]: { error: 503 } } },
  async ({ canvasElement }) => {
    await userEvent.click(await body(canvasElement).findByTestId("factory-share"));
    // A 5xx is retried once before the error shows.
    await body(canvasElement).findByTestId("share-error", {}, { timeout: 6000 });
  },
);

/** Settings ▸ People: the roster and the audit log of people and control events. */
export const SettingsPeople = shell({ path: "/settings/people", events: round3AuditEvents() });

export const SettingsPeopleDark = shell({
  path: "/settings/people",
  events: round3AuditEvents(),
  theme: "dark",
});

export const SettingsPeopleMobile = phone(
  shell({ path: "/settings/people", events: round3AuditEvents() }),
);

/** No audit entries yet. */
export const SettingsPeopleEmpty = shell({
  path: "/settings/people",
  events: { data: [], has_more: false },
});

// ── Pages of the shell ─────────────────────────────────────────

/** The Room's page until it lands: the thread's slot and the decisions. */
export const RoomPage = shell({ path: "/room" });

export const RoomPageMobile = phone(shell({ path: "/room/project%3Adb-version" }));

/** The phone's More sheet: Overview, Activity, People, Share, Jump or act. */
export const MoreSheetMobile = phone(
  shell({}, async ({ canvasElement }) => {
    await userEvent.click(await body(canvasElement).findByTestId("factory-phone-more"));
  }),
);

/** A factory path this build has no page for. */
export const UnknownPage = shell({ path: "/graph" });

/** The viewer's Request access sent: the decision ref the owners will answer. */
export const RequestAccessSent = shell(
  {
    snapshot: round3Snapshot("viewer"),
    routes: {
      [`${SNAPSHOT_PATH}/access-requests`]: {
        body: { decision: { ...round3Snapshot().decisions[4]!, ref: "D-34" }, deduped: false },
      },
    },
  },
  async ({ canvasElement }) => {
    await userEvent.click(await body(canvasElement).findByTestId("factory-request-access"));
    await body(canvasElement).findByTestId("factory-access-requested");
  },
);
