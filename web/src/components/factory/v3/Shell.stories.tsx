import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect } from "react";
import { FactoryLayout } from "@/pages/factory/FactoryLayout";
import { preflightOk } from "../__fixtures__/liveRun";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import { COCKPIT_STORAGE_KEY, resetCockpitChoiceCache } from "../shell/cockpitV2";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";

// Cockpit v3 behind the cockpit choice: `?cockpit=v3` puts the node pages in
// the new cockpit's shell (Home is the product's node page). Without it the
// person keeps v2. This server has no node API, so v3 shows the sample
// factory and says so.

/** The v3 choice lives in this browser: drop it when the story closes, so other stories keep v2. */
function ForgetChoice() {
  useEffect(
    () => () => {
      try {
        window.localStorage.removeItem(COCKPIT_STORAGE_KEY);
      } catch {
        // Storage may be unavailable.
      }
      resetCockpitChoiceCache();
    },
    [],
  );
  return null;
}

function shell(options: CockpitStoryOptions & { v3?: boolean }): Story {
  const { v3 = true, search = "", ...rest } = options;
  const snapshot = round3Snapshot();
  const own = `/v1/factory/factories/${snapshot.factory.id}`;
  return {
    render: () => (
      <>
        <ForgetChoice />
        <CockpitStory
          snapshot={snapshot}
          search={v3 ? `?cockpit=v3${search ? `&${search.slice(1)}` : ""}` : search}
          routes={{
            "/v1/factory/preflight": { body: { ...preflightOk(), cockpit_v2: true } },
            [`${own}/notify/me/ui`]: { body: { cockpit: "v2" } },
          }}
          {...rest}
        />
      </>
    ),
  };
}

const meta = {
  title: "Factory/Cockpit v3/Shell",
  component: FactoryLayout,
  parameters: { layout: "fullscreen" },
  args: { children: null },
} satisfies Meta<typeof FactoryLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

/** v3 on: Home is the product's node page in the new cockpit's shell. */
export const V3On = shell({});
export const V3OnDark = shell({ theme: "dark" });
export const V3OnPhone: Story = { ...shell({}), globals: { viewport: { value: "mobile2" } } };

/** A v3 link to a node: the assistant feature. */
export const V3NodeLink = shell({ path: "/n/I-1" });

/** One level under the product: every project, grouped under its feature. */
export const V3LevelLink = shell({ path: "/n/product/project", theme: "dark" });

/** v3 off: the person keeps the v2 cockpit (the default for those who chose the new cockpit). */
export const V3Off = shell({ v3: false });
