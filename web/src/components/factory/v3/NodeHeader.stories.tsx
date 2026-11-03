import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { AboveProvider } from "./NodeContext";
import { NodeHeader } from "./NodeHeader";
import { type SampleOptions, sampleEngine } from "./fixture/sampleNodeSource";
import { V3PartStory } from "./fixture/v3StoryKit";

// The node header over the sample factory, one story per kind of status line:
// moving, a path that ends at you, a hop to another node, held on purpose,
// not started with met links (why open), done, later, proposed, and the root.

interface HeaderArgs extends SampleOptions {
  nodeRef: string;
  theme?: "light" | "dark";
  why?: boolean;
  width?: number;
}

function HeaderStory({ nodeRef, theme, why = false, width, ...sample }: HeaderArgs) {
  const [page] = useState(() => sampleEngine(sample).node(nodeRef));
  const [open, setOpen] = useState(why);
  if (!page) return null;
  const above = page.status.ends === "person" ? (page.status.decision?.id ?? null) : null;
  return (
    <V3PartStory theme={theme} width={width}>
      <AboveProvider aboveId={above}>
        <NodeHeader page={page} whyOpen={open} onWhyChange={setOpen} />
      </AboveProvider>
    </V3PartStory>
  );
}

const meta = {
  title: "Factory/Cockpit v3/Header",
  component: HeaderStory,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof HeaderStory>;

export default meta;
type Story = StoryObj<typeof meta>;

const APPLIED: [string, string][] = [["D-45", "Apply"]];

/** Moving: once C-16 applied, the task's builder opens its PR. */
export const Moving: Story = { args: { nodeRef: "T-16", answered: APPLIED } };
export const MovingDark: Story = { args: { nodeRef: "T-16", answered: APPLIED, theme: "dark" } };

/** A path that ends at you, with the one answer and "Open C-16". */
export const NeedsWithPath: Story = { args: { nodeRef: "assistant-core" } };
export const NeedsWithPathDark: Story = { args: { nodeRef: "assistant-core", theme: "dark" } };

/** Waits on another node (a hop): the core it needs is moving now. */
export const WaitsOnHop: Story = { args: { nodeRef: "assistant-panel-ux", answered: APPLIED } };
export const WaitsOnHopDark: Story = {
  args: { nodeRef: "assistant-panel-ux", answered: APPLIED, theme: "dark" },
};

/** Held on purpose: done locally, waiting for a scope decision. */
export const HeldOnPurpose: Story = { args: { nodeRef: "T-16" } };
export const HeldOnPurposeDark: Story = { args: { nodeRef: "T-16", theme: "dark" } };

/** Not started for 7 h, why open: the numbered path and the three met links with no reason. */
export const NotStartedWhyOpen: Story = { args: { nodeRef: "assistant-panel-ux", why: true } };
export const NotStartedWhyOpenDark: Story = {
  args: { nodeRef: "assistant-panel-ux", why: true, theme: "dark" },
};

/** Done: merged and in the base env. */
export const Done: Story = { args: { nodeRef: "connectors-core" } };
export const DoneDark: Story = { args: { nodeRef: "connectors-core", theme: "dark" } };

/** Later: held for a later wave, with its reason. */
export const Later: Story = { args: { nodeRef: "sales-skill" } };
export const LaterDark: Story = { args: { nodeRef: "sales-skill", theme: "dark" } };

/** Proposed (a ghost): Atlas asks to start it. */
export const Proposed: Story = { args: { nodeRef: "live-search-fix" } };
export const ProposedDark: Story = { args: { nodeRef: "live-search-fix", theme: "dark" } };

/** The root: the top blocker, what else it holds, "nothing in the product is moving". */
export const Root: Story = { args: { nodeRef: "product" } };
export const RootDark: Story = { args: { nodeRef: "product", theme: "dark" } };

/** At 390 the path wraps and the answer moves under it. */
export const Phone: Story = {
  args: { nodeRef: "assistant-panel-ux", width: 390 },
  globals: { viewport: { value: "mobile2" } },
};
export const PhoneDark: Story = {
  args: { nodeRef: "product", width: 390, theme: "dark" },
  globals: { viewport: { value: "mobile2" } },
};
