import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import { ProjectsPage } from "@/pages/factory/ProjectsPage";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
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

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
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
  await userEvent.click(await body.findByTestId("projects-density"));
});

/** Closed projects unfolded. */
export const ClosedOpen = projects({}, async ({ canvasElement }) => {
  const body = within(canvasElement.ownerDocument.body);
  await userEvent.click(within(await body.findByTestId("projects-closed")).getByRole("button"));
});

/** The cards view of the same factory (the round-2 board). */
export const Cards = projects({ search: "?view=cards" });

/** A viewer: + Project is disabled. */
export const Viewer = projects({ snapshot: round3Snapshot("viewer") });

/** No project yet. */
export const Empty = projects({
  snapshot: emptyFactorySnapshot(),
  search: "?view=table",
  events: { data: [], has_more: false },
});
