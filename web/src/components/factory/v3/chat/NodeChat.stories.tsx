import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect } from "react";
import { FactoryLayout } from "@/pages/factory/FactoryLayout";
import { CockpitStory, type CockpitStoryOptions } from "../../board/boardStoryKit";
import { COCKPIT_STORAGE_KEY, resetCockpitChoiceCache } from "../../shell/cockpitV2";
import { SAMPLE_ROOM_NOW_S, sampleRoomRoutes, sampleRoomSnapshot } from "../fixture/sampleRoom";

// Cockpit v3's node chat: a main column beside every node page (the Room
// thread that covers the node, named in each message until per-node threads
// land), children with news as one line each, the node's cards, quick verbs,
// the node's verbs after `/` and plain words that open previews. On a phone
// the box is pinned at the bottom and opens the thread full screen. The node
// pages and the Room are the sample factory's, served as the real API.

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

function chat(nodeRef: string, options: CockpitStoryOptions & { open?: boolean } = {}): Story {
  const { open = false, ...rest } = options;
  const snapshot = sampleRoomSnapshot();
  return {
    render: () => (
      <>
        <ForgetChoice />
        <CockpitStory
          snapshot={snapshot}
          path={`/n/${encodeURIComponent(nodeRef)}`}
          search={`?cockpit=v3${open ? "&chat=1" : ""}`}
          now={NOW}
          routes={sampleRoomRoutes({ snapshot })}
          {...rest}
        />
      </>
    ),
  };
}

const phone = (story: Story): Story => ({ ...story, globals: { viewport: { value: "mobile2" } } });

const meta = {
  title: "Factory/Cockpit v3/Node chat",
  component: FactoryLayout,
  parameters: { layout: "fullscreen" },
  args: { children: null },
} satisfies Meta<typeof FactoryLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Home: #factory beside the product, with the projects that have news as one line each. */
export const RootThread = chat("product");
export const RootThreadDark = chat("product", { theme: "dark" });

/** assistant-core: its own thread with the demo, the alarm the server closed, and D-45 (C-16). */
export const AssistantCoreC16 = chat("assistant-core");
export const AssistantCoreC16Dark = chat("assistant-core", { theme: "dark" });

/** assistant-panel-ux: Atlas offers to start it now, and "why" posted the numbered card. */
export const PanelOfferAndWhy = chat("assistant-panel-ux");
export const PanelOfferAndWhyDark = chat("assistant-panel-ux", { theme: "dark" });

/** A feature chats in #factory, the feature named in the box ("To Atlas · about I-1 · …"). */
export const FeatureInFactory = chat("I-1");

/** A run has no thread: the page shows its task's (T-16 in #assistant-core, relayed to the builder). */
export const RunShowsTaskThread = chat("run:luz-t16");
export const RunShowsTaskThreadDark = chat("run:luz-t16", { theme: "dark" });

/** A phone: the box is pinned under the page with the node's ask above it. */
export const PhonePinned = phone(chat("assistant-core"));
export const PhonePinnedHome = phone(chat("product"));

/** A phone: the thread full screen, the status line's answer first among the quick verbs. */
export const PhoneFullScreen = phone(chat("assistant-core", { open: true }));
export const PhoneFullScreenDark = phone(chat("assistant-core", { open: true, theme: "dark" }));
