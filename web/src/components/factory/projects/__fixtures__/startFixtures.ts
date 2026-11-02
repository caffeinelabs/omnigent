// The 2026-11-02 morning on the round-3 factory: four projects Atlas just
// started (their PL pods coming up, or no pod free yet), one being created,
// and validate-build-tier, which tracks the already-merged app#9774 while its
// PL still validates it. Every export is a fresh copy a caller may mutate.

import type {
  FactorySnapshot,
  MemberRef,
  PrView,
  ProjectStartView,
  ProjectView,
} from "@/lib/factoryApi";
import { round3Snapshot } from "../../shell/__fixtures__/shellFixtures";

function plRef(template: MemberRef, slug: string, n: number): MemberRef {
  return {
    ...template,
    session_id: `${n}`.repeat(32).slice(0, 32),
    title: `PL · ${slug}`,
    status: "idle",
    running: false,
    summary: null,
    last_activity_at: null,
    cost_usd: null,
    url: `/c/${`${n}`.repeat(32).slice(0, 32)}`,
  };
}

/** The new and validating projects, each with what its row should say. */
export function startStorySnapshot(): FactorySnapshot {
  const snapshot = round3Snapshot();
  const now = snapshot.server_time;
  const template = snapshot.projects.find((p) => p.slug === "settings-row")!;
  const pl = snapshot.projects.find((p) => p.slug === "api-version")!.pl!;
  const queued = (
    slug: string,
    title: string,
    start: ProjectStartView,
    patch: Partial<ProjectView> = {},
  ): ProjectView => ({
    ...structuredClone(template),
    slug,
    title,
    priority: 2,
    stage: "queued",
    stage_since: now - 300,
    overlay: null,
    overlay_reason: null,
    depends_on: null,
    pl: null,
    team: [],
    pr_ids: [],
    open_decision_ids: [],
    cost_usd: null,
    provisioning: null,
    tasks_summary: null,
    loops_summary: null,
    start,
    ...patch,
  });
  const pod = { label: "No free pod: 12 of 12 in use", pods_in_use: 12, pod_cap: 12 };
  const fresh: ProjectView[] = [
    queued(
      "ux-status-page",
      "Status page",
      {
        state: "starting",
        phase: "provisioning",
        label: "Provisioning sandbox",
        since: now - 130,
      },
      {
        pl: plRef(pl, "ux-status-page", 1),
        provisioning: { stage: "provisioning", label: "Provisioning sandbox" },
      },
    ),
    queued(
      "ux-env-badge-copy",
      "Env badge copy",
      {
        state: "starting",
        phase: "connect",
        label: "Waiting for the agent to connect",
        since: now - 250,
      },
      { pl: plRef(pl, "ux-env-badge-copy", 2) },
    ),
    queued("api-request-id", "Request ids in API logs", {
      state: "starting",
      phase: "creating",
      label: "Creating the PL session",
      since: now - 20,
    }),
    queued("ux-slash-focus", "Slash focuses the composer", {
      state: "queued",
      phase: "pod_cap",
      since: now - 180,
      ahead: 0,
      ...pod,
    }),
    queued("ux-dark-toggle", "Dark mode toggle", {
      state: "queued",
      phase: "pod_cap",
      since: now - 170,
      ahead: 1,
      ...pod,
    }),
  ];

  // validate-build-tier: made after Luka merged app#9774, its PL deploys a preview to check it.
  const merged = snapshot.prs.find((p) => p.state === "merged")!;
  const pr9774: PrView = {
    ...structuredClone(merged),
    id: "9774".repeat(8),
    number: 9774,
    url: "https://github.com/caffeinelabs/app/pull/9774",
    title: "feat(previews): build tier for factory previews",
    project_slug: "validate-build-tier",
    head_ref: "factory/preview-build-tier",
    base_ref: "main",
  };
  const validating: ProjectView = {
    ...queued("validate-build-tier", "Validate the preview build tier", {
      state: "starting",
      phase: null,
      label: "Starting",
      since: null,
    }),
    stage: "verify",
    stage_since: now - 900,
    stage_note: "PR merged · 1 task still open",
    agent_stage: "verify",
    start: null,
    pl: { ...plRef(pl, "validate-build-tier", 3), running: true, status: "running" },
    pr_ids: [pr9774.id],
    tasks_summary: {
      total: 3,
      done: 2,
      ready: 0,
      running: 1,
      blocked: 0,
      failed: 0,
      critical_path_min: 25,
    },
  };

  snapshot.prs.push(pr9774);
  const active = snapshot.projects.filter((p) => p.status !== "done");
  const closed = snapshot.projects.filter((p) => p.status === "done");
  snapshot.projects = [...active, validating, ...fresh, ...closed];
  return snapshot;
}
