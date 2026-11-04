import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import { type V3StoryOptions, V3PageStory } from "../fixture/v3StoryKit";

// Proof is never hidden (calm rule 10), over the sample factory: a project, a
// change and a feature open with "Latest demo · PASS · 0:14 ▶" and the poster;
// the feature's acceptance run is PARTIAL and says what failed; Home links
// each feature's newest demo from its row ("▶"); "n more demos" opens the
// Demos list for the subtree; a click plays a demo in place.

const meta = {
  title: "Factory/Cockpit v3/Proof",
  component: V3PageStory,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof V3PageStory>;

export default meta;
type Story = StoryObj<typeof meta>;

const page = (args: V3StoryOptions): Story => ({ args });
const phone = (args: V3StoryOptions): Story => ({
  args,
  globals: { viewport: { value: "mobile2" } },
});

/** A project: its newest demo across its PRs (#9876 PASS 0:14), and "1 more demo". */
export const Project = page({ nodeRef: "assistant-core" });
export const ProjectDark = page({ nodeRef: "assistant-core", theme: "dark" });
export const ProjectPhone = phone({ nodeRef: "assistant-core" });

/** A feature: its acceptance run's recording, PARTIAL, with the step that failed. */
export const FeaturePartial = page({ nodeRef: "I-1" });
export const FeaturePartialDark = page({ nodeRef: "I-1", theme: "dark" });
export const FeaturePartialPhone = phone({ nodeRef: "I-1", theme: "dark" });

/** A change: the demo of its PR's current head (the FAIL at an earlier commit is history). */
export const Change = page({ nodeRef: "PR-9876" });

/** Home: one "▶" on each feature row that has a demo, PARTIAL said in words. */
export const HomeChips = page({});
export const HomeChipsDark = page({ theme: "dark" });
export const HomeChipsPhone = phone({});

/** "4 more demos": the feature's subtree, newest first, one line each. */
export const DemosList = page({ nodeRef: "I-1", search: "?peek=demos&at=I-1" });
export const DemosListDark = page({ nodeRef: "I-1", search: "?peek=demos&at=I-1", theme: "dark" });
export const DemosListPhone = phone({ nodeRef: "I-1", search: "?peek=demos&at=I-1" });

/** Home's "▶" on Agent assistant: the list opens with its acceptance run playing. */
export const PlayFromHome = page({ search: "?peek=demos&at=I-1&demo=demo-accept-1" });

/** A click on the row plays the demo in place (the PR page's player). */
export const PlayingInPlace: Story = {
  args: { nodeRef: "assistant-core" },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByTestId("v3-proof-play", {}, { timeout: 5000 }));
  },
};
export const PlayingInPlaceDark: Story = {
  ...PlayingInPlace,
  args: { ...PlayingInPlace.args, theme: "dark" },
};
