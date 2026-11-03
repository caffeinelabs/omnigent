import type { Meta, StoryObj } from "@storybook/react-vite";
import type { StatusSummary } from "@/lib/factoryStatusApi";
import { LIVE_RUN_FACTORY_ID } from "../__fixtures__/liveRun";
import { BoardFrame } from "../board/boardStoryKit";
import type { StoryResponse } from "../factoryStoryFixtures";
import { ROUND3_NOW_S } from "../shell/__fixtures__/shellFixtures";
import { FactoryNowContext } from "../factoryClock";
import { FactoryViewerProvider } from "../shell/viewerCan";
import {
  busyNow,
  cantStartNow,
  idleNow,
  idleWaitingNow,
  launchingNow,
  liveNow,
  liveSnapshot,
  pausedNow,
  startingNow,
  validatingNow,
  waitingOnYouNow,
} from "./__fixtures__/nowScenarios";
import { NowCard, NowCardView } from "./NowCard";

const SUMMARY_PATH = `/v1/factory/factories/${LIVE_RUN_FACTORY_ID}/status-summary`;

function nowStory(
  response: StatusSummary | StoryResponse,
  theme: "light" | "dark" = "light",
): Story {
  const route: StoryResponse =
    typeof response === "object" && "factory_id" in response ? { body: response } : response;
  return {
    render: () => (
      <BoardFrame theme={theme} width={1100} routes={{ [SUMMARY_PATH]: route }}>
        <NowCard factoryId={LIVE_RUN_FACTORY_ID} nowS={ROUND3_NOW_S} />
      </BoardFrame>
    ),
  };
}

const ROUND3_NOW = new Date(ROUND3_NOW_S * 1000);

/**
 * The card with the Overview's live snapshot: the header's $/h, and decisions
 * that answer in place (their full card opens under the line).
 */
function withSnapshotStory(theme: "light" | "dark" = "light"): Story {
  return {
    render: () => {
      const snapshot = liveSnapshot();
      return (
        <BoardFrame theme={theme} width={1100}>
          <FactoryNowContext.Provider value={ROUND3_NOW}>
            <FactoryViewerProvider viewer={snapshot.viewer}>
              <NowCardView
                factoryId={LIVE_RUN_FACTORY_ID}
                summary={liveNow()}
                nowS={ROUND3_NOW_S}
                snapshot={snapshot}
              />
            </FactoryViewerProvider>
          </FactoryNowContext.Provider>
        </BoardFrame>
      );
    },
  };
}

/** A refresh failed: the busy card, dimmed, says it shows the read from 5 minutes ago. */
function staleStory(theme: "light" | "dark" = "light"): Story {
  return {
    render: () => (
      <BoardFrame theme={theme} width={1100}>
        <NowCardView
          factoryId={LIVE_RUN_FACTORY_ID}
          summary={busyNow()}
          nowS={ROUND3_NOW_S}
          stale={{ updatedAtS: ROUND3_NOW_S - 5 * 60, onRetry: () => undefined }}
        />
      </BoardFrame>
    ),
  };
}

/** The same story at 390 px (Storybook's `mobile2` viewport). */
function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

const meta = {
  title: "Factory/Overview/Now",
  component: NowCard,
  args: { factoryId: LIVE_RUN_FACTORY_ID, nowS: ROUND3_NOW_S },
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof NowCard>;

export default meta;
type Story = StoryObj<typeof meta>;

// ── The live factory, 2 Nov 11:45 UTC ─────────────────────────

/**
 * Four projects only wait on Lena to merge their PRs: each is listed once,
 * under Waiting on you, with Review → and the Slack DMs he got about it.
 * validate-build-tier is the one project in progress; Atlas's note links the
 * PRs it names; spend says the 24 h and the $/h.
 */
export const BusyWithPrsWaiting = withSnapshotStory();

export const BusyWithPrsWaitingDark = withSnapshotStory("dark");

export const BusyWithPrsWaitingPhone = phone(withSnapshotStory());

export const BusyWithPrsWaitingPhoneDark = phone(withSnapshotStory("dark"));

/** validate-build-tier done too: idle (still spending), and the four PRs wait on Lena. */
export const IdleWaitingOnYou = nowStory(idleWaitingNow());

export const IdleWaitingOnYouDark = nowStory(idleWaitingNow(), "dark");

export const IdleWaitingOnYouPhone = phone(nowStory(idleWaitingNow()));

// ── Busy ───────────────────────────────────────────────────────

/** Lena on Atlas's factory: 8 things wait on him, 2 projects in flight, one queued. */
export const Busy = nowStory(busyNow());

export const BusyDark = nowStory(busyNow(), "dark");

export const BusyPhone = phone(nowStory(busyNow()));

export const BusyPhoneDark = phone(nowStory(busyNow(), "dark"));

// ── Idle ───────────────────────────────────────────────────────

/** Nothing in flight since the docs project closed, yet the leads still spend $2.71/h. */
export const Idle = nowStory(idleNow());

export const IdleDark = nowStory(idleNow(), "dark");

export const IdlePhone = phone(nowStory(idleNow()));

export const IdlePhoneDark = phone(nowStory(idleNow(), "dark"));

// ── Waiting on you ─────────────────────────────────────────────

/** The live factory: Atlas's proposal (D-19) has waited 2d 18h; a PR waits for a merge. */
export const WaitingOnYou = nowStory(waitingOnYouNow());

export const WaitingOnYouDark = nowStory(waitingOnYouNow(), "dark");

export const WaitingOnYouPhone = phone(nowStory(waitingOnYouNow()));

export const WaitingOnYouPhoneDark = phone(nowStory(waitingOnYouNow(), "dark"));

// ── Paused, starting, loading, errors ──────────────────────────

/** Alice paused the factory with a note: the headline and Health say who and why. */
export const Paused = nowStory(pausedNow());

export const PausedDark = nowStory(pausedNow(), "dark");

/** Four projects queued minutes ago: each says which start-up step it is on. */
export const Starting = nowStory(startingNow());

export const StartingPhone = phone(nowStory(startingNow()));

// ── Can't start, validating, stale ─────────────────────────────

/**
 * cache-warm's lead never got a machine: Next and the headline say so, Health
 * folds it; audit-log is cloning, quota-alerts waits for a free machine.
 */
export const CantStart = nowStory(cantStartNow());

export const CantStartDark = nowStory(cantStartNow(), "dark");

export const CantStartPhone = phone(nowStory(cantStartNow()));

export const CantStartPhoneDark = phone(nowStory(cantStartNow(), "dark"));

/** validate-build-tier merged; its lead checks the preview between turns: still Working on. */
/** PL sandboxes coming up: waiting for a node, pulling the image, a retry, a failure. */
export const Launching = nowStory(launchingNow());

export const LaunchingDark = nowStory(launchingNow(), "dark");

export const LaunchingPhone = phone(nowStory(launchingNow()));

export const Validating = nowStory(validatingNow());

export const ValidatingDark = nowStory(validatingNow(), "dark");

export const ValidatingPhone = phone(nowStory(validatingNow()));

/** The last refresh failed: the last good read stays, dimmed, with its time and Retry. */
export const Stale = staleStory();

export const StaleDark = staleStory("dark");

export const StalePhone = phone(staleStory());

export const Loading = nowStory("pending");

/** The read failed: one line with Retry; the panels below still render. */
export const LoadError = nowStory({ error: 503 });

export const LoadErrorDark = nowStory({ error: 503 }, "dark");
