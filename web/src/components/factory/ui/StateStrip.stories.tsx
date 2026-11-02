import type { Meta, StoryObj } from "@storybook/react-vite";
import { StateStrip } from "./StateStrip";
import {
  PHONE,
  SAMPLE_STRIP_BUSY,
  SAMPLE_STRIP_STUCK,
  Specimen,
  type StoryTheme,
  UiFrame,
} from "./uiStoryFixtures";

/** A day of agent states: a stuck demo, a busy builder, a partial strip. */
function Gallery({ theme = "light" }: { theme?: StoryTheme }) {
  return (
    <UiFrame theme={theme}>
      <div className="flex max-w-xl flex-col gap-6">
        <Specimen label="Stuck at the end (hatched)">
          <StateStrip segments={SAMPLE_STRIP_STUCK} />
        </Specimen>
        <Specimen label="Busy, with a wait on a person">
          <StateStrip segments={SAMPLE_STRIP_BUSY} legend={false} />
        </Specimen>
        <Specimen label="Partial (only while someone had the cockpit open)">
          <StateStrip
            segments={[
              { state: "working", seconds: 2400 },
              { state: "idle", seconds: 1800 },
              { state: "working", seconds: 600 },
            ]}
            windowLabel="Last 80 minutes"
            partial
            legend={false}
          />
        </Specimen>
      </div>
    </UiFrame>
  );
}

const meta = {
  title: "Factory/UI/StateStrip",
  component: Gallery,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof Gallery>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Light: Story = { args: {} };
export const Dark: Story = { args: { theme: "dark" } };
export const Phone: Story = { args: {}, ...PHONE };
