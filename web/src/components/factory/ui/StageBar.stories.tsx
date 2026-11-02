import type { Meta, StoryObj } from "@storybook/react-vite";
import { StageBar } from "./StageBar";
import { STAGE_STEPS, STAGE_STEP_LABEL } from "./statusTokens";
import { PHONE, Specimen, type StoryTheme, UiFrame } from "./uiStoryFixtures";

/** The bar at every step, broken, and with its labels (project page). */
function Gallery({ theme = "light" }: { theme?: StoryTheme }) {
  return (
    <UiFrame theme={theme}>
      <div className="flex max-w-xl flex-col gap-4">
        {STAGE_STEPS.map((step) => (
          <Specimen key={step} label={STAGE_STEP_LABEL[step]}>
            <StageBar stage={step} className="max-w-[150px]" />
          </Specimen>
        ))}
        <Specimen label="Broken (a stopped project at Build)">
          <StageBar stage="building" broken className="max-w-[150px]" />
        </Specimen>
        <Specimen label="Full width with labels">
          <StageBar stage="verify" labels />
        </Specimen>
      </div>
    </UiFrame>
  );
}

const meta = {
  title: "Factory/UI/StageBar",
  component: Gallery,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof Gallery>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Light: Story = { args: {} };
export const Dark: Story = { args: { theme: "dark" } };
export const Phone: Story = { args: {}, ...PHONE };
