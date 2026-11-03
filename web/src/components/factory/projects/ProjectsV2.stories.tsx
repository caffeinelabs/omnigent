import type { Meta, StoryObj } from "@storybook/react-vite";
import { CockpitStory } from "../board/boardStoryKit";
import {
  type ProjectV2Options,
  projectV2Routes,
  projectsV2Snapshot,
} from "../project/__fixtures__/projectV2Fixtures";
import { PHONE } from "../ui/uiStoryFixtures";

interface PageOptions {
  search?: string;
  snapshot?: ProjectV2Options;
  theme?: "light" | "dark";
  phone?: boolean;
}

/** (sample) The new cockpit's Projects page. */
function page(options: PageOptions = {}): Story {
  const snapshot = projectsV2Snapshot(options.snapshot);
  const story: Story = {
    render: () => (
      <CockpitStory
        snapshot={snapshot}
        theme={options.theme}
        path="/projects"
        search={`?cockpit=v2${options.search ?? ""}`}
        routes={projectV2Routes(snapshot)}
      />
    ),
  };
  return options.phone ? { ...story, ...PHONE } : story;
}

const meta = {
  title: "Factory/Projects (new cockpit)",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Outcome-first cards: why each exists, its stage, the lead's update, its team and spend. */
export const Cards = page({ snapshot: { question: true } });
export const CardsDark = page({ snapshot: { question: true }, theme: "dark" });
export const CardsMobile = page({ snapshot: { question: true }, phone: true });

/** The board by stage; shipped work says what its readout will say. */
export const Board = page({ search: "&view=board" });
export const BoardDark = page({ search: "&view=board", theme: "dark" });
export const BoardMobile = page({ search: "&view=board", phone: true });

/** Ready for merge piles up: "Bottleneck: your merge". */
export const BoardBottleneck = page({ search: "&view=board", snapshot: { more: 10 } });

/** Filtered to what needs you (a merge and an open question). */
export const NeedsYou = page({ search: "&f=needs", snapshot: { question: true } });

/** Filtered to work without user evidence: requested and agents' ideas. */
export const Requested = page({ search: "&f=requested" });

/** A filter that matches nothing. */
export const NothingMatches = page({
  search: "&f=stopped",
  snapshot: { only: ["dark-mode", "bulk-archive"] },
});

/** Fifteen projects. */
export const Busy = page({ snapshot: { more: 10 } });
export const BusyMobile = page({ snapshot: { more: 10 }, phone: true });
