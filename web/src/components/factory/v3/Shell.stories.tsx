import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect } from "react";
import { FactoryLayout } from "@/pages/factory/FactoryLayout";
import { preflightOk } from "../__fixtures__/liveRun";
import serverLenses from "../__fixtures__/nodeLenses.server.json";
import serverLevels from "../__fixtures__/nodeLevels.server.json";
import serverPages from "../__fixtures__/nodePages.server.json";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import { COCKPIT_STORAGE_KEY, resetCockpitChoiceCache } from "../shell/cockpitV2";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";

// Cockpit v3 behind the cockpit choice: `?cockpit=v3` puts the node pages in
// the new cockpit's shell (Home is the product's node page). Without it the
// person keeps v2. This server has no node API unless a story serves the
// server's own node pages (`nodes`).

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

/** The server's own node pages, level views, lenses and levels (its contract fixtures). */
function serverNodeRoutes(own: string): Record<string, { body: unknown }> {
  const routes: Record<string, { body: unknown }> = {};
  const node = (ref: string) => `${own}/n/${encodeURIComponent(ref)}`;
  for (const [ref, body] of Object.entries(serverPages)) routes[node(ref)] = { body };
  for (const [key, body] of Object.entries(serverLevels)) {
    const [ref, level] = key.split("/") as [string, string];
    routes[`${node(ref)}/level/${level}`] = { body };
  }
  for (const [key, body] of Object.entries(serverLenses)) {
    const [lens, ref] = key.split("/") as [string, string | undefined];
    if (lens === "levels") routes[`${own}/levels`] = { body };
    else if (ref && ["lineage", "timeline", "money"].includes(lens)) {
      routes[`${node(ref)}/${lens}`] = { body };
    }
  }
  return routes;
}

function shell(options: CockpitStoryOptions & { v3?: boolean; nodes?: boolean }): Story {
  const { v3 = true, search = "", nodes = false, ...rest } = options;
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
            ...(nodes ? serverNodeRoutes(own) : {}),
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

/**
 * The factory tree on the server's own pages: Product | Factory in the top
 * bar, Factory on with its asks counted.
 */
export const V3FactoryTree = shell({ path: "/n/fx", nodes: true });
export const V3FactoryHealthDark = shell({ path: "/n/fx:health", theme: "dark", nodes: true });
/** The server's timeline of the assistant feature, in the shell. */
export const V3ServerTimeline = shell({ path: "/n/I-1", search: "?lens=timeline", nodes: true });

/** v3 off: the person keeps the v2 cockpit (the default for those who chose the new cockpit). */
export const V3Off = shell({ v3: false });
