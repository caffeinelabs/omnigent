import type { Meta, StoryObj } from "@storybook/react-vite";
import { TeamsPage } from "@/pages/factory/TeamsPage";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import {
  TEAMS_NOW_S,
  benchResponse,
  scaleTeamsSnapshot,
  teamsAgentRows,
  teamsRoutes,
  teamsSnapshot,
} from "./__fixtures__/teamsFixtures";

/** Teams in the new cockpit (`?cockpit=v2`, the server flag on), over the sample factory. */
function teams(options: CockpitStoryOptions & { tab?: string; query?: string } = {}): Story {
  const snapshot = options.snapshot ?? teamsSnapshot();
  const { tab, query, routes, ...rest } = options;
  const search = new URLSearchParams(query ?? "");
  search.set("cockpit", "v2");
  return {
    render: () => (
      <CockpitStory
        snapshot={snapshot}
        path={tab ? `/teams/${tab}` : "/teams"}
        search={`?${search.toString()}`}
        routes={{ ...teamsRoutes(snapshot), ...routes }}
        {...rest}
      />
    ),
  };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

const meta = {
  title: "Factory/Teams",
  component: TeamsPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof TeamsPage>;

export default meta;
type Story = StoryObj<typeof meta>;

// ── Teams ──────────────────────────────────────────────────────

/** The lead seats, then one card per project team: ring, name, role, vendor · model, state. */
export const Live = teams();

export const LiveDark = teams({ theme: "dark" });

export const LiveMobile = phone(teams());

/** "codex" matches every member on Codex: the filter reads name, role, vendor, model, state, project. */
export const FilterCodex = teams({ query: "q=codex" });

export const FilterCodexDark = teams({ query: "q=codex", theme: "dark" });

export const FilterCodexMobile = phone(teams({ query: "q=codex" }));

/** The "1 stuck" count is a filter too. */
export const FilterStuck = teams({ query: "state=stuck" });

/** "reviewer sonnet": no reviewer runs sonnet. */
export const FilterNoMatch = teams({ query: "q=reviewer%20sonnet" });

export const FilterNoMatchMobile = phone(teams({ query: "q=reviewer%20sonnet" }));

/** 60 agents across 12 project teams. */
export const Scale60 = teams({ snapshot: scaleTeamsSnapshot(12, 60) });

export const Scale60Dark = teams({ snapshot: scaleTeamsSnapshot(12, 60), theme: "dark" });

export const Scale60Mobile = phone(teams({ snapshot: scaleTeamsSnapshot(12, 60) }));

/** A lead's helper (stuck here) has no project team: it shows on the Other agents card. */
const helperRows = (() => {
  const rows = teamsAgentRows(teamsSnapshot());
  const atlas = rows.find((r) => r.handle === "atlas")!;
  const ivy = rows.find((r) => r.handle === "ivy")!;
  return [
    ...rows,
    {
      ...ivy,
      session_id: "5b000000000000000000000000000002",
      handle: "lark",
      title: "(sample) helper: tidy the backlog",
      summary: "(sample) Grouping open ideas by area",
      project: null,
      parent_session_id: atlas.session_id,
      task: null,
      stuck: true,
      stuck_reason: "running with no new activity for 20 min",
      stuck_since: TEAMS_NOW_S - 20 * 60,
      stuck_issue: "INC-9",
    },
  ];
})();

export const OtherAgents = teams({ routes: teamsRoutes(teamsSnapshot(), { rows: helperRows }) });

export const OtherAgentsDark = teams({
  routes: teamsRoutes(teamsSnapshot(), { rows: helperRows }),
  theme: "dark",
});

export const OtherAgentsMobile = phone(
  teams({ routes: teamsRoutes(teamsSnapshot(), { rows: helperRows }) }),
);

/** The fleet read is slow: skeleton cards keep the layout. */
export const Loading = teams({
  routes: { [`/v1/factory/factories/${teamsSnapshot().factory.id}/agents`]: "pending" },
});

// ── Bench ──────────────────────────────────────────────────────

/** Track record by role × vendor · model with n; rows under five samples are grey. */
export const Bench = teams({ tab: "bench" });

export const BenchDark = teams({ tab: "bench", theme: "dark" });

export const BenchMobile = phone(teams({ tab: "bench" }));

/** Before work is stamped with its model: cost by model only, and it says so. */
export const BenchCostOnly = teams({
  tab: "bench",
  routes: teamsRoutes(teamsSnapshot(), { bench: benchResponse(false) }),
});

export const BenchCostOnlyDark = teams({
  tab: "bench",
  theme: "dark",
  routes: teamsRoutes(teamsSnapshot(), { bench: benchResponse(false) }),
});

// ── Templates and All agents ───────────────────────────────────

/** Templates live in Steer: the tab says so and links there. */
export const Templates = teams({ tab: "templates" });

export const TemplatesDark = teams({ tab: "templates", theme: "dark" });

export const TemplatesMobile = phone(teams({ tab: "templates" }));

/** Every agent in a table with a model column. */
export const AllAgents = teams({ tab: "agents" });

export const AllAgentsDark = teams({ tab: "agents", theme: "dark" });

export const AllAgentsMobile = phone(teams({ tab: "agents" }));

// ── The agent panel (opened from a member) ─────────────────────

const peek = (handle: string, extra: CockpitStoryOptions = {}) =>
  teams({ query: `peek=agent%3A${handle}`, ...extra });

/** Stuck: same failing call three times; the incident is open and nothing needs you. */
export const PanelStuck = peek("dot");

export const PanelStuckDark = peek("dot", { theme: "dark" });

export const PanelStuckMobile = phone(peek("dot"));

/** Working: what it runs now and for how long. */
export const PanelWorking = peek("ivy");

export const PanelWorkingDark = peek("ivy", { theme: "dark" });

export const PanelWorkingMobile = phone(peek("ivy"));

/** Idle, and that is fine: the project waits for a person to merge. */
export const PanelIdleMergeWait = peek("juno");

export const PanelIdleMergeWaitDark = peek("juno", { theme: "dark" });

export const PanelIdleMergeWaitMobile = phone(peek("juno"));

/** Waiting on you: the project lead asked a question only a person can answer. */
const waitingSnapshot = (() => {
  const snapshot = teamsSnapshot();
  const nova = snapshot.members.find((m) => m.handle === "nova")!;
  const template = round3Snapshot().decisions.find((d) => d.status === "open")!;
  snapshot.decisions = [
    {
      ...template,
      id: "d-sample",
      ref: "D-9",
      status: "open",
      project_slug: "csv-export",
      asked_by_session_id: nova.session_id,
      asked_by_title: nova.title,
      question: "(sample) Should exports include archived rows?",
      context: null,
      options: [
        {
          id: "1",
          label: "(sample) Yes",
          description: null,
          recommended: true,
          action: null,
          href: null,
          target: null,
        },
        {
          id: "2",
          label: "(sample) No",
          description: null,
          recommended: false,
          action: null,
          href: null,
          target: null,
        },
      ],
      claimed_by: null,
      assignee: "operators",
      answer_text: null,
      delivered_to_title: null,
    },
  ];
  return snapshot;
})();

export const PanelWaitingOnYou = teams({
  snapshot: waitingSnapshot,
  query: "peek=agent%3Anova",
  routes: teamsRoutes(waitingSnapshot),
});

export const PanelWaitingOnYouDark = teams({
  snapshot: waitingSnapshot,
  query: "peek=agent%3Anova",
  routes: teamsRoutes(waitingSnapshot),
  theme: "dark",
});

export const PanelWaitingOnYouMobile = phone(
  teams({
    snapshot: waitingSnapshot,
    query: "peek=agent%3Anova",
    routes: teamsRoutes(waitingSnapshot),
  }),
);

/** Stopped: its relaunch failed; it needs a person. */
export const PanelDead = peek("mira");

export const PanelDeadDark = peek("mira", { theme: "dark" });

export const PanelDeadMobile = phone(peek("mira"));

/** A lead seat: what it owns, its model and check-ins, instead of a track record. */
export const PanelLeadSeat = peek("atlas");

export const PanelLeadSeatDark = peek("atlas", { theme: "dark" });

export const PanelLeadSeatMobile = phone(peek("atlas"));

/** A discovery seat, as a factory with discovery on would show it (forward-compatible role). */
const scoutSnapshot = (() => {
  const snapshot = teamsSnapshot();
  const sentinel = snapshot.members.find((m) => m.handle === "sentinel")!;
  snapshot.members = [
    ...snapshot.members,
    {
      ...sentinel,
      session_id: "5b000000000000000000000000000001",
      title: "Scout",
      role: "scout" as never,
      handle: "scout",
      summary: "(sample) Daily sweep: labelling 38 new signals",
    },
  ];
  return snapshot;
})();

export const PanelScoutSeat = teams({
  snapshot: scoutSnapshot,
  query: "peek=agent%3Ascout",
  routes: teamsRoutes(scoutSnapshot),
});

/** A peek link names an agent this factory does not have (or the viewer cannot see). */
export const PanelNotFound = peek("nobody");

export const PanelNotFoundDark = peek("nobody", { theme: "dark" });

export const PanelNotFoundMobile = phone(peek("nobody"));

/** The strip covers only part of the day: recording began 9 hours ago. */
export const PanelPartialStrip = teams({
  query: "peek=agent%3Adot",
  routes: teamsRoutes(teamsSnapshot(), { partialSinceH: 9 }),
});

/** Ops (viewer): levers hidden, one line says who can. */
export const PanelViewer = (() => {
  const snapshot = teamsSnapshot();
  const none = Object.fromEntries(Object.keys(snapshot.viewer!.can).map((k) => [k, false]));
  snapshot.viewer = { ...snapshot.viewer!, role: "viewer", can: none as never };
  return teams({ snapshot, query: "peek=agent%3Anova", routes: teamsRoutes(snapshot) });
})();

// ── The full page ──────────────────────────────────────────────

function fullPage(handle: string, options: CockpitStoryOptions = {}): Story {
  const snapshot = teamsSnapshot();
  return {
    render: () => (
      <CockpitStory
        snapshot={snapshot}
        path={`/agents/${handle}`}
        search="?cockpit=v2"
        routes={teamsRoutes(snapshot)}
        {...options}
      />
    ),
  };
}

/** `/agents/:handle`: the same panel at page width, under a breadcrumb. */
export const FullPage = fullPage("dot");

export const FullPageDark = fullPage("dot", { theme: "dark" });

export const FullPageMobile = phone(fullPage("dot"));

/** A handle that is not in this factory. */
export const FullPageNotFound = fullPage("nobody");
