import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import type { DefinitionPreviewResponse, ProposalView, ViewerRole } from "@/lib/factoryApi";
import { SteerPage } from "@/pages/factory/SteerPage";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import { homeRoutes } from "../home/__fixtures__/homeFixtures";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { teamCatalog } from "../team/__fixtures__/teamFixtures";
import {
  SAMPLE_SOURCES,
  STEER_VERSION,
  type SteerRouteOptions,
  goalSuggestion,
  previewBudgetLower,
  previewFocus,
  previewOperator,
  previewRestore,
  previewRubric,
  previewStale,
  previewWith,
  steerDefinition,
  steerProposals,
  steerRoutes,
} from "./__fixtures__/steerFixtures";
import { stageStarter } from "./discoveryStarter";
import {
  EMPTY_STEER,
  type SteerStaging,
  addItemEdit,
  replaceEdit,
  stageEdit,
  stageProposal,
  stageRemoveItem,
  stageRestore,
  trialRule,
  withBase,
} from "./steerModel";
import { resetSteerStaging, writeSteerStaging } from "./steerStaging";

// Steer in the new cockpit over the round-3 sample factory (every product
// word is the sample's own data): each section with nothing staged and with
// a change staged, the rubric with a running trial (its field locked, a
// second trial refused), sources with one waiting for an owner, the add-source
// sheet's four steps, Scout's brief diff from an agent's proposal, versions
// with an undone one and a staged restore, Undo of the version in force, a
// change staged on a version no longer in force, an operator's raise that
// waits for an owner and their lower budget that applies at once, a viewer,
// discovery off, empty sections, the environment profile, light, and a 390 px
// phone. A story's staging is staged on v12 unless it says otherwise.

interface SteerStoryOptions extends SteerRouteOptions {
  role?: ViewerRole;
  staging?: () => SteerStaging;
  preview?: DefinitionPreviewResponse;
  proposals?: ProposalView[];
  frame?: CockpitStoryOptions;
  /** No team specs on this server (the catalog answers 404). */
  noCatalog?: boolean;
}

/** The server's preview of what a story stages: its diff and the trial verdict for its paths. */
function autoPreview(staging: SteerStaging, operator = false): DefinitionPreviewResponse {
  const entries = staging.edits.map((e) => ({
    path: e.change.path,
    before: e.before,
    after: e.after,
  }));
  const rule = trialRule(
    entries.map((e) => e.path),
    steerDefinition(),
  );
  const preview = previewWith(
    entries,
    rule.ok
      ? { ok: true, trial_h: 48, blocks: [] }
      : {
          ok: false,
          code: rule.code === "trial_overlap" ? "trial_overlap" : "not_trialable",
          reason: rule.text,
          trial: rule.trial,
        },
  );
  return operator
    ? { ...preview, who: previewOperator().who, you_can_apply: false, you_can_propose: true }
    : preview;
}

