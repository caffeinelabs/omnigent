import type { Meta, StoryObj } from "@storybook/react-vite";
import { LoopStrip } from "./LoopStrip";
import {
  PHONE,
  SAMPLE_GOAL,
  SAMPLE_LOOP,
  SAMPLE_LOOP_COLD,
  type StoryTheme,
  UiFrame,
} from "./uiStoryFixtures";

type Variant = "on" | "off" | "cold";

/** The loop with discovery on, off (Build and Measure only), and just turned on (sample data). */
function Loop({ theme = "light", variant = "on" }: { theme?: StoryTheme; variant?: Variant }) {
  return (
    <UiFrame theme={theme} viewport>
      <LoopStrip
        stages={variant === "cold" ? SAMPLE_LOOP_COLD : SAMPLE_LOOP}
        discoveryOn={variant !== "off"}
        goal={
          variant === "cold"
            ? {
                statement: null,
                measured: false,
                note: "Opportunities are ranked against goals. Scout suggested one.",
              }
            : SAMPLE_GOAL
        }
        goalAction={{ label: variant === "cold" ? "Set a goal" : "Edit strategy", to: "/factory" }}
        turnOn={{ label: "Turn on discovery", to: "/factory" }}
      />
    </UiFrame>
  );
}

const meta = {
  title: "Factory/UI/LoopStrip",
  component: Loop,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof Loop>;

export default meta;
type Story = StoryObj<typeof meta>;

export const DiscoveryOn: Story = { args: {} };
export const DiscoveryOnDark: Story = { args: { theme: "dark" } };
export const DiscoveryOff: Story = { args: { variant: "off" } };
export const ColdStart: Story = { args: { variant: "cold" } };
export const ColdStartDark: Story = { args: { variant: "cold", theme: "dark" } };
export const Phone: Story = { args: {}, ...PHONE };
export const PhoneDiscoveryOff: Story = { args: { variant: "off" }, ...PHONE };
