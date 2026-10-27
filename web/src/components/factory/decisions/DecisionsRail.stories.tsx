import type { Meta, StoryObj } from "@storybook/react-vite";
import type { DecisionView } from "@/lib/factoryApi";
import { LIVE_RUN_FACTORY_ID, liveRunSnapshot } from "../__fixtures__/liveRun";
import { DocumentTheme } from "../board/boardStoryKit";
import { FactoryStoryFrame } from "../factoryStoryFixtures";
import { answeredAndDelivered, lateQuestion } from "./__fixtures__/decisionFixtures";
import { DecisionsRail } from "./DecisionsRail";

const meta = {
  title: "Factory/Decisions/Rail",
  component: DecisionsRail,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof DecisionsRail>;

export default meta;
type Story = StoryObj<typeof meta>;

function rail(
  decisions: DecisionView[],
  options: { theme?: "light" | "dark"; highlightId?: string } = {},
): Story {
  const args = {
    factoryId: LIVE_RUN_FACTORY_ID,
    decisions,
    highlightId: options.highlightId ?? null,
  };
  return {
    args,
    render: () => (
      <FactoryStoryFrame mode="viewport" theme={options.theme}>
        <DocumentTheme theme={options.theme ?? "light"} />
        <div className="overflow-y-auto p-6">
          <div className="w-72">
            <DecisionsRail {...args} />
          </div>
        </div>
      </FactoryStoryFrame>
    ),
  };
}

/** Nothing open: the rail says so. */
export const Empty = rail([]);

export const EmptyDark = rail([], { theme: "dark" });

/** The live run: D-2 (blocking question) and D-3 (relaunch failed) open, D-1 answered. */
export const Populated = rail(liveRunSnapshot().decisions);

export const PopulatedDark = rail(liveRunSnapshot().decisions, { theme: "dark" });

/** A third, non-blocking question that has waited 40 min; D-2 delivered moments ago. */
export const Busy = rail([
  ...liveRunSnapshot().decisions.filter((d) => d.ref !== "D-2"),
  answeredAndDelivered(),
  lateQuestion(),
]);

/** `?decision=D-3` highlights and scrolls to it. */
export const Highlighted = rail(liveRunSnapshot().decisions, { highlightId: "D-3" });
