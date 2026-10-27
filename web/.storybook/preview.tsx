import type { Preview } from "@storybook/react-vite";
import { type ReactNode, useState } from "react";
import { addons } from "storybook/preview-api";
import {
  PLAY_FUNCTION_THREW_EXCEPTION,
  STORY_FINISHED,
  STORY_PREPARED,
  type StoryFinishedPayload,
} from "storybook/internal/core-events";
import { ForceThemeContext, ThemeProvider } from "@/components/theme/ThemeProvider";
import { TooltipProvider } from "@/components/ui/tooltip";
import "katex/dist/katex.min.css";
import "streamdown/styles.css";
import "../src/index.css";

const storybookChannel = addons.getChannel();
storybookChannel.on(STORY_PREPARED, () => {
  delete document.documentElement.dataset.storybookPlayError;
  delete document.documentElement.dataset.storybookStoryId;
  delete document.documentElement.dataset.storybookStoryStatus;
});
storybookChannel.on(PLAY_FUNCTION_THREW_EXCEPTION, () => {
  document.documentElement.dataset.storybookPlayError = "true";
});
storybookChannel.on(STORY_FINISHED, ({ storyId, status }: StoryFinishedPayload) => {
  document.documentElement.dataset.storybookStoryId = storyId;
  document.documentElement.dataset.storybookStoryStatus = status;
});

/** The app's theme provider, which a story can force to dark (portals included). */
function StoryTheme({ children }: { children: ReactNode }) {
  const [forced, setForced] = useState<"light" | "dark" | undefined>();
  return (
    <ForceThemeContext.Provider value={setForced}>
      <ThemeProvider forcedTheme={forced}>{children}</ThemeProvider>
    </ForceThemeContext.Provider>
  );
}

const preview: Preview = {
  decorators: [
    (Story) => (
      <StoryTheme>
        <TooltipProvider>
          <div className="min-w-80 max-w-3xl p-6">
            <Story />
          </div>
        </TooltipProvider>
      </StoryTheme>
    ),
  ],
  parameters: {
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /Date$/i,
      },
    },
    layout: "centered",
  },
};

export default preview;
