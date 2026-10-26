import type { Meta, StoryObj } from "@storybook/react-vite";
import type { ComponentProps } from "react";
import { ChatHeader } from "@/shell/ChatHeader";
import type { Session } from "@/lib/types";
import {
  LIVE_RUN_ATLAS_ID,
  LIVE_RUN_FACTORY_ID,
  LIVE_RUN_PL_API_ID,
  atlasSession,
  icSession,
  plSession,
} from "./__fixtures__/liveRun";
import { FactoryStrip } from "./FactoryStrip";
import { FactoryStoryFrame, type FactoryFrameOptions } from "./factoryStoryFixtures";

const mobileMenu: ComponentProps<typeof ChatHeader>["mobileMenu"] = {
  fileViewerOpen: false,
  panelOpen: false,
  terminalFirst: false,
  executionLogsOpen: false,
  filesPanelOpen: false,
  subagentsPanelOpen: false,
  shellsPanelOpen: false,
  hideTerminalsTab: false,
  showShellsTab: false,
  terminalsLength: 0,
  debugMode: false,
  changedCount: 0,
  subagentsWorking: 0,
  agentCount: 1,
  onOpenFiles: () => undefined,
  onOpenChanges: () => undefined,
  onOpenShells: () => undefined,
  onOpenSubagents: () => undefined,
  githubPanelOpen: false,
  onOpenGithub: () => undefined,
  onOpenMainExecutionLog: () => undefined,
};

const noop = () => undefined;

type HeaderProps = Partial<ComponentProps<typeof ChatHeader>> & { conversationId: string };

function header(props: HeaderProps) {
  return (
    <ChatHeader
      sidebarOpen={false}
      onOpenSidebar={noop}
      isChildSession={false}
      conversationTitle={null}
      projectName="Atlas factory"
      projectIcon="🏭"
      boundAgent={undefined}
      wrapperLabel={null}
      canShare
      canFork={false}
      onShare={noop}
      onFork={noop}
      hasAgentInfo={false}
      onAgentInfo={noop}
      hasHeaderMenu
      showFilesPanel
      hasRailContent
      rightPanelOpen={false}
      onToggleRightPanel={noop}
      mobileMenu={mobileMenu}
      {...props}
    />
  );
}

/**
 * The real chat header, with the strip mounted where ChatHeader puts it, over
 * a muted stand-in for the transcript.
 */
function headerStory(
  sessions: Session[],
  props: HeaderProps,
  frame: FactoryFrameOptions = {},
): Story {
  return {
    render: () => (
      <FactoryStoryFrame
        mode="viewport"
        route={`/c/${props.conversationId}`}
        sessions={sessions}
        {...frame}
      >
        <div className="relative h-14 shrink-0 md:h-12">{header(props)}</div>
        <div className="mx-auto mt-6 flex w-full max-w-3xl flex-col gap-3 px-4" aria-hidden>
          <div className="h-4 w-2/3 rounded bg-muted" />
          <div className="h-4 w-1/2 rounded bg-muted" />
          <div className="h-4 w-3/5 rounded bg-muted" />
        </div>
      </FactoryStoryFrame>
    ),
  };
}

const meta = {
  title: "Factory/Chat header strip",
  component: FactoryStrip,
  args: { sessionId: LIVE_RUN_PL_API_ID },
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof FactoryStrip>;

export default meta;
type Story = StoryObj<typeof meta>;

const PL_HEADER: HeaderProps = {
  conversationId: LIVE_RUN_PL_API_ID,
  conversationTitle: "PL · api-version",
  factoryParent: { id: LIVE_RUN_ATLAS_ID, title: "Atlas" },
};

/** PL · api-version: #9630 ready, preview at head, demo passed. */
export const ProjectLead = headerStory([plSession()], PL_HEADER);

export const ProjectLeadDark = headerStory([plSession()], PL_HEADER, { theme: "dark" });

/** PL · db-version: blocked on D-2, preview behind the PR head. */
export const ProjectLeadBlocked = headerStory(
  [
    {
      ...plSession(),
      id: "a2fc64b9e92e54c8b2f0f78038df202d",
      title: "PL · db-version",
      labels: { ...plSession().labels, "omnigent.factory.project": "db-version" },
    },
  ],
  {
    ...PL_HEADER,
    conversationId: "a2fc64b9e92e54c8b2f0f78038df202d",
    conversationTitle: "PL · db-version",
  },
);

/** An IC sub-agent: placement read from its root PL. */
export const SubAgent = headerStory([plSession(), icSession()], {
  conversationId: icSession().id,
  isChildSession: true,
  conversationTitle: "PL · api-version",
  titleLinkTo: `/c/${LIVE_RUN_PL_API_ID}`,
  subAgentName: "ic_codex",
  projectName: null,
});

/** Atlas: the whole factory at a glance. */
export const Lead = headerStory([atlasSession()], {
  conversationId: LIVE_RUN_ATLAS_ID,
  conversationTitle: "Atlas",
});

/** The snapshot is still loading: labels only, still a way into the cockpit. */
export const SnapshotLoading = headerStory([plSession()], PL_HEADER, {
  routes: { [`/v1/factory/factories/${LIVE_RUN_FACTORY_ID}`]: "pending" },
});

/** The snapshot failed (e.g. not deployed yet): the same label-only strip. */
export const SnapshotUnavailable = headerStory([plSession()], PL_HEADER, {
  routes: { [`/v1/factory/factories/${LIVE_RUN_FACTORY_ID}`]: { error: 404 } },
});

/** A narrow header (a side panel open): low-priority segments drop out whole. */
export const NarrowHeader: Story = {
  render: () => (
    <FactoryStoryFrame width={760} route={`/c/${LIVE_RUN_PL_API_ID}`} sessions={[plSession()]}>
      <div className="relative h-12">{header({ ...PL_HEADER, sidebarOpen: true })}</div>
    </FactoryStoryFrame>
  ),
};
