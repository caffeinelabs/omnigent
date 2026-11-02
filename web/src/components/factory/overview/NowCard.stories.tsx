import type { Meta, StoryObj } from "@storybook/react-vite";
import type { StatusSummary } from "@/lib/factoryStatusApi";
import { LIVE_RUN_FACTORY_ID } from "../__fixtures__/liveRun";
import { BoardFrame } from "../board/boardStoryKit";
import type { StoryResponse } from "../factoryStoryFixtures";
import { ROUND3_NOW_S } from "../shell/__fixtures__/shellFixtures";
import {
  busyNow,
  idleNow,
  pausedNow,
  startingNow,
  waitingOnYouNow,
} from "./__fixtures__/nowScenarios";
import { NowCard } from "./NowCard";

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

export const Loading = nowStory("pending");

/** The read failed: one line with Retry; the panels below still render. */
export const LoadError = nowStory({ error: 503 });

export const LoadErrorDark = nowStory({ error: 503 }, "dark");
