import type { Meta, StoryObj } from "@storybook/react-vite";
import { BubbleView, bubbleKey } from "@/components/chat/chatBubbleParts";
import {
  type FactoryFrame,
  type TranscriptName,
  fixtureBubbles,
  withFactoryFrame,
} from "./factoryStoryKit";

/** A fixture transcript rendered the way the chat column renders it. */
function TranscriptColumn({ name, fold }: { name: TranscriptName; fold: boolean }) {
  return (
    <div className="flex w-full min-w-0 flex-col gap-4" data-testid={`story-transcript-${name}`}>
      {fixtureBubbles(name, fold).map((bubble) => (
        <BubbleView key={bubbleKey(bubble)} bubble={bubble} />
      ))}
    </div>
  );
}

const meta = {
  title: "Factory/Transcript/Transcripts",
  decorators: [withFactoryFrame],
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const dark: FactoryFrame = { theme: "dark" };
const phone: FactoryFrame = { width: 390 };

/** The lead's transcript: cards for factory traffic, folded no-op heartbeats. */
export const AtlasTranscript: Story = { render: () => <TranscriptColumn name="atlas" fold /> };

export const AtlasTranscriptDark: Story = {
  ...AtlasTranscript,
  parameters: { factoryFrame: dark },
};

export const AtlasTranscriptPhone: Story = {
  ...AtlasTranscript,
  parameters: { factoryFrame: phone },
};

/** The same transcript with every heartbeat left unfolded. */
export const AtlasTranscriptUnfolded: Story = {
  render: () => <TranscriptColumn name="atlas" fold={false} />,
};

/** A project lead: PR, preview and demo outcomes, cockpit controls and reviews. */
export const ProjectLeadTranscript: Story = {
  render: () => <TranscriptColumn name="pl_api" fold />,
};

export const ProjectLeadTranscriptDark: Story = {
  ...ProjectLeadTranscript,
  parameters: { factoryFrame: dark },
};

export const ProjectLeadTranscriptPhone: Story = {
  ...ProjectLeadTranscript,
  parameters: { factoryFrame: phone },
};

/** Sentinel verifying a READY PR against its preview. */
export const SentinelTranscript: Story = {
  render: () => <TranscriptColumn name="sentinel" fold />,
};
