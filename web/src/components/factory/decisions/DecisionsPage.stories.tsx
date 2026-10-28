import type { Meta, StoryObj } from "@storybook/react-vite";
import { fireEvent, within } from "storybook/test";
import type { DecisionView, OpenDecisionItem, ViewerRole } from "@/lib/factoryApi";
import { FactoryLayout } from "@/pages/factory/FactoryLayout";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import { ROOM_FID, ROOM_NOW_S, roomRoutes } from "../room/__fixtures__/roomFixtures";
import { round3Events, round3Snapshot } from "../shell/__fixtures__/shellFixtures";

const NOW = new Date(ROOM_NOW_S * 1000);

/** A week of answered and expired decisions of the round-3 factory. */
function history(): OpenDecisionItem[] {
  const factory = { id: ROOM_FID, name: "Atlas factory" };
  const base = round3Snapshot().decisions.find((d) => d.ref === "D-1")!;
  const answered = (
    n: number,
    question: string,
    option: number,
    by: string,
    ago: number,
  ): DecisionView => ({
    ...base,
    id: `hist${String(n).padStart(28, "0")}`,
    ref: `D-${n}`,
    number: n,
    question,
    status: "answered",
    answer_option_id: base.options[option]?.id ?? null,
    answered_by: by,
    answered_at: ROOM_NOW_S - ago * 60,
    delivered_at: ROOM_NOW_S - ago * 60 + 4,
    updated_at: ROOM_NOW_S - ago * 60,
  });
  return [
    {
      factory,
      decision: answered(
        30,
        "Use knex_migrations as the source of truth?",
        0,
        "mia@caffeine.ai",
        12,
      ),
    },
    {
      factory,
      decision: answered(
        29,
        "Ship the Settings row before the DB endpoint?",
        1,
        "luka@caffeine.ai",
        95,
      ),
    },
    {
      factory,
      decision: answered(
        28,
        `Rename the version header ${"to something much longer ".repeat(4)}?`,
        0,
        "luka@caffeine.ai",
        300,
      ),
    },
    {
      factory,
      decision: {
        ...answered(27, "Re-run the preview on the old head?", 0, "", 600),
        status: "expired",
        answered_by: null,
        answer_option_id: null,
      },
    },
  ];
}

function page(
  options: CockpitStoryOptions & { role?: ViewerRole } = {},
  play?: Story["play"],
): Story {
  const { role = "creator", routes, ...rest } = options;
  return {
    render: () => {
      try {
        window.localStorage.removeItem(`omnigent.factory.room.lastThread.${ROOM_FID}`);
      } catch {
        // Storage off: the dock starts on #factory anyway.
      }
      return (
        <CockpitStory
          snapshot={options.snapshot ?? round3Snapshot(role)}
          events={round3Events()}
          path="/decisions"
          now={NOW}
          routes={{
            ...roomRoutes(ROOM_FID, { role }),
            "/v1/factory/decisions": { body: { data: history(), count: history().length } },
            ...routes,
          }}
          {...rest}
        />
      );
    },
    play,
  };
}

const meta = {
  title: "Factory/Decisions page",
  component: FactoryLayout,
  parameters: { layout: "fullscreen" },
  args: { children: null },
} satisfies Meta<typeof FactoryLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Luka (creator): owners' items are his role's; D-2 is Mia's. Proposals render as their cards. */
export const Open = page();

export const OpenDark = page({ theme: "dark" });

export const OpenMobile: Story = { ...page(), globals: { viewport: { value: "mobile2" } } };

/** Mia (operator): D-2 assigned to her first, then operators', then the owners'. */
export const AssignedToMe = page({ role: "operator" });

/** A viewer: every answer, Claim, Assign and Apply disabled. */
export const Viewer = page({ role: "viewer" });

/** Nothing open. */
export const Empty = page({
  snapshot: (() => {
    const snapshot = round3Snapshot();
    snapshot.decisions = snapshot.decisions.filter((d) => d.status !== "open");
    return snapshot;
  })(),
});

/** The history: answered, who answered and the delivery receipt. */
export const Answered = page({}, async ({ canvasElement }) => {
  fireEvent.mouseDown(
    await within(canvasElement.ownerDocument.body).findByTestId(
      "decisions-view-answered",
      {},
      { timeout: 6000 },
    ),
  );
  await within(canvasElement.ownerDocument.body).findByTestId(
    "decision-history-D-30",
    {},
    { timeout: 6000 },
  );
});

export const AnsweredDark = page({ theme: "dark" }, Answered.play);

export const AnsweredMobile: Story = {
  ...page({}, Answered.play),
  globals: { viewport: { value: "mobile2" } },
};

/** The history while it loads. */
export const AnsweredLoading = page(
  { routes: { "/v1/factory/decisions": "pending" } },
  async ({ canvasElement }) => {
    fireEvent.mouseDown(
      await within(canvasElement.ownerDocument.body).findByTestId(
        "decisions-view-answered",
        {},
        { timeout: 6000 },
      ),
    );
  },
);

/** The history couldn't be read. */
export const AnsweredError = page(
  { routes: { "/v1/factory/decisions": { error: 503 } } },
  async ({ canvasElement }) => {
    fireEvent.mouseDown(
      await within(canvasElement.ownerDocument.body).findByTestId(
        "decisions-view-answered",
        {},
        { timeout: 6000 },
      ),
    );
  },
);
