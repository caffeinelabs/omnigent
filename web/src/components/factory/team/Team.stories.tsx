import type { Meta, StoryObj } from "@storybook/react-vite";
import { type ReactNode, useState } from "react";
import { CockpitStory } from "../board/boardStoryKit";
import {
  TEAM_SLUG,
  type TeamShape,
  previewBlockedRemoval,
  previewNewMember,
  previewOneChange,
  previewOperator,
  previewTemplateSwitch,
  previewThreeChanges,
  teamRoutes,
  teamSnapshot,
} from "./__fixtures__/teamFixtures";
import type { StagedTeamChange, TeamStaging } from "./teamModel";
import { resetTeamStaging, writeTeamStaging } from "./teamStaging";
import type { DefinitionPreviewResponse, ViewerRole } from "@/lib/factoryApi";

interface TeamStoryOptions {
  page: "view" | "edit";
  shape?: TeamShape;
  mode?: "warn" | "enforce";
  viewer?: ViewerRole;
  staged?: StagedTeamChange[];
  preview?: DefinitionPreviewResponse;
  justApplied?: boolean;
  peek?: string;
  theme?: "light" | "dark";
  phone?: boolean;
}

/** Puts `staging` in the store before the page mounts (each story starts clean). */
function Seeded({
  factoryId,
  staging,
  children,
}: {
  factoryId: string;
  staging: TeamStaging | null;
  children: ReactNode;
}) {
  useState(() => {
    resetTeamStaging();
    if (staging) writeTeamStaging(factoryId, TEAM_SLUG, staging);
    return null;
  });
  return children;
}

/** The sample project's page (or its team editor) in the new cockpit. */
function team(options: TeamStoryOptions): Story {
  const snapshot = teamSnapshot(options.shape, { mode: options.mode, viewer: options.viewer });
  const search = new URLSearchParams({ cockpit: "v2" });
  if (options.peek) search.set("peek", options.peek);
  const staging = options.staged
    ? { changes: options.staged, when: "after_current" as const, scope: "project" as const }
    : null;
  const story: Story = {
    render: () => (
      <Seeded factoryId={snapshot.factory.id} staging={staging}>
        <CockpitStory
          snapshot={snapshot}
          theme={options.theme}
          path={`/projects/${TEAM_SLUG}${options.page === "edit" ? "/team" : ""}`}
          search={`?${search.toString()}`}
          routes={teamRoutes(snapshot, {
            shape: options.shape,
            mode: options.mode,
            preview: options.preview,
            justApplied: options.justApplied,
          })}
        />
      </Seeded>
    ),
  };
  return options.phone ? { ...story, globals: { viewport: { value: "mobile2" } } } : story;
}

