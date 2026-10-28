import type { Meta, StoryObj } from "@storybook/react-vite";
import { fireEvent, userEvent, within } from "storybook/test";
import { FactoryLayout } from "@/pages/factory/FactoryLayout";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import { round3Events, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import {
  ROOM_FID,
  ROOM_NOW_S,
  type RoomRouteOptions,
  busyThread,
  roomMessages,
  roomRoutes,
  roomThreads,
  scaleSnapshot,
} from "./__fixtures__/roomFixtures";

const NOW = new Date(ROOM_NOW_S * 1000);
const DB_THREAD = "/room/project%3Adb-version";
const PR_THREAD = "/room/pr%3A8f15c0f9a98e5831a39615fb58f35036";

/** Message events the bus view interleaves (Atlas ⇄ Sentinel, PL → IC, a bounce). */
function busEvents() {
  const page = round3Events();
  const at = (min: number) => (ROOM_NOW_S - min * 60) * 1000;
  const mail = (
    id: string,
    min: number,
    from: string,
    to: string,
    kind: string,
    text: string,
    bounced = false,
  ) => ({
    id,
    ts: at(min),
    kind: bounced ? "message.bounced" : "message.queued",
    severity: bounced ? ("warning" as const) : ("info" as const),
    project_slug: null,
    from_id: from,
    to_id: to,
    from_title: from,
    to_title: to,
    subject: null,
    summary: text,
    data: { kind, excerpt: text },
  });
  return {
    ...page,
    data: [
      mail("bus-1", 2, "Atlas", "Lead B · Sentinel", "request", "verify !9631 after the knex fix"),
      mail(
        "bus-2",
        60,
        "Lead B · Sentinel",
        "Atlas",
        "reply",
        "!9631 verify FAIL (code: migrations)",
      ),
      mail("bus-3", 50, "PL · db-version", "ic:route-r2", "request", "re-run with knex seed"),
      mail("bus-4", 45, "Atlas", "PL · ux-badge", "info", "rebase on temp-main", true),
      ...page.data,
    ],
  };
}

function room(
  path: string,
  options: CockpitStoryOptions & { room?: RoomRouteOptions } = {},
  play?: Story["play"],
): Story {
  const { room: roomOptions, routes, ...rest } = options;
  const snapshot = options.snapshot ?? round3Snapshot(roomOptions?.role ?? "creator");
  return {
    render: () => {
      try {
        window.localStorage.removeItem("omnigent.factory.room.busView");
      } catch {
        // Storage off: the bus view starts off anyway.
      }
      return (
        <CockpitStory
          snapshot={snapshot}
          events={options.events ?? busEvents()}
          path={path}
          now={NOW}
          routes={{ ...roomRoutes(ROOM_FID, roomOptions), ...routes }}
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

async function composerOf(canvasElement: HTMLElement) {
  return (await body(canvasElement).findByTestId(
    "room-composer-input",
    {},
    { timeout: 6000 },
  )) as HTMLTextAreaElement;
}

const meta = {
  title: "Factory/Room",
  component: FactoryLayout,
  parameters: { layout: "fullscreen" },
  args: { children: null },
} satisfies Meta<typeof FactoryLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

// ── Live threads ───────────────────────────────────────────────

/** #factory: the brief, Atlas's answers, the C-4 proposal card, a decision card, people. */
export const Factory = room("/room");

export const FactoryDark = room("/room", { theme: "dark" });

export const FactoryMobile = phone(room("/room"));

/** #db-version: Mia's steer, the PL's answer, D-2 claimed and the exhausted loop with remedies. */
export const ProjectThread = room(DB_THREAD);

export const ProjectThreadDark = room(DB_THREAD, { theme: "dark" });

export const ProjectThreadMobile = phone(room(DB_THREAD));

/** !9631: the PR card with its gates, the PR's context on the right. */
export const PrThread = room(PR_THREAD);

/** #improvements: Sentinel's retro and the C-14 trial card. */
export const Improvements = room("/room/improvements");

/** Bus view on: message.* one-liners between the messages, a bounce in red. */
export const BusView = room("/room", {}, async ({ canvasElement }) => {
  fireEvent.click(await body(canvasElement).findByTestId("room-bus-toggle", {}, { timeout: 6000 }));
  await body(canvasElement).findByTestId("room-bus-bus-1", {}, { timeout: 6000 });
});

export const BusViewDark = room("/room", { theme: "dark" }, BusView.play);

/** Atlas is working with a message queued for it. */
export const ResponderWorking = room("/room", {
  snapshot: (() => {
    const snapshot = round3Snapshot();
    snapshot.members = snapshot.members.map((m) =>
      m.session_id === snapshot.factory.root_session_id ? { ...m, running: true } : m,
    );
    return snapshot;
  })(),
});

// ── Composer ───────────────────────────────────────────────────

/** `@pl:d` lists the agents and people it can mean. */
export const MentionMenu = room("/room", {}, async ({ canvasElement }) => {
  await userEvent.type(await composerOf(canvasElement), "@pl:d");
  await body(canvasElement).findByTestId("room-composer-menu", {}, { timeout: 6000 });
});

/** `/` lists the slash commands (no LLM: each calls its route). */
export const SlashMenu = room("/room", {}, async ({ canvasElement }) => {
  await userEvent.type(await composerOf(canvasElement), "/");
  await body(canvasElement).findByTestId("room-composer-menu", {}, { timeout: 6000 });
});

export const SlashMenuMobile = phone(room("/room", {}, SlashMenu.play));

/** A parsed command says what Enter will do. */
export const SlashParsed = room("/room", {}, async ({ canvasElement }) => {
  await userEvent.type(await composerOf(canvasElement), "/pause #db-version ");
  await body(canvasElement).findByTestId("room-composer-hint", {}, { timeout: 6000 });
});

/** A command that can't run says why. */
export const SlashRefused = room("/room", {}, async ({ canvasElement }) => {
  await userEvent.type(await composerOf(canvasElement), "/assign D-2 @nobody");
  await body(canvasElement).findByTestId("room-composer-hint", {}, { timeout: 6000 });
});

/** `/status`: the server's digest as a note only the viewer sees. */
export const StatusDigest = room("/room", {}, async ({ canvasElement }) => {
  await userEvent.type(await composerOf(canvasElement), "/status{enter}");
  await body(canvasElement).findByTestId("room-status-digest", {}, { timeout: 6000 });
});

export const StatusDigestDark = room("/room", { theme: "dark" }, StatusDigest.play);

// ── People ─────────────────────────────────────────────────────

/** Mia (operator): posting as Mia, D-2 is hers, the owners' C-15 apply is disabled. */
export const Operator = room("/room", { room: { role: "operator" } });

/** A viewer: the composer and every card lever disabled, with who can. */
export const Viewer = room("/room", { room: { role: "viewer" } });

export const ViewerMobile = phone(room("/room", { room: { role: "viewer" } }));

// ── Scale and edge cases ───────────────────────────────────────

/** 120 messages (older pages load on scroll) with long words and paths. */
export const LongThread = room("/room", { room: { messages: busyThread("factory", 120) } });

export const LongThreadMobile = phone(
  room("/room", { room: { messages: busyThread("factory", 120) } }),
);

/** 60 agents in the factory: the context and mentions stay usable. */
export const SixtyAgents = room(DB_THREAD, { snapshot: scaleSnapshot(60) });

/** A thread nobody wrote in yet. */
export const Empty = room("/room/project%3Aapi-version");

export const EmptyDark = room("/room/project%3Aapi-version", { theme: "dark" });

/** The thread is loading. */
export const Loading = room("/room", { room: { messages: "pending", threads: "pending" } });

/** The thread couldn't be read. */
export const LoadError = room("/room", { room: { messages: { error: 503 } } });

/** No threads yet besides the fixed ones (a factory just started). */
export const NewFactory = room("/room", {
  room: {
    // The thread being read has nothing unread.
    threads: roomThreads()
      .filter((t) => t.kind === "factory" || t.kind === "improvements")
      .map((t) => (t.kind === "factory" ? { ...t, unread: 0 } : t)),
    messages: roomMessages("factory").slice(0, 1),
  },
});

const projectThread = () => roomThreads().find((t) => t.kind === "project")!;

/** Many threads: 34 projects (long slugs too) and 18 PR threads; the list scrolls and truncates. */
export const ManyThreads = room("/room", {
  room: {
    threads: [
      ...roomThreads(),
      ...Array.from({ length: 28 }, (_, i) => ({
        ...projectThread(),
        key: `project:scale-${i}`,
        title: `#${i % 5 === 0 ? `a-rather-long-project-slug-number-${i}` : `scale-${i}`}`,
        project_slug: `scale-${i}`,
        unread: i % 7 === 0 ? 12 : 0,
      })),
      ...Array.from({ length: 18 }, (_, i) => ({
        ...projectThread(),
        key: `pr:scale${String(i).padStart(28, "0")}`,
        kind: "pr" as const,
        title: `!${9700 + i}`,
        project_slug: `scale-${i}`,
        pr_number: 9700 + i,
        unread: i % 5 === 0 ? 3 : 0,
      })),
    ],
  },
});
