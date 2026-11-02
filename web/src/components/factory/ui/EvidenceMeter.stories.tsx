import type { Meta, StoryObj } from "@storybook/react-vite";
import { EvidenceMeter } from "./EvidenceMeter";
import { SourceMix } from "./SourceMix";
import { EVIDENCE_LEVELS } from "./statusTokens";
import { PHONE, SAMPLE_SOURCES, Specimen, type StoryTheme, UiFrame } from "./uiStoryFixtures";

/** The evidence meter per level (a hunch is hollow) and the source mix bar. */
function Gallery({ theme = "light" }: { theme?: StoryTheme }) {
  return (
    <UiFrame theme={theme}>
      <div className="flex flex-col gap-4">
        {EVIDENCE_LEVELS.map((level) => (
          <Specimen key={level} label={`Evidence: ${level}`}>
            <EvidenceMeter level={level} showLabel />
          </Specimen>
        ))}
        <Specimen label="Source mix: three sources and a team hunch (hatched)">
          <SourceMix sources={SAMPLE_SOURCES} showTags />
        </Specimen>
        <Specimen label="Source mix: one source">
          <SourceMix sources={[{ tag: "web", count: 30 }]} showTags />
        </Specimen>
        <Specimen label="Source mix: only a hunch">
          <SourceMix sources={[{ tag: "team", count: 2, hunch: true }]} showTags />
        </Specimen>
        <Specimen label="Source mix: none yet">
          <SourceMix sources={[]} showTags />
        </Specimen>
      </div>
    </UiFrame>
  );
}

const meta = {
  title: "Factory/UI/EvidenceMeter",
  component: Gallery,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof Gallery>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Light: Story = { args: {} };
export const Dark: Story = { args: { theme: "dark" } };
export const Phone: Story = { args: {}, ...PHONE };
