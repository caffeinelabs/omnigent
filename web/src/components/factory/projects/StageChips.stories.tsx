import type { Meta, StoryObj } from "@storybook/react-vite";
import { BoardFrame } from "../board/boardStoryKit";
import { ProjectHeader } from "../project/ProjectHeader";
import { FactoryViewerProvider } from "../shell/viewerCan";
import { startStorySnapshot } from "./__fixtures__/startFixtures";
import { StageDots } from "./StageDots";

const SLUGS = [
  "ux-status-page",
  "ux-env-badge-copy",
  "api-rate-limit",
  "db-backfill",
  "api-request-id",
  "api-healthz",
  "ux-slash-focus",
  "ux-dark-toggle",
  "ux-theme-tokens",
  "settings-row",
  "validate-build-tier",
  "validate-preview-ttl",
];

/** Each queued state's stage chip next to its project, as a Projects row reads it. */
function Chips() {
  const snapshot = startStorySnapshot();
  const projects = SLUGS.map((slug) => snapshot.projects.find((p) => p.slug === slug)!);
  return (
    <ul className="flex flex-col divide-y divide-border rounded-xl border border-border bg-card px-3 text-sm">
      {projects.map((project) => (
        <li key={project.slug} className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 py-2">
          <span className="w-40 shrink-0 truncate font-mono text-xs text-muted-foreground">
            {project.slug}
          </span>
          <StageDots project={project} nowS={snapshot.server_time} />
        </li>
      ))}
    </ul>
  );
}

/** The project page's stage card for starting, restarting, queued, waiting and validating. */
function Headers() {
  const snapshot = startStorySnapshot();
  return (
    <FactoryViewerProvider viewer={snapshot.viewer}>
      {[
        "ux-status-page",
        "db-backfill",
        "ux-dark-toggle",
        "settings-row",
        "ux-theme-tokens",
        "validate-build-tier",
        "validate-preview-ttl",
      ].map((slug) => (
        <ProjectHeader
          key={slug}
          snapshot={snapshot}
          project={snapshot.projects.find((p) => p.slug === slug)!}
          nowS={snapshot.server_time}
          onDecision={() => {}}
        />
      ))}
    </FactoryViewerProvider>
  );
}

const meta = {
  title: "Factory/Projects table/Stage chips",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const PHONE = {
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
} satisfies Partial<Story>;

/**
 * Starting PL (sandbox, runner, brief, session being created), Restarting PL
 * (a woken PL), Queued (the server's next pass, a pod: next or 1 ahead,
 * Atlas), Waiting on a dependency, and Verify on merged PRs still validated
 * (merged before the project started; a test task still open).
 */
export const QueuedAndStarting: Story = {
  render: () => (
    <BoardFrame width={720}>
      <Chips />
    </BoardFrame>
  ),
};

export const QueuedAndStartingDark: Story = {
  render: () => (
    <BoardFrame width={720} theme="dark">
      <Chips />
    </BoardFrame>
  ),
};

export const QueuedAndStartingPhone: Story = {
  ...PHONE,
  render: () => (
    <BoardFrame width={390}>
      <Chips />
    </BoardFrame>
  ),
};

/** The project page header says the same, in full. */
export const ProjectHeaders: Story = {
  render: () => (
    <BoardFrame width={1100}>
      <Headers />
    </BoardFrame>
  ),
};

export const ProjectHeadersDark: Story = {
  render: () => (
    <BoardFrame width={1100} theme="dark">
      <Headers />
    </BoardFrame>
  ),
};

export const ProjectHeadersPhone: Story = {
  ...PHONE,
  render: () => (
    <BoardFrame width={390}>
      <Headers />
    </BoardFrame>
  ),
};
