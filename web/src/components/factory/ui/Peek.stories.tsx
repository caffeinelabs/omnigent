import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { MemberAvatar, StateLabel } from "./MemberAvatar";
import { Peek } from "./Peek";
import { StateStrip } from "./StateStrip";
import { WhyChain } from "./WhyChain";
import {
  PHONE,
  SAMPLE_CHAIN_WAITING,
  SAMPLE_STRIP_STUCK,
  SAMPLE_TEAM,
  type StoryTheme,
  UiFrame,
} from "./uiStoryFixtures";

const dot = SAMPLE_TEAM[4]!;

/**
 * An agent panel in a peek (sample content). Side panel at desktop widths; a
 * 90 % bottom sheet with a grab handle when the viewport is 760 px or less.
 */
function PeekStory({ theme = "light", wide = false }: { theme?: StoryTheme; wide?: boolean }) {
  const [open, setOpen] = useState(true);
  return (
    <UiFrame theme={theme} viewport>
      <button
        type="button"
        className="rounded-md border border-border px-2.5 py-1 hover:bg-muted"
        onClick={() => setOpen(true)}
      >
        Open the panel
      </button>
      <Peek
        open={open}
        onOpenChange={setOpen}
        wide={wide}
        title={wide ? "Share a private draft link (sample)" : "Dot · Demo"}
        description={
          wide
            ? "Bet C-31 and the case against"
            : "on A failed build still ends in a rendered first draft (sample)"
        }
        footer={
          <>
            <button
              type="button"
              className="rounded-md bg-primary px-3 py-1 font-medium text-primary-foreground"
            >
              Message
            </button>
            <button type="button" className="rounded-md border border-border px-3 py-1">
              Restart
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <div className="flex items-start gap-3">
            <MemberAvatar {...dot} size={48} />
            <div className="flex flex-col gap-1">
              <StateLabel state={dot.state} />
              <span className="font-mono text-sm">claude · sonnet</span>
            </div>
          </div>
          <div className="rounded-lg border border-border p-3">
            <p className="font-semibold">
              Stuck: same browser step failed 3 times in 14 min (sample)
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              The reliability lead is restarting it. Nothing needs you.
            </p>
          </div>
          <WhyChain links={SAMPLE_CHAIN_WAITING} compact />
          <StateStrip segments={SAMPLE_STRIP_STUCK} />
        </div>
      </Peek>
    </UiFrame>
  );
}

const meta = {
  title: "Factory/UI/Peek",
  component: PeekStory,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof PeekStory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Side: Story = { args: {} };
export const SideDark: Story = { args: { theme: "dark" } };
export const Wide: Story = { args: { wide: true } };
/** Capture at 390 px: the same panel as a bottom sheet. */
export const SheetPhone: Story = { args: {}, ...PHONE };
export const SheetPhoneDark: Story = { args: { theme: "dark" }, ...PHONE };
