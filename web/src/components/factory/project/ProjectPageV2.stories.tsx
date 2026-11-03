import type { Meta, StoryObj } from "@storybook/react-vite";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { CockpitStory } from "../board/boardStoryKit";
import { PHONE } from "../ui/uiStoryFixtures";
import {
  EVIDENCE_SLUG,
  IDEA_SLUG,
  type ProjectV2Options,
  type ProjectV2RouteOptions,
  REQUESTED_SLUG,
  STOPPED_SLUG,
  charterVersions,
  projectV2Routes,
  projectsV2Snapshot,
} from "./__fixtures__/projectV2Fixtures";

interface PageOptions {
  slug?: string;
  tab?: "" | "/work" | "/history" | "/charter";
  snapshot?: ProjectV2Options;
  routes?: ProjectV2RouteOptions;
  theme?: "light" | "dark";
  phone?: boolean;
  search?: string;
}

/** (sample) A project page of the new cockpit. */
function page(options: PageOptions = {}): Story {
  const snapshot: FactorySnapshot = projectsV2Snapshot(options.snapshot);
  const story: Story = {
    render: () => (
      <CockpitStory
        snapshot={snapshot}
        theme={options.theme}
        path={`/projects/${options.slug ?? EVIDENCE_SLUG}${options.tab ?? ""}`}
        search={`?cockpit=v2${options.search ?? ""}`}
        routes={projectV2Routes(snapshot, { discovery: true, ...options.routes })}
      />
    ),
  };
  return options.phone ? { ...story, ...PHONE } : story;
}

const meta = {
  title: "Factory/Project page (new cockpit)",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

// ── Why it exists ──────────────────────────────────────────────

/** From evidence: O-3's accounts and sources, the bet's range, the readout days; the facts override "on track". */
export const FromEvidence = page();
export const FromEvidenceDark = page({ theme: "dark" });
export const FromEvidenceMobile = page({ phone: true });

/** Requested by a person: the why-chain hatched from evidence to bet; ready for your merge. */
export const Requested = page({ slug: REQUESTED_SLUG, snapshot: { question: true } });
export const RequestedDark = page({
  slug: REQUESTED_SLUG,
  snapshot: { question: true },
  theme: "dark",
});
export const RequestedMobile = page({
  slug: REQUESTED_SLUG,
  snapshot: { question: true },
  phone: true,
});

/** An agent's idea, counted as a hunch: no user evidence, not measured, no team yet. */
export const AgentIdea = page({ slug: IDEA_SLUG });
export const AgentIdeaDark = page({ slug: IDEA_SLUG, theme: "dark" });
export const AgentIdeaMobile = page({ slug: IDEA_SLUG, phone: true });

/** Stopped: the red card with Close project and the lead's next step. */
export const Stopped = page({ slug: STOPPED_SLUG });
export const StoppedDark = page({ slug: STOPPED_SLUG, theme: "dark" });
export const StoppedMobile = page({ slug: STOPPED_SLUG, phone: true });

/** The lead's update is four hours old: the chip greys and the facts speak. */
export const StaleUpdate = page({ snapshot: { staleUpdate: true } });

/** Discovery off: the chain still names O-3, without the ledger's evidence. */
export const DiscoveryOff = page({ routes: { discovery: false } });

// ── The tabs ───────────────────────────────────────────────────

/** Work: tasks, pull requests, demos, previews, loops, and the views in Debug. */
export const Work = page({ tab: "/work" });
export const WorkDark = page({ tab: "/work", theme: "dark" });
export const WorkMobile = page({ tab: "/work", phone: true });

/** History: team versions with Restore, charter changes, recent activity. */
export const History = page({ tab: "/history" });
export const HistoryDark = page({ tab: "/history", theme: "dark" });
export const HistoryMobile = page({ tab: "/history", phone: true });

/** Charter: the charter with the profile's merge policy and flags; versions with Revert. */
export const Charter = page({ tab: "/charter" });
export const CharterDark = page({ tab: "/charter", theme: "dark" });
export const CharterMobile = page({ tab: "/charter", phone: true });

/** Two minutes after an edit: Undo stays on the version for the rest of 10 minutes. */
export const CharterJustEdited = page({
  tab: "/charter",
  routes: { versions: charterVersions(projectsV2Snapshot().server_time, { justEdited: true }) },
});

/** An older server without charter versions: the tab says so and still edits. */
export const CharterNoHistory = page({ tab: "/charter", routes: { versions: "missing" } });

/** A viewer reads everything; the levers are hidden with one line. */
export const CharterViewer = page({ tab: "/charter", snapshot: { viewer: "viewer" } });
