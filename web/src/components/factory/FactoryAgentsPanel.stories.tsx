import type { Meta, StoryObj } from "@storybook/react-vite";
import { childSessionsQueryKey, type ChildSessionInfo } from "@/hooks/useChildSessions";
import { SubagentsPanel } from "@/shell/SubagentsPanel";
import {
  LIVE_RUN_ATLAS_ID,
  LIVE_RUN_PL_API_ID,
  LIVE_RUN_PL_DB_ID,
  atlasSession,
  plSession,
} from "./__fixtures__/liveRun";
import { FactoryStoryFrame } from "./factoryStoryFixtures";

function child(overrides: Partial<ChildSessionInfo> & { id: string }): ChildSessionInfo {
  return {
    title: null,
    task_summary: null,
    tool: null,
    session_name: null,
    current_task_status: null,
    busy: false,
    last_message_preview: null,
    pending_elicitations_count: 0,
    ...overrides,
  };
}

const member = (id: string, title: string, labels: Record<string, string>, busy = false) =>
  child({ id, title, tool: title, labels, busy });

// Atlas's members (joined by the factory parent label) and one PL's own team.
const TREE: Record<string, ChildSessionInfo[]> = {
  [LIVE_RUN_ATLAS_ID]: [
    member("713c96e676515bd992f5c15015101a2e", "Lead B · Sentinel", {
      "omnigent.factory.role": "lead",
      "omnigent.factory.parent": LIVE_RUN_ATLAS_ID,
      "omnigent.factory.health": "dead",
      "omnigent.factory.summary": "relaunch failed 3× (runner_unavailable)",
    }),
    member(LIVE_RUN_PL_API_ID, "PL · api-version", {
      "omnigent.factory.role": "project_lead",
      "omnigent.factory.project": "api-version",
      "omnigent.factory.parent": LIVE_RUN_ATLAS_ID,
      "omnigent.factory.summary": "#9630 ready for human merge",
      "omnigent.factory.health": "ok",
    }),
    member(
      LIVE_RUN_PL_DB_ID,
      "PL · db-version",
      {
        "omnigent.factory.role": "project_lead",
        "omnigent.factory.project": "db-version",
        "omnigent.factory.parent": LIVE_RUN_ATLAS_ID,
        "omnigent.factory.summary": "Waiting on D-2 · IC continues on the route",
        "omnigent.factory.health": "blocked",
      },
      true,
    ),
  ],
};

function panelStory(rootId: string, theme: "light" | "dark" = "light"): Story {
  const root = rootId === LIVE_RUN_ATLAS_ID ? atlasSession() : plSession();
  return {
    args: { conversationId: rootId, rootSessionId: rootId },
    render: (args) => (
      <FactoryStoryFrame
        theme={theme}
        route={`/c/${rootId}`}
        sessions={[
          { ...root, labels: { ...root.labels, "omnigent.wrapper": "claude-code-native-ui" } },
        ]}
        seed={(client) => {
          for (const [id, children] of Object.entries(TREE)) {
            client.setQueryData(childSessionsQueryKey(id), children);
          }
          for (const id of [
            LIVE_RUN_PL_API_ID,
            LIVE_RUN_PL_DB_ID,
            "713c96e676515bd992f5c15015101a2e",
          ]) {
            client.setQueryData(childSessionsQueryKey(id), []);
          }
        }}
      >
        <div className="h-[420px] w-[320px] overflow-hidden rounded-lg border bg-card">
          <SubagentsPanel {...args} />
        </div>
      </FactoryStoryFrame>
    ),
  };
}

const meta = {
  title: "Factory/Agents panel root",
  component: SubagentsPanel,
} satisfies Meta<typeof SubagentsPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Atlas's tree: the root row reads "Atlas · Lead", not the harness name. */
export const AtlasRoot = panelStory(LIVE_RUN_ATLAS_ID);

export const AtlasRootDark = panelStory(LIVE_RUN_ATLAS_ID, "dark");
