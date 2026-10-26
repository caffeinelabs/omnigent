import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import { storySnapshot } from "../board/__fixtures__/boardFixtures";
import { BoardFrame, EVENTS_PATH } from "../board/boardStoryKit";
import type { FactoryFrameOptions } from "../factoryStoryFixtures";
import { ActivityList } from "./ActivityList";

function activity(
  options: {
    theme?: "light" | "dark";
    project?: string;
    routes?: FactoryFrameOptions["routes"];
  } = {},
): Story {
  return {
    render: () => {
      const snapshot = storySnapshot();
      return (
        <BoardFrame theme={options.theme} routes={options.routes}>
          <ActivityList
            factoryId={snapshot.factory.id}
            projects={snapshot.projects}
            members={snapshot.members}
            project={options.project}
            heightClassName="max-h-[32rem]"
          />
        </BoardFrame>
      );
    },
  };
}

const meta = {
  title: "Factory/Activity",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Newest first; message receipts fold into their message, heartbeats into one ♥ row. */
export const Default = activity();

export const Dark = activity({ theme: "dark" });

/** Pinned to one project (the project drawer). */
export const ProjectPinned = activity({ project: "db-version" });

/** Heartbeats switched on: each beat is its own row. */
export const HeartbeatsShown: Story = {
  ...activity(),
  play: async ({ canvasElement }) => {
    await userEvent.click(await within(canvasElement).findByTestId("activity-kind-heartbeats"));
  },
};

/** A row clicked open: its excerpt and data. */
export const RowExpanded: Story = {
  ...activity(),
  play: async ({ canvasElement }) => {
    const rows = await within(canvasElement).findAllByTestId("activity-row");
    const button = rows[0]?.querySelector("button");
    if (button) await userEvent.click(button);
  },
};

export const Empty = activity({
  routes: { [EVENTS_PATH]: { body: { data: [], has_more: false } } },
});

export const Loading = activity({ routes: { [EVENTS_PATH]: "pending" } });

export const LoadError = activity({ routes: { [EVENTS_PATH]: { error: 503 } } });
