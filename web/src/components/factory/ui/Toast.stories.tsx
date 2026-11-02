import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect, useState } from "react";
import { useFactoryToast } from "./Toast";
import { PHONE, type StoryTheme, UiFrame } from "./uiStoryFixtures";

const STAY = 10 * 60 * 1000;

/** Shows one toast on mount and lets the reader raise it again; Undo also answers `u`. */
function Demo({ undo }: { undo: boolean }) {
  const { show } = useFactoryToast();
  const [undone, setUndone] = useState(0);
  useEffect(() => {
    show(
      undo
        ? {
            message: "Applied as definition v13. The project lead gets one team update.",
            undo: () => setUndone((n) => n + 1),
            durationMs: STAY,
          }
        : { message: "Answer sent to the project lead.", durationMs: STAY },
    );
  }, [show, undo]);
  return (
    <div className="flex flex-col items-start gap-2">
      <button
        type="button"
        className="rounded-md border border-border px-2.5 py-1 hover:bg-muted"
        onClick={() =>
          show(
            undo
              ? { message: "Paused the source.", undo: () => setUndone((n) => n + 1) }
              : { message: "Copied the link." },
          )
        }
      >
        Show the toast again
      </button>
      <p className="text-sm text-muted-foreground">Undo pressed {undone} times.</p>
    </div>
  );
}

function ToastStory({ theme = "light", undo = true }: { theme?: StoryTheme; undo?: boolean }) {
  return (
    <UiFrame theme={theme} viewport>
      <Demo undo={undo} />
    </UiFrame>
  );
}

const meta = {
  title: "Factory/UI/Toast",
  component: ToastStory,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof ToastStory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithUndo: Story = { args: {} };
export const WithUndoDark: Story = { args: { theme: "dark" } };
export const Plain: Story = { args: { undo: false } };
export const Phone: Story = { args: {}, ...PHONE };
