import type { Meta, StoryObj } from "@storybook/react-vite";
import type { LoopView } from "@/lib/factoryApi";
import {
  PR_9631,
  round3IssueList,
  round3LoopList,
  timelineSpans,
} from "../agents/__fixtures__/opsFixtures";
import {
  BoardFrame,
  CockpitStory,
  type CockpitStoryOptions,
  SNAPSHOT_PATH,
} from "../board/boardStoryKit";
import { round3Events, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { FactoryViewerProvider } from "../shell/viewerCan";
import { LoopStrip } from "./LoopStrip";
import { loopLines } from "./loopStripModel";

const LOOPS_PATH = `${SNAPSHOT_PATH}/loops`;

function loop(kind: LoopView["kind"], patch: Partial<LoopView> = {}): LoopView {
  return { ...round3LoopList().find((l) => l.kind === kind)!, ...patch };
}

/** T-5's IC is dispatched; its lead waits on two answers from a person (D-22, D-23). */
function heldLoop(): LoopView {
  return loop("implement", {
    id: "impl-held",
    subject: "task:held",
    task_id: "0f5e4d3c2b1a49788776655443322110",
    task_ref: "T-5",
    pr_id: null,
    pr_number: null,
    state: "running",
    iteration: 1,
    spent: { attempts: 1, usd: 2.4, wall_s: 5400, per_head: {}, env_retries: 0 },
    last_verdict: "DISPATCHED",
    last_reason: "attempt 1 (dispatch)",
    decision_id: null,
    decision_ref: null,
    held_by: { decision_id: null, decision_ref: "D-22", refs: ["D-22", "D-23"], since: null },
  });
}

/** T-6's PR is ready: it waits on a person's merge, not an answer (no Decisions link). */
function mergeHeldLoop(): LoopView {
  const label = "waiting on a person to merge PR #9640";
  return {
    ...heldLoop(),
    id: "impl-merge",
    subject: "task:merge",
    task_ref: "T-6",
    held_by: {
      decision_id: null,
      decision_ref: label,
      refs: [],
      since: null,
      count: 0,
      kind: "merge",
      label,
      who: "an operator",
    },
  };
}

/** One loop in each strip state. */
function stateLoops(): LoopView[] {
  return [
    loop("implement"),
    heldLoop(),
    mergeHeldLoop(),
    loop("review", {
      id: "rv-thrash",
      state: "thrashing",
      iteration: 3,
      last_verdict: "CHANGES",
      last_reason: "3 blocking · finding 3fa2c1d0 came back in round 3",
    }),
    loop("review", {
      id: "rv-dry",
      subject: "pr:other",
      state: "stalled",
      last_verdict: "CHANGES",
      last_reason: "review dry: round 2 adds no new blocking finding and fixes none",
    }),
    loop("verify", { state: "running" }),
    // A demo that passed at its first run (no spans: the strip reads its count and verdict).
    loop("demo", {
      id: "demo-pass",
      state: "converged",
      iteration: 1,
      last_verdict: "PASS",
      last_reason: "PASS at 5e1c2d4",
    }),
  ];
}

function Strip({ role = "creator" }: { role?: "creator" | "viewer" }) {
  const snapshot = round3Snapshot(role);
  return (
    <FactoryViewerProvider viewer={snapshot.viewer}>
      <div className="rounded-xl border border-border bg-card p-4">
        <LoopStrip
          snapshot={snapshot}
          lines={loopLines(stateLoops(), timelineSpans(), round3IssueList())}
        />
      </div>
    </FactoryViewerProvider>
  );
}

function pageStory(path: string, options: CockpitStoryOptions = {}): Story {
  return {
    render: () => (
      <CockpitStory snapshot={round3Snapshot()} events={round3Events()} path={path} {...options} />
    ),
  };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

const meta = {
  title: "Factory/Loops",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

// ── The strip's states ─────────────────────────────────────────

/**
 * Exhausted (r0..r2 ✗, $11.80/$15, D-31's remedies), waiting on a person (T-5:
 * `waiting on D-22 +1`, not counted as stalled), waiting on a merge (T-6, the
 * server's words, unlinked), thrashing (`#3 ✗ CHANGES · 3 blocking`), dry,
 * running, converged.
 */
export const States: Story = {
  render: () => (
    <BoardFrame width={1100}>
      <Strip />
    </BoardFrame>
  ),
};

export const StatesDark: Story = {
  render: () => (
    <BoardFrame width={1100} theme="dark">
      <Strip />
    </BoardFrame>
  ),
};

export const StatesMobile: Story = phone({
  render: () => (
    <BoardFrame width={390}>
      <Strip />
    </BoardFrame>
  ),
});

/** A viewer: the remedies disabled with who can. */
export const StatesViewer: Story = {
  render: () => (
    <BoardFrame width={1100}>
      <Strip role="viewer" />
    </BoardFrame>
  ),
};

// ── On the PR page ─────────────────────────────────────────────

/** !9631: implement exhausted, review converged 2/3, verify and demo 1/2 this head, CI flake F-7. */
export const PrPage = pageStory(`/prs/${PR_9631}`);

export const PrPageDark = pageStory(`/prs/${PR_9631}`, { theme: "dark" });

export const PrPageMobile = phone(pageStory(`/prs/${PR_9631}`));

export const PrPageLoading = pageStory(`/prs/${PR_9631}`, { routes: { [LOOPS_PATH]: "pending" } });

export const PrPageError = pageStory(`/prs/${PR_9631}`, {
  routes: { [LOOPS_PATH]: { error: 503, message: "unavailable" } },
});

/** No loops yet: the empty line. */
export const PrPageEmpty = pageStory(`/prs/${PR_9631}`, {
  routes: { [LOOPS_PATH]: { body: { data: [], engine_mode: "observe" } } },
});

// ── The project's Loops tab ────────────────────────────────────

/** db-version's Loops tab: a strip per PR, then the task and plan loops. */
export const ProjectTab = pageStory("/projects/db-version", { search: "#loops" });

export const ProjectTabDark = pageStory("/projects/db-version", {
  search: "#loops",
  theme: "dark",
});

export const ProjectTabMobile = phone(pageStory("/projects/db-version", { search: "#loops" }));

export const ProjectTabEmpty = pageStory("/projects/docs-factory-envs", {
  search: "#loops",
  routes: { [LOOPS_PATH]: { body: { data: [], engine_mode: "assist" } } },
});

/** db-version's Loops tab while T-5 waits on a person's answers: no stall, no remedies. */
const waitingRoutes = {
  [LOOPS_PATH]: {
    body: {
      data: [heldLoop(), ...round3LoopList().filter((l) => l.project_slug === "db-version")],
      engine_mode: "assist",
    },
  },
};

export const ProjectTabWaiting = pageStory("/projects/db-version", {
  search: "#loops",
  routes: waitingRoutes,
});

export const ProjectTabWaitingDark = pageStory("/projects/db-version", {
  search: "#loops",
  routes: waitingRoutes,
  theme: "dark",
});

export const ProjectTabWaitingMobile = phone(
  pageStory("/projects/db-version", { search: "#loops", routes: waitingRoutes }),
);
