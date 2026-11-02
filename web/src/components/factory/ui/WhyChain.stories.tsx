import type { Meta, StoryObj } from "@storybook/react-vite";
import { WhyChain } from "./WhyChain";
import {
  PHONE,
  SAMPLE_CHAIN_COMPLETE,
  SAMPLE_CHAIN_REQUESTED,
  SAMPLE_CHAIN_WAITING,
  Specimen,
  type StoryTheme,
  UiFrame,
} from "./uiStoryFixtures";

/** Complete, waiting on a bet, requested work (hatched links), lit, compact and vertical. */
function Gallery({ theme = "light" }: { theme?: StoryTheme }) {
  return (
    <UiFrame theme={theme}>
      <div className="flex flex-col gap-5">
        <Specimen label="Complete: evidence to a won readout">
          <WhyChain links={SAMPLE_CHAIN_COMPLETE} className="w-full" />
        </Specimen>
        <Specimen label="A bet that waits for you; project and readout missing">
          <WhyChain links={SAMPLE_CHAIN_WAITING} className="w-full" />
        </Specimen>
        <Specimen label="Requested work: no evidence, not measured">
          <WhyChain links={SAMPLE_CHAIN_REQUESTED} className="w-full" />
        </Specimen>
        <Specimen label="Lit (a Flow selection)">
          <WhyChain
            links={SAMPLE_CHAIN_COMPLETE.map((l) => ({ ...l, lit: true }))}
            className="w-full"
          />
        </Specimen>
        <Specimen label="Compact (side panel)">
          <div className="w-[480px] max-w-full">
            <WhyChain links={SAMPLE_CHAIN_WAITING} compact />
          </div>
        </Specimen>
        <Specimen label="Vertical">
          <div className="w-[220px]">
            <WhyChain links={SAMPLE_CHAIN_REQUESTED} vertical />
          </div>
        </Specimen>
      </div>
    </UiFrame>
  );
}

const meta = {
  title: "Factory/UI/WhyChain",
  component: Gallery,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof Gallery>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Light: Story = { args: {} };
export const Dark: Story = { args: { theme: "dark" } };
export const Phone: Story = { args: {}, ...PHONE };
