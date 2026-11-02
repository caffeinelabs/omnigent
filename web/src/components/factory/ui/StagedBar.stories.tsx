import type { Meta, StoryObj } from "@storybook/react-vite";
import { StagedBar } from "./StagedBar";
import { PHONE, Specimen, type StoryTheme, UiFrame } from "./uiStoryFixtures";

const noop = () => undefined;

/** Inline bars (one and three changes) and the floating bar at the bottom. */
function Bars({ theme = "light" }: { theme?: StoryTheme }) {
  return (
    <UiFrame theme={theme} viewport>
      <div className="flex max-w-xl flex-col gap-4">
        <Specimen label="One change (Steer)">
          <StagedBar count={1} context="v13" onReview={noop} placement="inline" />
        </Specimen>
        <Specimen label="Three team changes, with Discard">
          <StagedBar
            count={3}
            noun="team change"
            onReview={noop}
            onDiscard={noop}
            placement="inline"
          />
        </Specimen>
        <p className="text-sm text-muted-foreground">
          The floating bar sits at the bottom, above the phone tab bar.
        </p>
      </div>
      <StagedBar count={3} context="v13" onReview={noop} />
    </UiFrame>
  );
}

const meta = {
  title: "Factory/UI/StagedBar",
  component: Bars,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof Bars>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Light: Story = { args: {} };
export const Dark: Story = { args: { theme: "dark" } };
export const Phone: Story = { args: {}, ...PHONE };
