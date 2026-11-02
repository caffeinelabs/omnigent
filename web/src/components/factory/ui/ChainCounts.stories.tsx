import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { ChainCounts } from "./ChainCounts";
import {
  PHONE,
  SAMPLE_CHAIN_COUNTS,
  SAMPLE_CHAIN_COUNTS_COLD,
  Specimen,
  type StoryTheme,
  UiFrame,
} from "./uiStoryFixtures";

/** Counts as filters (one selected), and a cold start where zeros are faint (sample counts). */
function Gallery({ theme = "light" }: { theme?: StoryTheme }) {
  const [selected, setSelected] = useState<string | null>("bets");
  return (
    <UiFrame theme={theme}>
      <div className="flex flex-col gap-5">
        <Specimen label="Live sample, Bets selected as a filter (the amber count waits for you)">
          <ChainCounts
            counts={SAMPLE_CHAIN_COUNTS}
            selected={selected}
            onSelect={setSelected}
            className="w-full"
          />
        </Specimen>
        <Specimen label="Cold start: zeros are faint">
          <ChainCounts
            counts={SAMPLE_CHAIN_COUNTS_COLD}
            label="From evidence to impact, cold start"
            className="w-full"
          />
        </Specimen>
      </div>
    </UiFrame>
  );
}

const meta = {
  title: "Factory/UI/ChainCounts",
  component: Gallery,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof Gallery>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Light: Story = { args: {} };
export const Dark: Story = { args: { theme: "dark" } };
export const Phone: Story = { args: {}, ...PHONE };
