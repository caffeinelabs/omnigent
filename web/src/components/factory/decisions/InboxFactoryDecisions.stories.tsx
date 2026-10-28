import type { Meta, StoryObj } from "@storybook/react-vite";
import type { OpenDecisionItem } from "@/lib/factoryApi";
import { DocumentTheme } from "../board/boardStoryKit";
import { FactoryStoryFrame } from "../factoryStoryFixtures";
import { inboxDecisionItems } from "./__fixtures__/decisionFixtures";
import { InboxFactoryDecisions } from "./InboxFactoryDecisions";

/** D-2 (Atlas factory) and the Docs factory's question are assigned to the viewer. */
function items(mine = true): OpenDecisionItem[] {
  return inboxDecisionItems().map((item, i) => ({ ...item, mine: mine && i !== 1 }));
}

function inbox(options: { theme?: "light" | "dark"; mine?: boolean; error?: boolean } = {}): Story {
  const theme = options.theme ?? "light";
  return {
    render: () => (
      <FactoryStoryFrame mode="viewport" theme={theme}>
        <DocumentTheme theme={theme} />
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <div className="mx-auto w-full max-w-3xl">
            <InboxFactoryDecisions
              items={items(options.mine ?? true)}
              error={options.error ? new Error("HTTP 503") : null}
              onRetry={() => {}}
            />
          </div>
        </div>
      </FactoryStoryFrame>
    ),
  };
}

const meta = {
  title: "Factory/Decisions/Inbox",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** "Assigned to you" first, across factories; the rest grouped by factory. */
export const AssignedToYou = inbox();

export const AssignedToYouDark = inbox({ theme: "dark" });

export const AssignedToYouMobile: Story = {
  ...inbox(),
  globals: { viewport: { value: "mobile2" } },
};

/** Nothing assigned to the viewer: only the per-factory groups. */
export const NothingAssigned = inbox({ mine: false });

/** The read failed: the stale list stays with the error above it. */
export const ReadError = inbox({ error: true });
