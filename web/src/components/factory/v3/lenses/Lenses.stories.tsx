import type { Meta, StoryObj } from "@storybook/react-vite";
import { type V3StoryOptions, V3PageStory } from "../fixture/v3StoryKit";

// Cockpit v3's lenses over the sample factory, each a full node page with the
// lens named in its address (`?lens=`): the assistant feature's projects over
// the day (hatched waits, amber where a person is the cause, shaded since the
// last look) and against their budgets, and where T-16 came from, top to
// bottom. The lens bar offers only the lenses with something to show.

const meta = {
  title: "Factory/Cockpit v3/Lenses",
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

/** Agent assistant's projects over the day: the core held for C-16 since 07:46, the panel queued all day. */
export const AgentAssistantTimeline = page({ nodeRef: "I-1", search: "?lens=timeline" });
export const AgentAssistantTimelineDark = page({
  nodeRef: "I-1",
  search: "?lens=timeline",
  theme: "dark",
});
export const AgentAssistantTimelinePhone = phone({ nodeRef: "I-1", search: "?lens=timeline" });
export const AgentAssistantTimelinePhoneDark = phone({
  nodeRef: "I-1",
  search: "?lens=timeline",
  theme: "dark",
});

/** Agent assistant's money: each project's cost against its budget, most first, and the total. */
export const AgentAssistantMoney = page({ nodeRef: "I-1", search: "?lens=money" });
export const AgentAssistantMoneyDark = page({
  nodeRef: "I-1",
  search: "?lens=money",
  theme: "dark",
});
export const AgentAssistantMoneyPhone = phone({ nodeRef: "I-1", search: "?lens=money" });

/** T-16's lineage: product › need (the ask, no user evidence) › feature (approved by C-11) › project › here. */
export const T16Lineage = page({ nodeRef: "T-16", search: "?lens=lineage" });
export const T16LineageDark = page({ nodeRef: "T-16", search: "?lens=lineage", theme: "dark" });
export const T16LineagePhone = phone({ nodeRef: "T-16", search: "?lens=lineage" });
/** On a phone the Lineage button opens the same lens as a sheet. */
export const T16LineageSheetPhoneDark = phone({
  nodeRef: "T-16",
  search: "?peek=lineage&at=T-16",
  theme: "dark",
});

/** assistant-core's tasks over the day: T-15 done, T-16 held for C-16 (amber). */
export const AssistantCoreTimeline = page({ nodeRef: "assistant-core", search: "?lens=timeline" });
