import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { LevelStrip, PhoneLevelRow } from "./LevelStrip";
import { type SampleOptions, sampleEngine } from "./fixture/sampleNodeSource";
import { V3PartStory } from "./fixture/v3StoryKit";

// The level strip: ancestors, the node, each level below with its counts and
// state bar, and the results. Empty levels are left out; no cell says 0.

interface StripArgs extends SampleOptions {
  nodeRef: string;
  theme?: "light" | "dark";
  phone?: boolean;
}

function StripStory({ nodeRef, theme, phone, ...sample }: StripArgs) {
  const [page] = useState(() => sampleEngine(sample).node(nodeRef));
  if (!page) return null;
  const shown = page.home ?? (page.children.level || null);
  return (
    <V3PartStory theme={theme} width={phone ? 390 : 1440}>
      {phone ? (
        <PhoneLevelRow page={page} shown={shown} />
      ) : (
        <LevelStrip page={page} shown={shown} />
      )}
    </V3PartStory>
  );
}

const meta = {
  title: "Factory/Cockpit v3/Level strip",
  component: StripStory,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof StripStory>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The product: needs, features, projects, tasks, changes and runs below; Goal hidden (none exist). */
export const Root: Story = { args: { nodeRef: "product" } };
export const RootDark: Story = { args: { nodeRef: "product", theme: "dark" } };
export const RootPhone: Story = {
  args: { nodeRef: "product", phone: true },
  globals: { viewport: { value: "mobile2" } },
};

/** A feature: its need and the product above it, "+4 later" and "1 you" on projects. */
export const Feature: Story = { args: { nodeRef: "I-1" } };
export const FeatureDark: Story = { args: { nodeRef: "I-1", theme: "dark" } };
export const FeaturePhone: Story = {
  args: { nodeRef: "I-1", phone: true, theme: "dark" },
  globals: { viewport: { value: "mobile2" } },
};

/** A project whose need and feature are implicit, folded into it (in italics). */
export const ProjectFolded: Story = { args: { nodeRef: "build-tier-drafts-3" } };

/** A project: tasks, changes, runs below. */
export const Project: Story = { args: { nodeRef: "assistant-core" } };

/** A task: nothing merged yet. */
export const Task: Story = { args: { nodeRef: "T-16" } };

/** A run: the last level, nothing below. */
export const Run: Story = { args: { nodeRef: "run:luz-t16", theme: "dark" } };

/** The factory tree (its own root and levels). */
export const FactoryTree: Story = { args: { nodeRef: "fx" } };

/** At scale: goals appear, hundreds of tasks, the bars carry the states. */
export const Scale: Story = { args: { nodeRef: "product", scale: true } };
export const ScaleDark: Story = { args: { nodeRef: "product", scale: true, theme: "dark" } };

/** Level names are data: Feature and Project renamed Outcome and Workstream. */
export const RenamedLevels: Story = { args: { nodeRef: "I-1", renamed: true } };
export const RenamedLevelsPhone: Story = {
  args: { nodeRef: "I-1", renamed: true, phone: true },
  globals: { viewport: { value: "mobile2" } },
};
