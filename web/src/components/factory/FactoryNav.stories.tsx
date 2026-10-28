import type { Meta, StoryObj } from "@storybook/react-vite";
import { ClockIcon, InboxIcon, LayoutDashboardIcon, SquarePenIcon, WalletIcon } from "lucide-react";
import { PrimaryNavLink } from "@/shell/PrimaryNavLink";
import type { FactoryListResponse } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { allDoneSnapshot, factoryList, summaryOf } from "./__fixtures__/liveRun";
import { FactoryNavLink } from "./FactoryNavLink";
import { FactoryStoryFrame, type FactoryFrameOptions } from "./factoryStoryFixtures";
import { sharedFactoryList } from "./shell/__fixtures__/shellFixtures";

/** The sidebar's primary nav as Sidebar lays it out, with the Factory entry in place. */
function SidebarNav({ factoryActive = false }: { factoryActive?: boolean }) {
  return (
    <aside className="conversations-sidebar flex w-64 flex-col rounded-xl border border-border bg-card py-2">
      <div className="flex flex-col gap-0 px-2" data-testid="sidebar-primary-nav">
        <PrimaryNavLink
          to="/"
          label="New session"
          icon={SquarePenIcon}
          active={false}
          componentId="story.new"
        />
        <PrimaryNavLink
          to="/tasks"
          label="Automations"
          icon={ClockIcon}
          active={false}
          componentId="story.tasks"
        />
        <PrimaryNavLink
          to="/inbox"
          label="Inbox"
          icon={InboxIcon}
          active={false}
          componentId="story.inbox"
          trailing={
            <span
              className={cn(
                "ml-auto inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-10 font-medium text-[var(--sidebar-active-foreground)] tabular-nums",
                "bg-[var(--sidebar-active)]",
              )}
            >
              3
            </span>
          }
        />
        <FactoryNavLink active={factoryActive} />
        <PrimaryNavLink
          to="/canvas"
          label="Canvas"
          icon={LayoutDashboardIcon}
          active={false}
          componentId="story.canvas"
        />
        <PrimaryNavLink
          to="/usage"
          label="Usage"
          icon={WalletIcon}
          active={false}
          componentId="story.usage"
        />
      </div>
    </aside>
  );
}

function navStory(
  list: FactoryListResponse | "pending",
  options: FactoryFrameOptions & { factoryActive?: boolean } = {},
): Story {
  const { factoryActive, ...frame } = options;
  return {
    render: () => (
      <FactoryStoryFrame
        route={factoryActive ? "/factory" : "/"}
        routes={{ "/v1/factory/factories": list === "pending" ? "pending" : { body: list } }}
        className="p-4"
        {...frame}
      >
        <SidebarNav factoryActive={factoryActive} />
      </FactoryStoryFrame>
    ),
  };
}

const meta = {
  title: "Factory/Sidebar entry",
  component: FactoryNavLink,
  args: { active: false },
} satisfies Meta<typeof FactoryNavLink>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Agents running, 2 decisions + 1 PR ready for review need the human. */
export const NeedsYou = navStory(factoryList());

export const NeedsYouDark = navStory(factoryList(), { theme: "dark" });

/** On a cockpit route the entry carries the active wash; the pill stays amber. */
export const Active = navStory(factoryList(), { factoryActive: true });

export const ActiveDark = navStory(factoryList(), { factoryActive: true, theme: "dark" });

/** Every factory finished: no dot, no pill. */
export const Quiet = navStory({ data: [summaryOf(allDoneSnapshot())] });

/** The list is still loading: the plain entry, nothing guessed. */
export const Loading = navStory("pending");

/** Factories shared with the viewer sit under the entry (their members leave Shared sessions). */
export const SharedWithYou = navStory(sharedFactoryList());

export const SharedWithYouDark = navStory(sharedFactoryList(), { theme: "dark" });

/** On a shared factory's page: its row reads as the current one. */
export const SharedActive: Story = {
  render: () => (
    <FactoryStoryFrame
      route={`/factory/${sharedFactoryList().data[0]!.id}`}
      routes={{ "/v1/factory/factories": { body: sharedFactoryList() } }}
      className="p-4"
    >
      <SidebarNav factoryActive />
    </FactoryStoryFrame>
  ),
};
