import type { Meta, StoryObj } from "@storybook/react-vite";
import { RangeBar } from "./RangeBar";
import { PHONE, Specimen, type StoryTheme, UiFrame } from "./uiStoryFixtures";

/** A predicted band with and without its readout (sample numbers). */
function Gallery({ theme = "light" }: { theme?: StoryTheme }) {
  return (
    <UiFrame theme={theme}>
      <div className="flex max-w-xl flex-col gap-5">
        <Specimen label="Won: actual inside the band">
          <RangeBar low={2} high={6} actual={4.1} unit="pp" verdict="won" legend />
        </Specimen>
        <Specimen label="Flat: actual near zero">
          <RangeBar low={1} high={4} actual={0.2} unit="pp" verdict="flat" legend />
        </Specimen>
        <Specimen label="Lost: actual below zero">
          <RangeBar low={1} high={3} actual={-0.8} unit="pp" verdict="lost" legend />
        </Specimen>
        <Specimen label="Not measured yet">
          <RangeBar low={1} high={4} unit="pp" legend />
        </Specimen>
      </div>
    </UiFrame>
  );
}

const meta = {
  title: "Factory/UI/RangeBar",
  component: Gallery,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof Gallery>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Light: Story = { args: {} };
export const Dark: Story = { args: { theme: "dark" } };
export const Phone: Story = { args: {}, ...PHONE };
