// The 2026-11-02 morning on the round-3 factory: projects Atlas just started
// (their PL pods coming up, a PL woken after days, no pod free yet, one held
// on a dependency), one being created, and two validation projects on
// already-merged PRs (validate-build-tier tracks app#9774, merged before it
// started). Every export is a fresh copy a caller may mutate.

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
    start: ProjectStartView | null,
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
  const pod = { label: "All 12 pods in use", pods_in_use: 12, pod_cap: 12 };
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
    queued(
      "api-rate-limit",
      "Rate limit the public API",
      { state: "starting", phase: "briefing", label: "Briefing the PL", since: now - 40 },
      { pl: plRef(pl, "api-rate-limit", 4) },
    ),
    // Its PL worked days ago and was stopped while idle; waking it is a restart.
    queued(
      "db-backfill",
      "Backfill project stages",
      {
        state: "starting",
        phase: "provisioning",
        label: "Provisioning sandbox",
        since: now - 150,
        restart: true,
      },
      {
        pl: plRef(pl, "db-backfill", 5),
        provisioning: { stage: "provisioning", label: "Provisioning sandbox" },
      },
    ),
    queued("api-request-id", "Request ids in API logs", {
      state: "starting",
      phase: "creating",
      label: "Creating the PL session",
      since: now - 20,
    }),
    queued("api-healthz", "Health endpoint", {
      state: "queued",
      phase: "server",
      label: "Starts within a minute",
      since: now - 25,
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
    // Held on its dependency: the overlay says why, no start reason.
    queued("ux-theme-tokens", "Theme tokens for dark mode", null, {
      overlay: "waiting",
      overlay_reason: "waiting on ux-dark-toggle",
      depends_on: ["ux-dark-toggle"],
    }),
  ];

  // Atlas spawns this one's PL (a charter with spawn_owner "lead").
  const settings = snapshot.projects.find((p) => p.slug === "settings-row")!;
  settings.start = {
    state: "queued",
    phase: "lead",
    label: "Waiting for Atlas to start its PL",
    since: settings.stage_since,
  };

  // Projects made to check work that is already merged.
  const merged = snapshot.prs.find((p) => p.state === "merged")!;
  const mergedPr = (number: number, slug: string, title: string, head: string): PrView => ({
    ...structuredClone(merged),
    id: `${number}`.repeat(8),
    number,
    url: `https://github.com/sample-org/sample-app/pull/${number}`,
    title,
    project_slug: slug,
    head_ref: head,
    base_ref: "main",
  });
  const validation = (slug: string, title: string, n: number, patch: Partial<ProjectView>) => ({
    ...queued(slug, title, null),
    stage: "verify" as const,
    stage_since: now - 900,
    pl: { ...plRef(pl, slug, n), running: true, status: "running" as const },
    ...patch,
  });
  // validate-build-tier: made after Lena merged app#9774; its PL deploys a preview to check it.
  const pr9774 = mergedPr(
    9774,
    "validate-build-tier",
    "feat(previews): build tier for factory previews",
    "factory/preview-build-tier",
  );
  const adopted = validation("validate-build-tier", "Validate the preview build tier", 3, {
    stage_note: "!9774 was merged before this project started",
    pr_ids: [pr9774.id],
  });
  // validate-preview-ttl: its PR merged, a test task on the work graph still runs.
  const pr9781 = mergedPr(
    9781,
    "validate-preview-ttl",
    "fix(previews): expire idle previews",
    "factory/preview-ttl",
  );
  const checking = validation("validate-preview-ttl", "Check preview expiry", 6, {
    stage_note: "PR merged · 1 task still open",
    pr_ids: [pr9781.id],
    tasks_summary: {
      total: 3,
      done: 2,
      ready: 0,
      running: 1,
      blocked: 0,
      failed: 0,
      critical_path_min: 25,
    },
  });

  snapshot.prs.push(pr9774, pr9781);
  const active = snapshot.projects.filter((p) => p.status !== "done");
  const closed = snapshot.projects.filter((p) => p.status === "done");
  snapshot.projects = [...active, adopted, checking, ...fresh, ...closed];
  return snapshot;
}

/**
 * PL sandboxes of the 2026-11-02 launch, one card per step: the cluster full
 * (waiting for a node), a cold node pulling the 3.9 GB image, the clone, the
 * containers and the host starting, the server's retry on a new sandbox, a
 * first failure about to be retried, one that failed twice, and a PL whose own
 * sandbox did not wake (once, then twice).
 */
export function launchStorySnapshot(): FactorySnapshot {
  const snapshot = round3Snapshot();
  const now = snapshot.server_time;
  const template = snapshot.projects.find((p) => p.slug === "settings-row")!;
  const pl = snapshot.projects.find((p) => p.slug === "api-version")!.pl!;
  let n = 0;
  const card = (
    slug: string,
    title: string,
    patch: Partial<ProjectView>,
    start: ProjectStartView | null = null,
  ): ProjectView => ({
    ...structuredClone(template),
    slug,
    title,
    priority: 2,
    stage: "queued",
    stage_since: now - 600,
    overlay: null,
    overlay_reason: null,
    depends_on: null,
    pl: plRef(pl, slug, ++n),
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
  const starting = (
    phase: string,
    label: string,
    phaseAgo: number,
    attempt?: number,
  ): ProjectStartView => ({
    state: "starting",
    phase,
    label,
    since: now - 600,
    phase_since: now - phaseAgo,
    ...(attempt ? { attempt } : {}),
  });
  const step = (waitsOn: string, label: string, ago: number, attempt?: number) => ({
    stage: "starting",
    label,
    step: waitsOn,
    since: now - ago,
    ...(attempt ? { attempt } : {}),
  });
  snapshot.projects = [
    card(
      "build-tier-drafts",
      "Draft previews for the build tier",
      { provisioning: step("image", "Pulling the image", 236) },
      starting("image", "Pulling the image", 236),
    ),
    card(
      "tier-copy",
      "Build tier copy",
      { provisioning: step("node", "Waiting for a node", 95) },
      starting("node", "Waiting for a node", 95),
    ),
    card(
      "tier-docs",
      "Build tier docs",
      { provisioning: step("clone", "Cloning the repo", 40) },
      starting("clone", "Cloning the repo", 40),
    ),
    card(
      "tier-metrics",
      "Build tier metrics",
      { provisioning: step("containers", "Starting containers", 15) },
      starting("containers", "Starting containers", 15),
    ),
    card(
      "tier-limits",
      "Build tier limits",
      { provisioning: step("host", "Starting the host", 20) },
      starting("host", "Starting the host", 20),
    ),
    card(
      "tier-alerts",
      "Build tier alerts",
      { provisioning: step("image", "Pulling the image", 130, 2) },
      starting("image", "Pulling the image", 130, 2),
    ),
    card("tier-flags", "Build tier flags", {
      overlay: "failed",
      overlay_reason: "PL sandbox failed · retrying on a new sandbox",
    }),
    card("tier-billing", "Build tier billing", {
      overlay: "failed",
      overlay_reason: "PL sandbox failed twice",
    }),
    // Its own sandbox, with its files, did not wake: Restart first, a new sandbox last.
    card("tier-quota", "Build tier quota", {
      overlay: "failed",
      overlay_reason: "PL sandbox did not wake · waking it again",
    }),
    card("tier-search", "Build tier search", {
      overlay: "failed",
      overlay_reason: "PL sandbox did not wake twice",
    }),
  ];
  return snapshot;
}
