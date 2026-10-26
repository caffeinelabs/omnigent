import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";
import { FACTORIES_QUERY_KEY, factoryQueryKey } from "@/hooks/useFactory";
import { FactoryPage } from "@/pages/factory/FactoryPage";
import { NewFactoryPage } from "@/pages/factory/NewFactoryPage";
import { LIVE_RUN_FACTORY_ID, liveRunSnapshot } from "./__fixtures__/liveRun";
import { FactoryStoryFrame, type FactoryFrameOptions } from "./factoryStoryFixtures";

const SNAPSHOT_PATH = `/v1/factory/factories/${LIVE_RUN_FACTORY_ID}`;

function cockpitStory(frame: FactoryFrameOptions = {}): Story {
  return {
    render: () => (
      <FactoryStoryFrame mode="viewport" route={`/factory/${LIVE_RUN_FACTORY_ID}`} {...frame}>
        <Routes>
          <Route path="/factory/:factoryId" element={<FactoryPage />} />
        </Routes>
      </FactoryStoryFrame>
    ),
  };
}

const meta = {
  title: "Factory/Cockpit shell",
  component: FactoryPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof FactoryPage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The cockpit header from the live snapshot (the board lands below it). */
export const Header = cockpitStory();

export const HeaderDark = cockpitStory({ theme: "dark" });

export const Loading = cockpitStory({ routes: { [SNAPSHOT_PATH]: "pending" } });

/** Unknown id, or a factory the viewer can't read. */
export const NotFound = cockpitStory({ routes: { [SNAPSHOT_PATH]: { error: 404 } } });

/** A refresh failed: the header stays, the alert says how old it is. */
export const RefreshError = cockpitStory({
  routes: { [SNAPSHOT_PATH]: { error: 503 } },
  seed: (client) =>
    client.setQueryData(factoryQueryKey(LIVE_RUN_FACTORY_ID), liveRunSnapshot(), {
      updatedAt: new Date(2026, 9, 26, 14, 2).getTime(),
    }),
  afterMount: (client) => {
    void client.invalidateQueries({ queryKey: factoryQueryKey(LIVE_RUN_FACTORY_ID) });
    void client.invalidateQueries({ queryKey: FACTORIES_QUERY_KEY });
  },
});

/** The start form's placeholder route. */
export const NewFactory: Story = {
  render: () => (
    <FactoryStoryFrame mode="viewport" route="/factory/new">
      <NewFactoryPage />
    </FactoryStoryFrame>
  ),
};