function steer(path: string, options: SteerStoryOptions = {}): Story {
  const snapshot = round3Snapshot(options.role ?? "creator");
  const preview =
    options.preview ??
    (options.staging ? autoPreview(options.staging(), options.role === "operator") : undefined);
  const fid = snapshot.factory.id;
  const own = `/v1/factory/factories/${fid}`;
  return {
    beforeEach: () => {
      resetSteerStaging();
      if (options.staging) {
        writeSteerStaging(fid, withBase(options.staging(), STEER_VERSION));
      }
      return () => resetSteerStaging();
    },
    render: () => (
      <CockpitStory
        snapshot={snapshot}
        path={path.split("?")[0]}
        search={path.includes("?") ? `?${path.split("?")[1]}` : ""}
        theme="dark"
        routes={{
          ...homeRoutes(snapshot),
          ...steerRoutes(fid, { ...options, preview }),
          ...(options.noCatalog ? { [`${own}/catalog`]: { error: 404 } } : {}),
        }}
        {...options.frame}
      />
    ),
  };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

const light: SteerStoryOptions = { frame: { theme: "light" } };

const doc = () => steerDefinition().effective;
const focusStaged = () =>
  stageEdit(
    EMPTY_STEER,
    replaceEdit(
      "strategy",
      "strategy.focus_segments",
      "Focus segments",
      ["new-users"],
      ["new-users", "active-builders"],
      "Changes fit for every opportunity in these segments. Coverage gaps are reported for them.",
    ),
  );
const rubricStaged = () =>
  stageEdit(
    EMPTY_STEER,
    replaceEdit(
      "rubric",
      "discovery.rubric.off_focus_factor",
      "Fit outside the focus segments",
      0.5,
      0.8,
      "Re-scores every opportunity. See the re-rank in the right column.",
    ),
  );

const meta = {
  title: "Factory/Cockpit v2/Steer",
  component: SteerPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof SteerPage>;

export default meta;
type Story = StoryObj<typeof meta>;

// ── Overview ───────────────────────────────────────────────────

/** How this factory works: the five stages with their leads, what waits, recent versions. */
export const Overview = steer("/steer/overview");

export const OverviewLight = steer("/steer/overview", { frame: { theme: "light" } });

/** Discovery off: Sense and Make sense become "Turn on discovery" with the starter. */
export const OverviewDiscoveryOff = steer("/steer/overview", { discovery: false });

/** The starter staged: what Scout reads and costs, applied as one version. */
export const OverviewStarterStaged = steer("/steer/overview", {
  discovery: false,
  staging: () => stageStarter(EMPTY_STEER, steerDefinition({ discovery: false }).effective),
});

// ── Strategy ───────────────────────────────────────────────────

export const Strategy = steer("/steer/strategy");

/** Staged on v12, but v13 (someone else's) came into force since: Apply waits for Rebase. */
export const StrategyStagedOnOlderVersion = steer("/steer/strategy", {
  moved: true,
  staging: focusStaged,
  preview: previewStale(),
});

/** J7: "Active builders" toggled into focus, with the re-rank before Apply. */
export const StrategyFocusStaged = steer("/steer/strategy", {
  staging: focusStaged,
  preview: previewFocus(),
});

export const StrategyFocusStagedLight = steer("/steer/strategy", {
  staging: focusStaged,
  preview: previewFocus(),
  frame: { theme: "light" },
});

/** No goal yet, with Scout's suggestion to stage. */
export const StrategyNoGoal = steer("/steer/strategy", {
  noGoal: true,
  proposals: [goalSuggestion()],
});

export const StrategyFocusStagedMobile = phone(
  steer("/steer/strategy", { staging: focusStaged, preview: previewFocus() }),
);

// ── Product profile ────────────────────────────────────────────

/** With Scout's draft waiting (C-32 adds a segment). */
export const ProductProfile = steer("/steer/product");

export const ProductProfileStaged = steer("/steer/product", {
  staging: () =>
    stageEdit(
      EMPTY_STEER,
      replaceEdit(
        "product",
        "product.delivery.merge_policy",
        "Merge policy",
        doc().product!.delivery.merge_policy,
        "Two people approve, then one merges (sample).",
      ),
    ),
});

/** A factory with an empty profile. */
export const ProductProfileEmpty = steer("/steer/product", {
  discovery: false,
  proposals: [],
});

/** The environment profile, scrolled into view, with a protected branch staged. */
function environment(options: SteerStoryOptions = {}): Story {
  const story = steer("/steer/product", {
    proposals: [],
    staging: () =>
      stageEdit(
        EMPTY_STEER,
        replaceEdit(
          "product",
          "environment.protected_branches",
          "Protected branches",
          doc().environment!.protected_branches,
          ["develop", "release/*"],
        ),
      ),
    ...options,
  });
  return {
    ...story,
    play: async ({ canvasElement }) => {
      const body = within(canvasElement.ownerDocument.body);
      (await body.findByTestId("steer-environment")).scrollIntoView({ block: "start" });
    },
  };
}

export const ProductProfileEnvironment = environment();

export const ProductProfileEnvironmentLight = environment(light);

export const ProductProfileEnvironmentMobile = phone(environment());

// ── Sources ────────────────────────────────────────────────────

/** Four sources (one on trial) and one Scout proposed, waiting for an owner. */
export const Sources = steer("/steer/sources");

export const SourcesMobile = phone(steer("/steer/sources"));

/** Discovery runs, but no source is connected yet. */
export const SourcesEmpty = steer("/steer/sources", { noSources: true, proposals: [] });

/** Removing the forum staged. */
export const SourcesStaged = steer("/steer/sources", {
  staging: () =>
    stageRemoveItem(
      EMPTY_STEER,
      "sources",
      "discovery.sources",
      "forum",
      "Source: Public community forum (sample)",
      SAMPLE_SOURCES[0],
    ),
});

function addSource(steps: number, options: SteerStoryOptions = {}): Story {
  return {
    ...steer("/steer/sources", options),
    play: async ({ canvasElement }) => {
      const body = within(canvasElement.ownerDocument.body);
      await userEvent.click(await body.findByTestId("steer-add-source-open"));
      for (let i = 1; i < steps; i += 1) {
        // Each step shows only after the last one: one click at a time.
        // oxlint-disable-next-line no-await-in-loop
        await userEvent.click(await body.findByTestId("steer-add-source-next"));
      }
    },
  };
}

export const AddSourceStep1 = addSource(1);
export const AddSourceStep2 = addSource(2);
export const AddSourceStep3 = addSource(3);
export const AddSourceStep4 = addSource(4);
export const AddSourceStep4Light = addSource(4, light);

// ── Rubric ─────────────────────────────────────────────────────

/** C-33 trials the discovery block: its field is locked, and a second trial is refused. */
export const RubricTrial = steer("/steer/rubric", {
  staging: rubricStaged,
  preview: previewRubric(),
});

export const RubricTrialLight = steer("/steer/rubric", {
  staging: rubricStaged,
  preview: previewRubric(),
  ...light,
});

export const RubricTrialMobile = phone(
  steer("/steer/rubric", { staging: rubricStaged, preview: previewRubric() }),
);

// ── Roles and briefs ───────────────────────────────────────────

export const RolesBuilder = steer("/steer/roles?role=ic");

/** A factory that never set a brief or a rule, on a server without team specs. */
export const RolesEmpty = steer("/steer/roles?role=ic", {
  bare: true,
  noCatalog: true,
  discovery: false,
  noTrial: true,
  proposals: [],
});

/** Sentinel's C-35 staged: Scout's brief, before → after. */
export const RolesScoutBriefDiff = steer("/steer/roles?role=scout", {
  staging: () => stageProposal(steerProposals()[1]!, doc()),
});

// ── Templates and gates ────────────────────────────────────────

export const Templates = steer("/steer/templates");

export const TemplatesStaged = steer("/steer/templates", {
  staging: () => {
    const standard = teamCatalog().templates[0]!;
    const roles = standard.roles.map((r) => (r.role === "ic" ? { ...r, count: r.count + 1 } : r));
    return stageEdit(
      EMPTY_STEER,
      replaceEdit(
        "templates",
        `team.templates.${standard.id}.roles`,
        `Template ${standard.label}: members`,
        standard.roles,
        roles,
      ),
    );
  },
});

/** No team specs on this server yet. */
export const TemplatesNone = steer("/steer/templates", { noCatalog: true });

export const Gates = steer("/steer/gates");

export const GatesMobile = phone(steer("/steer/gates"));

// ── Guardrails ─────────────────────────────────────────────────

/** The factory's daily budget staged at `usd` (it is $400 a day in force). */
const dayBudgetStaged = (usd: number) => () =>
  stageEdit(
    EMPTY_STEER,
    replaceEdit(
      "budgets",
      "budgets.factory_usd_per_day",
      "Factory per day",
      (doc().budgets as { factory_usd_per_day: number }).factory_usd_per_day,
      usd,
    ),
  );

export const Budgets = steer("/steer/budgets");

/** An owner lowers the daily budget: class A, it applies at once. */
export const BudgetsStaged = steer("/steer/budgets", {
  staging: dayBudgetStaged(300),
  preview: previewBudgetLower(),
});

export const BudgetsStagedLight = steer("/steer/budgets", {
  ...light,
  staging: dayBudgetStaged(300),
  preview: previewBudgetLower(),
});

/** An operator's raise waits for an owner: Propose to owners. */
export const BudgetsOperator = steer("/steer/budgets", {
  role: "operator",
  staging: dayBudgetStaged(500),
});

/** An operator lowers the daily budget: it only reduces spend, so their Apply applies it. */
export const BudgetsOperatorLowers = steer("/steer/budgets", {
  role: "operator",
  staging: dayBudgetStaged(300),
  preview: previewBudgetLower("editor"),
});

export const BudgetsOperatorLowersLight = steer("/steer/budgets", {
  ...light,
  role: "operator",
  staging: dayBudgetStaged(300),
  preview: previewBudgetLower("editor"),
});

export const BudgetsOperatorLowersMobile = phone(
  steer("/steer/budgets", {
    role: "operator",
    staging: dayBudgetStaged(300),
    preview: previewBudgetLower("editor"),
  }),
);

export const AlertsAndRules = steer("/steer/alerts");

export const AlertsAndRulesStaged = steer("/steer/alerts", {
  staging: () =>
    stageEdit(
      EMPTY_STEER,
      addItemEdit(
        EMPTY_STEER,
        "alerts",
        "rules",
        "New rule for IC",
        {
          role: "ic",
          project: "*",
          text: "Run the package's unit tests before every push (sample).",
        },
        "Members in the role get a RULES UPDATE.",
      ),
    ),
});

/** No rules yet. */
export const AlertsEmpty = steer("/steer/alerts", { bare: true });

export const Cadence = steer("/steer/cadence");

export const CadenceStaged = steer("/steer/cadence", {
  staging: () =>
    stageEdit(
      EMPTY_STEER,
      replaceEdit(
        "cadence",
        "previews.max",
        "Live previews",
        (doc().previews as { max?: number } | undefined)?.max ?? null,
        2,
      ),
    ),
});

// ── Versions ───────────────────────────────────────────────────

/** v11 undone, v10 restored v8, C-33 on trial. */
export const Versions = steer("/steer/versions");

/** v13, applied by the viewer two minutes ago: Undo stays for 10 minutes, here and in the column. */
export const VersionsUndo = steer("/steer/versions", { undoable: true });

/** Restore v10 staged: its diff, and the trial it ends. */
export const VersionsRestoreStaged = steer("/steer/versions", {
  staging: () => stageRestore(10),
  preview: previewRestore(),
});

export const VersionsRestoreStagedMobile = phone(
  steer("/steer/versions", { staging: () => stageRestore(10), preview: previewRestore() }),
);

// ── People ─────────────────────────────────────────────────────

/** A viewer reads everything and pulls no lever. */
export const Viewer = steer("/steer/strategy", { role: "viewer" });

export const OverviewMobile = phone(steer("/steer/overview"));

export const OverviewMobileLight = phone(steer("/steer/overview", { frame: { theme: "light" } }));
