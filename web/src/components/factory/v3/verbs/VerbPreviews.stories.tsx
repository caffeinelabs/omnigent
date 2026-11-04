import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, waitFor, within } from "storybook/test";
import { type V3StoryOptions, V3PageStory } from "../fixture/v3StoryKit";

// Cockpit v3's verbs at the node over the sample factory: each preview as it
// opens (the parent's columns with a dashed ghost, the roll-up change, who
// approves) and the page after Apply, with the toast's Undo held up by the
// pointer. Nothing changes until Apply; words in the chat open these same
// previews (the address's `?verb=` is how another node's chat sends them).

const meta = {
  title: "Factory/Cockpit v3/Verb previews",
  component: V3PageStory,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof V3PageStory>;

export default meta;
type Story = StoryObj<typeof meta>;

const preview = (args: V3StoryOptions): Story => ({ args });
const phone = (args: V3StoryOptions): Story => ({
  args,
  globals: { viewport: { value: "mobile2" } },
});

/** Click the preview's Apply (after `before` picks a field), then hold the toast up. */
function applied(args: V3StoryOptions, before?: (body: HTMLElement) => Promise<void>): Story {
  return {
    args,
    play: async ({ canvasElement }) => {
      const body = within(canvasElement.ownerDocument.body);
      const panel = await body.findByTestId("v3-preview", {}, { timeout: 5000 });
      if (before) await before(panel);
      const apply = within(panel).getByTestId("v3-preview-apply");
      await waitFor(() => {
        if ((apply as HTMLButtonElement).disabled) throw new Error("the preview is not in yet");
      });
      await userEvent.click(apply);
      const toast = await body.findByTestId("factory-toast");
      await userEvent.hover(toast);
    },
  };
}

const START = { nodeRef: "assistant-panel-ux", search: "?verb=start" };
const ADD = { nodeRef: "I-1", search: "?verb=add&title=Ads%20network%20integration" };
const TEAM = { nodeRef: "assistant-core", search: "?verb=team&member=Luz&to=gpt-5-codex" };
const AUTO = { nodeRef: "I-1", search: "?verb=auto" };

/** Start now on the panel: it moves to First against #9876; the wait it really needs is met. */
export const StartNow = preview(START);
export const StartNowDark = preview({ ...START, theme: "dark" });
export const StartNowPhone = phone(START);
/** After Apply: the panel moves, the chip names #9876, and the toast offers Undo. */
export const StartNowApplied = applied(START);

/** "also plan ads network integration" on the feature: the held project is pulled forward, not copied. */
export const AddPullForward = preview(ADD);
export const AddPullForwardDark = preview({ ...ADD, theme: "dark" });
export const AddPullForwardPhone = phone(ADD);
export const AddPullForwardApplied = applied(ADD);
/** Add child with nothing typed yet: the title field, the order, Apply off. */
export const AddEmpty = preview({ nodeRef: "I-1", search: "?verb=add" });

/** Team, as opened: every member with a vendor and a model, nothing preselected. */
export const TeamNothingPicked = preview({ nodeRef: "assistant-core", search: "?verb=team" });
/** "swap luz to codex": that one change staged, with the cost line and who approves. */
export const TeamSwap = preview(TEAM);
export const TeamSwapDark = preview({ ...TEAM, theme: "dark" });
export const TeamSwapPhone = phone(TEAM);
export const TeamSwapApplied = applied(TEAM);

/** Autonomy & merge for the feature and everything under it (main stays human-merged). */
export const AutonomyAndMerge = preview(AUTO);
export const AutonomyAndMergeDark = preview({ ...AUTO, theme: "dark" });
export const AutonomyAndMergeApplied = applied(AUTO);

/** Pause a task: nothing new starts under it; Resume brings it back. */
export const Pause = preview({ nodeRef: "T-16", search: "?verb=pause" });
export const PauseApplied = applied({ nodeRef: "T-16", search: "?verb=pause" });

/** Move offers only parents one level up; both roll-ups change. */
export const Move = preview({ nodeRef: "assistant-panel-ux", search: "?verb=move" });
export const MoveApplied = applied(
  { nodeRef: "assistant-panel-ux", search: "?verb=move" },
  async (panel) => {
    await userEvent.click(await within(panel).findByRole("button", { name: "Your shortlist" }));
  },
);

/** Name an implicit parent, or file it under a real one. */
export const NameIt = preview({ nodeRef: "feature:from-C-13", search: "?verb=name" });
export const NameItApplied = applied(
  { nodeRef: "feature:from-C-13", search: "?verb=name" },
  async (panel) => {
    await userEvent.type(within(panel).getByLabelText("Name"), "Small fixes", { delay: 5 });
  },
);
