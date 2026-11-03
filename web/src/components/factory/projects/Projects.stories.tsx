import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import { ProjectsPage } from "@/pages/factory/ProjectsPage";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { mockScreenshot, withStoryMedia } from "../board/__fixtures__/boardFixtures";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import { startStorySnapshot } from "./__fixtures__/startFixtures";
import {
  emptyFactorySnapshot,
  round3Events,
  round3Snapshot,
  scaleSnapshot,
} from "../shell/__fixtures__/shellFixtures";

function projects(options: CockpitStoryOptions = {}, play?: Story["play"]): Story {
  return {
    render: () => (
      <CockpitStory
        path="/projects"
        snapshot={round3Snapshot()}
        events={round3Events()}
        {...options}
      />
    ),
    play,
  };
}

/** Demo stills inline (stories have no demo file server). */
function withStills(snapshot: FactorySnapshot): FactorySnapshot {
  withStoryMedia(snapshot);
  for (const pr of snapshot.prs) {
    for (const file of pr.demo?.files ?? []) {
      if (file.content_type.startsWith("image/") && !file.url.startsWith("data:")) {
        file.url = mockScreenshot("Sample App", "A sample landing page");
      }
    }
  }
  return snapshot;
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

/** A 390 px phone (the default presets jump from 320 to 414). */
function phone390(story: Story): Story {
  return {
    ...story,
    parameters: {
      viewport: {
        options: {
          phone390: {
            name: "Phone 390",
            styles: { width: "390px", height: "844px" },
            type: "mobile",
          },
        },
      },
    },
    globals: { viewport: { value: "phone390" } },
  };
}

const meta = {
  title: "Factory/Projects table",
  component: ProjectsPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof ProjectsPage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Seven projects: the table by default, then Environments (base-env) and Closed (1). */
export const Table = projects();

export const TableDark = projects({ theme: "dark" });

export const TableMobile = phone(projects());

export const TableMobileDark = phone(projects({ theme: "dark" }));

/**
 * Atlas just started four projects: two PLs coming up ("Starting PL · 2m ·
 * Provisioning sandbox"), one session being created, two waiting for a pod
 * ("Queued · 1 project ahead"); validate-build-tier reads Verify, not Merged,
 * while its PL validates the merged !9774.
 */
export const StartingAndQueued = projects({ snapshot: startStorySnapshot() });

export const StartingAndQueuedDark = projects({ snapshot: startStorySnapshot(), theme: "dark" });

export const StartingAndQueuedPhone = phone390(projects({ snapshot: startStorySnapshot() }));

/** Twelve projects, 60 agents: owner and depends-on columns, compact rows. */
export const Scale60Agents = projects({ snapshot: scaleSnapshot(12, 60) });

export const Scale60AgentsDark = projects({ snapshot: scaleSnapshot(12, 60), theme: "dark" });

/** Filtered to what needs attention. */
export const NeedsAttention = projects({}, async ({ canvasElement }) => {
  const body = within(canvasElement.ownerDocument.body);
  await userEvent.click(await body.findByTestId("projects-filter"));
  await userEvent.click(await body.findByRole("option", { name: "Needs attention" }));
});

/** Compact rows. */
export const Compact = projects({}, async ({ canvasElement }) => {
  const body = within(canvasElement.ownerDocument.body);
  await body.findByTestId("projects-section");
  // Phones have one row layout: no density switch there.
  const density = body.queryByTestId("projects-density");
  if (density) await userEvent.click(density);
});

/** Closed projects unfolded. */
export const ClosedOpen = projects({}, async ({ canvasElement }) => {
  const body = within(canvasElement.ownerDocument.body);
  await userEvent.click(within(await body.findByTestId("projects-closed")).getByRole("button"));
});

/** The cards view of the same factory (the round-2 board). */
export const Cards = projects({ search: "?view=cards", snapshot: withStills(round3Snapshot()) });

/** A viewer: + Project is disabled. */
export const Viewer = projects({ snapshot: round3Snapshot("viewer") });

/** No project yet. */
export const Empty = projects({
  snapshot: emptyFactorySnapshot(),
  search: "?view=table",
  events: { data: [], has_more: false },
});
