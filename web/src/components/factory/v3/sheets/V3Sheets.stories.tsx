import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect, useState } from "react";
import { userEvent, within } from "storybook/test";
import { FactoryLayout } from "@/pages/factory/FactoryLayout";
import { CockpitStory, type CockpitStoryOptions } from "../../board/boardStoryKit";
import { COCKPIT_STORAGE_KEY, resetCockpitChoiceCache } from "../../shell/cockpitV2";
import { SAMPLE_ROOM_NOW_S, sampleRoomRoutes, sampleRoomSnapshot } from "../fixture/sampleRoom";
import { sampleNodeSource } from "../fixture/sampleNodeSource";
import { NodeSourceProvider } from "../nodeSource";

// Cockpit v3's sheets in the shell over the sample factory: Needs you (ranked
// by what each ask holds, the asks that block nothing grouped with "Apply
// all", the alarms the server closed with their causes, the other tree's
// count), Find (⌘K finds nodes by ref or title with their breadcrumbs), the
// keys, the base env, and Try it (the passing steps by name, the flag, the
// sign-in note). Each opens from the address, as a link would.

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

const NOW = new Date(SAMPLE_ROOM_NOW_S * 1000);

function SampleCockpit(props: CockpitStoryOptions) {
  const [source] = useState(() => sampleNodeSource());
  return (
    <NodeSourceProvider source={source}>
      <ForgetChoice />
      <CockpitStory {...props} />
    </NodeSourceProvider>
  );
}

function sheet(
  nodeRef: string,
  query: string,
  options: CockpitStoryOptions & { play?: Story["play"] } = {},
): Story {
  const { play, ...rest } = options;
  const snapshot = sampleRoomSnapshot();
  return {
    render: () => (
      <SampleCockpit
        snapshot={snapshot}
        path={`/n/${encodeURIComponent(nodeRef)}`}
        search={`?cockpit=v3${query}`}
        now={NOW}
        routes={sampleRoomRoutes({ snapshot })}
        {...rest}
      />
    ),
    ...(play ? { play } : {}),
  };
}

const phone = (story: Story): Story => ({ ...story, globals: { viewport: { value: "mobile2" } } });

/** ⌘K under v3: the search button, then typed words. */
function find(words: string): Story["play"] {
  return async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(await body.findByTestId("cockpit-search", {}, { timeout: 5000 }));
    const input = await body.findByTestId("factory-palette-input");
    await userEvent.type(input, words, { delay: 5 });
    await body.findByTestId("factory-palette-group-nodes", {}, { timeout: 3000 });
  };
}

const meta = {
  title: "Factory/Cockpit v3/Sheets",
  component: FactoryLayout,
  parameters: { layout: "fullscreen" },
  args: { children: null },
} satisfies Meta<typeof FactoryLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Needs you, from Home: D-45, the merges on main, D-49; C-14 and C-15 grouped; three alarms closed. */
export const NeedsYou = sheet("product", "&peek=needs");
export const NeedsYouDark = sheet("product", "&peek=needs", { theme: "dark" });
export const NeedsYouPhone = phone(sheet("product", "&peek=needs"));

/** The factory tree's asks: the two rules Sentinel proposes. */
export const NeedsYouFactory = sheet("fx", "&peek=needs&tree=factory");

/** ⌘K finds a node by its ref, with its level and breadcrumb. */
export const FindByRef = sheet("product", "", { play: find("T-16") });
export const FindByRefDark = sheet("product", "", { theme: "dark", play: find("T-16") });
/** …and by words of its title. */
export const FindByTitle = sheet("product", "", { play: find("panel") });
export const FindPhone = phone(sheet("product", "", { play: find("C-16") }));

/** `?`: the keys, with each verb's. */
export const Keys = sheet("assistant-core", "&peek=keys");
export const KeysDark = sheet("assistant-core", "&peek=keys", { theme: "dark" });

/** The base env: what it runs, its health, what it follows, how to sign in. */
export const BaseEnv = sheet("product", "&peek=env");
export const BaseEnvDark = sheet("product", "&peek=env", { theme: "dark" });
export const BaseEnvPhone = phone(sheet("product", "&peek=env"));

/** Try it on the feature: the steps that pass, by name, through the API while the panel is not built. */
export const TryIt = sheet("I-1", "&peek=try&at=I-1");
export const TryItDark = sheet("I-1", "&peek=try&at=I-1", { theme: "dark" });
export const TryItPhone = phone(sheet("I-1", "&peek=try&at=I-1"));