const meta = {
  title: "Factory/Team chart and editor",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const REX: StagedTeamChange = { kind: "model", member: "M-3", vendor: "claude", model: null };
const REVIEWER: StagedTeamChange = {
  kind: "add",
  key: "new-1",
  role: "reviewer",
  vendor: "claude",
  model: "opus",
};
const BUDGET: StagedTeamChange = { kind: "budget", value: 80 };

// ── The team on the project page ───────────────────────────────

/** The standard team: the lead on top, then Build, Check and Run, each member's model, state and spend. */
export const ViewStandard = team({ page: "view" });
export const ViewStandardDark = team({ page: "view", theme: "dark" });
export const ViewStandardMobile = team({ page: "view", phone: true });

/** A pair: two Claude builders side by side. */
export const ViewPair = team({ page: "view", shape: "pair" });
export const ViewPairDark = team({ page: "view", shape: "pair", theme: "dark" });
export const ViewPairMobile = team({ page: "view", shape: "pair", phone: true });

/** An investigation: no env and no demo, so no Run group. */
export const ViewInvestigation = team({ page: "view", shape: "investigation" });
export const ViewInvestigationDark = team({
  page: "view",
  shape: "investigation",
  theme: "dark",
});
export const ViewInvestigationMobile = team({
  page: "view",
  shape: "investigation",
  phone: true,
});

/** Two minutes after Apply: Undo stays in the header for the rest of the 10 minutes. */
export const ViewJustApplied = team({ page: "view", justApplied: true });
export const ViewJustAppliedDark = team({ page: "view", justApplied: true, theme: "dark" });
export const ViewJustAppliedMobile = team({ page: "view", justApplied: true, phone: true });

/** History: every team change is a definition version, with Restore. */
export const History = team({ page: "view", peek: "team-history" });
export const HistoryDark = team({ page: "view", peek: "team-history", theme: "dark" });
export const HistoryMobile = team({ page: "view", peek: "team-history", phone: true });

// ── The editor ─────────────────────────────────────────────────

/** Nothing staged: locks say which gate needs whom. */
export const EditNothingStaged = team({ page: "edit" });
export const EditNothingStagedDark = team({ page: "edit", theme: "dark" });
export const EditNothingStagedMobile = team({ page: "edit", phone: true });

/** One change: Rex moves from Codex to Claude. */
export const EditOneChange = team({ page: "edit", staged: [REX], preview: previewOneChange() });
export const EditOneChangeDark = team({
  page: "edit",
  staged: [REX],
  preview: previewOneChange(),
  theme: "dark",
});
export const EditOneChangeMobile = team({
  page: "edit",
  staged: [REX],
  preview: previewOneChange(),
  phone: true,
});

/** Three changes: Rex to Claude, a second reviewer, a higher budget. */
export const EditThreeChanges = team({
  page: "edit",
  staged: [REX, REVIEWER, BUDGET],
  preview: previewThreeChanges(),
});
export const EditThreeChangesDark = team({
  page: "edit",
  staged: [REX, REVIEWER, BUDGET],
  preview: previewThreeChanges(),
  theme: "dark",
});
export const EditThreeChangesMobile = team({
  page: "edit",
  staged: [REX, REVIEWER, BUDGET],
  preview: previewThreeChanges(),
  phone: true,
});

/** Removing the only env keeper (staged from the palette): Tier 0 refuses it. */
export const EditBlockedRemoval = team({
  page: "edit",
  staged: [{ kind: "remove", member: "M-5" }],
  preview: previewBlockedRemoval(),
});
export const EditBlockedRemovalDark = team({
  page: "edit",
  staged: [{ kind: "remove", member: "M-5" }],
  preview: previewBlockedRemoval(),
  theme: "dark",
});
export const EditBlockedRemovalMobile = team({
  page: "edit",
  staged: [{ kind: "remove", member: "M-5" }],
  preview: previewBlockedRemoval(),
  phone: true,
});

/** A new member: a second reviewer, which unlocks removing the first. */
export const EditNewMember = team({
  page: "edit",
  staged: [REVIEWER],
  preview: previewNewMember(),
});
export const EditNewMemberDark = team({
  page: "edit",
  staged: [REVIEWER],
  preview: previewNewMember(),
  theme: "dark",
});
export const EditNewMemberMobile = team({
  page: "edit",
  staged: [REVIEWER],
  preview: previewNewMember(),
  phone: true,
});

/** Switching to the pair template: Rex goes, a second Claude builder comes. */
export const EditTemplateSwitch = team({
  page: "edit",
  staged: [{ kind: "template", value: "pair" }],
  preview: previewTemplateSwitch(),
});
export const EditTemplateSwitchDark = team({
  page: "edit",
  staged: [{ kind: "template", value: "pair" }],
  preview: previewTemplateSwitch(),
  theme: "dark",
});
export const EditTemplateSwitchMobile = team({
  page: "edit",
  staged: [{ kind: "template", value: "pair" }],
  preview: previewTemplateSwitch(),
  phone: true,
});

/** An operator: a model change is an owner change, so it is proposed, not applied. */
export const EditOperator = team({
  page: "edit",
  viewer: "operator",
  staged: [REX],
  preview: previewOperator(),
});
export const EditOperatorDark = team({
  page: "edit",
  viewer: "operator",
  staged: [REX],
  preview: previewOperator(),
  theme: "dark",
});
export const EditOperatorMobile = team({
  page: "edit",
  viewer: "operator",
  staged: [REX],
  preview: previewOperator(),
  phone: true,
});

/** The server only records dispatches outside a team: preview only, no Apply. */
export const EditReadOnly = team({
  page: "edit",
  mode: "warn",
  staged: [REX],
  preview: previewOneChange(),
});
export const EditReadOnlyDark = team({
  page: "edit",
  mode: "warn",
  staged: [REX],
  preview: previewOneChange(),
  theme: "dark",
});
export const EditReadOnlyMobile = team({
  page: "edit",
  mode: "warn",
  staged: [REX],
  preview: previewOneChange(),
  phone: true,
});
