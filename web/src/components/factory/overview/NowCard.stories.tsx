import type { Meta, StoryObj } from "@storybook/react-vite";
import type { StatusSummary } from "@/lib/factoryStatusApi";
import { LIVE_RUN_FACTORY_ID } from "../__fixtures__/liveRun";
import { BoardFrame } from "../board/boardStoryKit";
import type { StoryResponse } from "../factoryStoryFixtures";
import { ROUND3_NOW_S } from "../shell/__fixtures__/shellFixtures";
import {
  busyNow,
  cantStartNow,
  idleNow,
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

// ── Busy ───────────────────────────────────────────────────────

/** Luka on Atlas's factory: 8 things wait on him, 4 projects in flight, one queued. */
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

/** cache-warm's lead never got a machine: Next and the headline say so, Health folds it. */
export const CantStart = nowStory(cantStartNow());

export const CantStartDark = nowStory(cantStartNow(), "dark");

export const CantStartPhone = phone(nowStory(cantStartNow()));

export const CantStartPhoneDark = phone(nowStory(cantStartNow(), "dark"));

/** validate-build-tier merged; its lead checks the preview between turns: still Working on. */
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
