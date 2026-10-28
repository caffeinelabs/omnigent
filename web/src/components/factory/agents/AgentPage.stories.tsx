import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import { AgentPage } from "@/pages/factory/AgentPage";
import { CockpitStory, type CockpitStoryOptions, SNAPSHOT_PATH } from "../board/boardStoryKit";
import { ROUND3_NOW_S, round3Events, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { ENV_DB, IC_ROUTE_R2, PL_CACHE, PL_DB, agentDetail } from "./__fixtures__/opsFixtures";

function agent(sid: string, options: CockpitStoryOptions = {}, play?: Story["play"]): Story {
  return {
    render: () => (
      <CockpitStory
        snapshot={round3Snapshot()}
        events={round3Events()}
        path={`/agents/${sid}`}
        {...options}
      />
    ),
    play,
  };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

function tab(name: string): NonNullable<CockpitStoryOptions["search"]> {
  return `?tab=${name}`;
}

const meta = {
  title: "Factory/Agent page",
  component: AgentPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof AgentPage>;

export default meta;
type Story = StoryObj<typeof meta>;

// ── Live ───────────────────────────────────────────────────────

/** ic:route-r2 repeating its failing test: NOW, INC-13 with its runbook, Live, the composer. */
export const Live = agent(IC_ROUTE_R2);

export const LiveDark = agent(IC_ROUTE_R2, { theme: "dark" });

export const LiveMobile = phone(agent(IC_ROUTE_R2));

export const LiveMobileDark = phone(agent(IC_ROUTE_R2, { theme: "dark" }));

/** Mia (operator) watching with Luka: presence faces, every lever open. */
export const MultiUser = agent(IC_ROUTE_R2, {
  snapshot: (() => {
    const snapshot = round3Snapshot("operator");
    snapshot.presence = [
      ...(snapshot.presence ?? []).filter((p) => p.user_id !== "luka@caffeine.ai"),
      {
        user_id: "luka@caffeine.ai",
        name: "Luka",
        location: `agent:${IC_ROUTE_R2}`,
        idle: false,
        since: ROUND3_NOW_S - 60,
        sessions: [],
      },
      {
        user_id: "ops@caffeine.ai",
        name: "Ops",
        location: `agent:${IC_ROUTE_R2}`,
        idle: true,
        since: ROUND3_NOW_S - 600,
        sessions: [],
      },
    ];
    return snapshot;
  })(),
});

/** Ops (viewer): the levers, runbook and composer disabled with who can. */
export const Viewer = agent(IC_ROUTE_R2, { snapshot: round3Snapshot("viewer") });

export const ViewerMobile = phone(agent(IC_ROUTE_R2, { snapshot: round3Snapshot("viewer") }));

/** Redispatch from the header: the confirmation for T-4. */
export const RedispatchConfirm = agent(IC_ROUTE_R2, {}, async ({ canvasElement }) => {
  const screen = within(canvasElement.ownerDocument.body);
  await userEvent.click(await screen.findByTestId("agent-redispatch"));
  await screen.findByTestId("intervention-dialog");
});

// ── Tabs ───────────────────────────────────────────────────────

export const ToolCalls = agent(IC_ROUTE_R2, { search: tab("tools") });

export const ToolCallsDark = agent(IC_ROUTE_R2, { search: tab("tools"), theme: "dark" });

/** Opened from a Timeline bar: the moment is marked. */
export const AtMoment = agent(IC_ROUTE_R2, {
  search: `?tab=tools&at=${(ROUND3_NOW_S - 7 * 60) * 1000}`,
});

export const Transcript = agent(IC_ROUTE_R2, { search: tab("transcript") });

export const TranscriptMobile = phone(agent(IC_ROUTE_R2, { search: tab("transcript") }));

/** No terminal on this agent (SDK harness). */
export const Terminal = agent(IC_ROUTE_R2, { search: tab("terminal") });

/** The diff of the session's PR (here the runner reports no git workspace). */
export const Diff = agent(IC_ROUTE_R2, { search: tab("diff") });

export const Mail = agent(IC_ROUTE_R2, { search: tab("mail") });

export const MailMobile = phone(agent(IC_ROUTE_R2, { search: tab("mail") }));

/** T-4 and its implement loop: r0..r2 failed, exhausted, D-31's remedies. */
export const TaskAndLoop = agent(IC_ROUTE_R2, { search: tab("task") });

export const TaskAndLoopDark = agent(IC_ROUTE_R2, { search: tab("task"), theme: "dark" });

export const TaskAndLoopMobile = phone(agent(IC_ROUTE_R2, { search: tab("task") }));

export const Cost = agent(IC_ROUTE_R2, { search: tab("cost") });

export const CostDark = agent(IC_ROUTE_R2, { search: tab("cost"), theme: "dark" });

// ── Other agents ───────────────────────────────────────────────

/** The db-version PL, waiting on D-2: no task, its wait in the summary. */
export const ProjectLead = agent(PL_DB);

/** The dead cache-warm PL: INC-12 with Restart, Relaunch, Pause project. */
export const DeadLead = agent(PL_CACHE);

export const DeadLeadDark = agent(PL_CACHE, { theme: "dark" });

/** The db-version env keeper, running `tilt ci`. */
export const EnvKeeper = agent(ENV_DB);

/** Long summary and tool arguments wrap or truncate in place. */
export const LongText = agent(IC_ROUTE_R2, {
  routes: {
    [`${SNAPSHOT_PATH}/agents/${IC_ROUTE_R2}`]: {
      body: (() => {
        const detail = agentDetail(IC_ROUTE_R2)!;
        detail.agent.title = "ic:route-r2-with-a-very-long-worker-name-from-a-long-task-key";
        if (detail.agent.now) {
          detail.agent.now.tool_arg =
            "pnpm -F caffeine-api test -- --reporter=verbose --runInBand src/modules/db/version.test.ts src/modules/db/migrations.test.ts";
        }
        return detail;
      })(),
    },
  },
});

export const LongTextMobile = phone(LongText);

// ── Loading, errors, missing ───────────────────────────────────

export const Loading = agent(IC_ROUTE_R2, {
  routes: { [`${SNAPSHOT_PATH}/agents/${IC_ROUTE_R2}`]: "pending" },
});

/** The agent read failed: the snapshot's view with Retry. */
export const LoadError = agent(IC_ROUTE_R2, {
  routes: { [`${SNAPSHOT_PATH}/agents/${IC_ROUTE_R2}`]: { error: 503, message: "unavailable" } },
});

/** A session that isn't in this factory. */
export const Missing = agent("00000000000000000000000000000000", {
  routes: {
    [`${SNAPSHOT_PATH}/agents/00000000000000000000000000000000`]: {
      error: 404,
      message: "agent not found",
    },
  },
});
