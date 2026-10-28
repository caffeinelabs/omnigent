import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";
import { AlertsPage } from "@/pages/factory/AlertsPage";
import { issuePage } from "../agents/__fixtures__/opsFixtures";
import { CockpitStory, type CockpitStoryOptions, SNAPSHOT_PATH } from "../board/boardStoryKit";
import { round3Events, round3Snapshot } from "../shell/__fixtures__/shellFixtures";

const ISSUES_PATH = `${SNAPSHOT_PATH}/issues`;

function page(path: string, options: CockpitStoryOptions = {}, play?: Story["play"]): Story {
  return {
    render: () => (
      <CockpitStory snapshot={round3Snapshot()} events={round3Events()} path={path} {...options} />
    ),
    play,
  };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

const meta = {
  title: "Factory/Alerts",
  component: AlertsPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof AlertsPage>;

export default meta;
type Story = StoryObj<typeof meta>;

// ── Alerts list ────────────────────────────────────────────────

/** Open incidents: INC-12 (dead PL, critical) and INC-13 (repeating IC), each with its runbook. */
export const Open = page("/alerts");

export const OpenDark = page("/alerts", { theme: "dark" });

export const OpenMobile = phone(page("/alerts"));

export const OpenMobileDark = phone(page("/alerts", { theme: "dark" }));

/** Findings: flaky CI on !9631, wasted heartbeats (acked). */
export const Findings = page("/alerts", { search: "?class=finding&status=acked" });

export const FindingsOpen = page("/alerts", { search: "?class=finding" });

/** Resolved today: INC-11, resolved by the server when the preview came back. */
export const Resolved = page("/alerts", { search: "?status=resolved" });

/** The rules table and the routing line. */
export const Rules = page("/alerts", {}, async ({ canvasElement }) => {
  await userEvent.click(
    await within(canvasElement.ownerDocument.body).findByTestId("alerts-rules-toggle"),
  );
});

export const RulesMobile = phone(page("/alerts", {}, Rules.play));

/** Assigning: the roster, operators and owners. */
export const AssignMenu = page("/alerts", {}, async ({ canvasElement }) => {
  await userEvent.click(
    await within(canvasElement.ownerDocument.body).findByTestId("issue-assign-INC-13"),
  );
});

/** Ops (viewer): Ack, Assign, Snooze, Resolve and the runbooks disabled with who can. */
export const Viewer = page("/alerts", { snapshot: round3Snapshot("viewer") });

export const ViewerMobile = phone(page("/alerts", { snapshot: round3Snapshot("viewer") }));

export const Empty = page("/alerts", {
  routes: { [ISSUES_PATH]: { body: { data: [], count: 0, mine: [] } } },
});

export const Loading = page("/alerts", { routes: { [ISSUES_PATH]: "pending" } });

export const LoadError = page("/alerts", {
  routes: { [ISSUES_PATH]: { error: 503, message: "unavailable" } },
});

// ── Incident page ──────────────────────────────────────────────

/** INC-13: the IC's steps and the PL's wait before it, the runbook, root cause and evidence. */
export const Incident = page("/alerts/13");

export const IncidentDark = page("/alerts/13", { theme: "dark" });

export const IncidentMobile = phone(page("/alerts/13"));

export const IncidentMobileDark = phone(page("/alerts/13", { theme: "dark" }));

/** INC-12: a dead PL, critical, for owners (Restart, Relaunch with spec, Pause project). */
export const IncidentCritical = page("/alerts/12");

/** Runbook Restart behind its confirmation. */
export const IncidentRestartConfirm = page("/alerts/12", {}, async ({ canvasElement }) => {
  await userEvent.click(
    await within(canvasElement.ownerDocument.body).findByTestId("runbook-INC-12-restart"),
  );
});

/** Resolved with a confirmed root cause. */
export const IncidentResolved = page("/alerts/13", {
  routes: {
    [`${ISSUES_PATH}/INC-13`]: {
      body: (() => {
        const found = issuePage("INC-13")!;
        found.issue = {
          ...found.issue,
          status: "resolved",
          resolved_by: "mia@caffeine.ai",
          resolved_at: found.issue.last_seen + 300,
          category: "env_flaky",
          acked_by: "mia@caffeine.ai",
        };
        return found;
      })(),
    },
  },
});

/** A finding's page: F-7 flaky CI. */
export const FindingPage = page("/alerts/F-7");

/** Ops (viewer) on the incident: everything readable, nothing pullable. */
export const IncidentViewer = page("/alerts/13", { snapshot: round3Snapshot("viewer") });

export const IncidentLoading = page("/alerts/13", {
  routes: { [`${ISSUES_PATH}/INC-13`]: "pending" },
});

export const IncidentMissing = page("/alerts/99", {
  routes: { [`${ISSUES_PATH}/INC-99`]: { error: 404, message: "issue not found" } },
});
