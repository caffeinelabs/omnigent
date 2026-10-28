import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import { ImprovementsPage } from "@/pages/factory/ImprovementsPage";
import type { ViewerRole } from "@/lib/factoryApi";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import { round3Events, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { type LearnRouteOptions, learnRoutes } from "../insights/__fixtures__/learnFixtures";

const FID = round3Snapshot().factory.id;

function page(
  path: string,
  options: CockpitStoryOptions & { role?: ViewerRole; learn?: LearnRouteOptions } = {},
  play?: Story["play"],
): Story {
  const { role = "creator", learn, routes, ...rest } = options;
  return {
    render: () => (
      <CockpitStory
        snapshot={round3Snapshot(role)}
        events={round3Events()}
        path={path}
        routes={{ ...learnRoutes(FID, learn), ...routes }}
        {...rest}
      />
    ),
    play,
  };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

function click(testId: string): Story["play"] {
  return async ({ canvasElement }) => {
    await userEvent.click(await within(canvasElement.ownerDocument.body).findByTestId(testId));
  };
}

const meta = {
  title: "Factory/Improvements",
  component: ImprovementsPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof ImprovementsPage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** C-14 on trial (31 h of 48): progress, guards ✓, target, Keep now and Revert. */
export const OnTrial = page("/improvements");

export const OnTrialDark = page("/improvements", { theme: "dark" });

export const OnTrialMobile = phone(page("/improvements"));

export const OnTrialMobileDark = phone(page("/improvements", { theme: "dark" }));

/** The scorecard opened: baseline vs trial for the target and every guard. */
export const OnTrialScorecard = page("/improvements", {}, async ({ canvasElement }) => {
  const body = within(canvasElement.ownerDocument.body);
  await userEvent.click(await body.findByText("Scorecard"));
});

/** Waiting: C-15 rules (owners: Approve trial, Edit, Reject), C-16 code (Tier 2 off), C-18 co-sign. */
export const Proposed = page("/improvements", { search: "?tab=proposed" });

export const ProposedDark = page("/improvements", { search: "?tab=proposed", theme: "dark" });

export const ProposedMobile = phone(page("/improvements", { search: "?tab=proposed" }));

/** Ops (viewer): every lever disabled with "Owners can apply tuning and rules". */
export const ProposedViewer = page("/improvements", { search: "?tab=proposed", role: "viewer" });

/** Mia (operator): owners decide Tier-1 changes, so the levers stay disabled for her too. */
export const ProposedOperator = page("/improvements", {
  search: "?tab=proposed",
  role: "operator",
});

/** Edit C-15 before approving: the change dialog prefilled, applied as a trial. */
export const EditProposal = page(
  "/improvements",
  { search: "?tab=proposed" },
  click("proposal-C-15-edit"),
);

/** Tier 2 on: the code change can be approved like any other. */
export const ProposedTier2On = page("/improvements", {
  search: "?tab=proposed",
  learn: { tier2On: true },
});

/** Every C-n, newest first: kept, reverted, applied, pending, projects. */
export const History = page("/improvements", { search: "?tab=history" });

export const HistoryMobile = phone(page("/improvements", { search: "?tab=history" }));

/** Luka's Settings edit (loops.implement.attempts 3 → 4) as C-17 on top of History. */
export const HistoryAfterSave = page("/improvements", {
  search: "?tab=history",
  learn: { afterSave: true },
});

/** 48 changes in a 60-agent factory. */
export const HistoryScale = page("/improvements", {
  search: "?tab=history",
  learn: { scale: true },
});

export const Rules = page("/improvements", { search: "?tab=rules" });

export const RulesDark = page("/improvements", { search: "?tab=rules", theme: "dark" });

/** v7 in force, the diff from v6, one member on an older bundle → Relaunch. */
export const Definition = page("/improvements", { search: "?tab=definition" });

export const DefinitionDark = page("/improvements", { search: "?tab=definition", theme: "dark" });

export const DefinitionMobile = phone(page("/improvements", { search: "?tab=definition" }));

export const DefinitionNoDrift = page("/improvements", {
  search: "?tab=definition",
  learn: { bundlesDrift: false },
});

export const DefinitionViewer = page("/improvements", {
  search: "?tab=definition",
  role: "viewer",
});

/** Findings with "Is this right?" and Propose change; Sentinel's retro below. */
export const Findings = page("/improvements", { search: "?tab=findings" });

export const FindingsMobile = phone(page("/improvements", { search: "?tab=findings" }));

export const FindingsCalibrating = page(
  "/improvements",
  { search: "?tab=findings" },
  click("calibrate-F-7-no"),
);

/** Propose change from F-7 as an operator: it goes to the owners. */
export const ProposeChangeOperator = page(
  "/improvements",
  { search: "?tab=findings", role: "operator" },
  click("finding-propose-F-7"),
);

export const Empty = page("/improvements", {
  learn: { proposals: { body: { data: [] } } },
});

export const Loading = page("/improvements", { learn: { proposals: "pending" } });

export const LoadError = page("/improvements", {
  learn: { proposals: { error: 503, message: "unavailable" } },
});

// ── A change's page ────────────────────────────────────────────

export const ProposalTrial = page("/improvements/C-14");

export const ProposalTrialDark = page("/improvements/C-14", { theme: "dark" });

export const ProposalTrialMobile = phone(page("/improvements/C-14"));

/** C-12: a guard regressed, reverted automatically. */
export const ProposalReverted = page("/improvements/C-12");

/** C-16: a code change recorded while Tier 2 is off; Copy as issue. */
export const ProposalCode = page("/improvements/C-16");

/** C-4: a projects proposal shows the Room's card (Apply, Edit & apply, Reject). */
export const ProposalProjects = page("/improvements/C-4");

export const ProposalViewer = page("/improvements/C-15", { role: "viewer" });

export const ProposalMissing = page("/improvements/C-99", {
  routes: {
    [`/v1/factory/factories/${FID}/proposals/C-99`]: { error: 404, message: "no proposal" },
  },
});
