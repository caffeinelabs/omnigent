// The Now card's own scenarios, from the round-3 factory: busy, the live
// factory of 2 Nov (four PRs wait on a merge, one project still validates),
// idle and still spending, waiting on you (the live factory's D-19), paused,
// starting up, a project that can't start, and a merged project its lead
// still validates.

import type { FactorySnapshot, NotifiedView, ProjectView } from "@/lib/factoryApi";
import type { StatusSummary } from "@/lib/factoryStatusApi";
import { round3IssueList } from "../../agents/__fixtures__/opsFixtures";
import { ROUND3_NOW_S, round3Snapshot } from "../../shell/__fixtures__/shellFixtures";
import { statusSummaryOf } from "./nowFixtures";

const HOUR = 3600;

/** Atlas's factory as Luka (creator): eight things wait on him, four projects in flight. */
export function busyNow(): StatusSummary {
  return statusSummaryOf(round3Snapshot(), { incidents: round3IssueList(), last24hUsd: 41.2 });
}

function quietSnapshot(): FactorySnapshot {
  const snapshot = round3Snapshot();
  const finished = snapshot.projects.filter((p) => p.slug === "docs-factory-envs");
  for (const project of finished) project.closed_at = ROUND3_NOW_S - 3 * HOUR;
  snapshot.projects = finished;
  snapshot.prs = snapshot.prs.filter((pr) => pr.project_slug === "docs-factory-envs");
  snapshot.decisions = [];
  snapshot.totals = { ...snapshot.totals, usd_per_h: 2.71 };
  snapshot.factory.cost_usd = 170.0;
  atlasSays(snapshot, "No projects in flight · waiting for the next brief");
  return snapshot;
}

/** Atlas's one-liner (its beacon) in `snapshot`. */
function atlasSays(snapshot: FactorySnapshot, summary: string): void {
  const root = snapshot.members.find((m) => m.session_id === snapshot.factory.root_session_id);
  if (root) root.summary = summary;
}

const LIVE_PRS = [9826, 9829, 9828, 9827] as const;

/** The live factory's snapshot at 11:45 UTC on 2 Nov (see `liveNow`). */
export function liveSnapshot(): FactorySnapshot {
  const snapshot = quietSnapshot();
  const round3 = round3Snapshot();
  const template = round3.projects.find((p) => p.slug === "api-version")!;
  const prTemplate = round3.prs.find((pr) => pr.number === 9630)!;
  const titles = [
    "Add GET /version",
    "Show deployed versions in Settings",
    "ENV badge: click to copy",
    "Warm the asset cache on deploy",
  ];
  const ready = LIVE_PRS.map((number, i) => {
    const slug = ["version-endpoint", "settings-row", "env-badge", "cache-warm"][i]!;
    const project: ProjectView = {
      ...template,
      slug,
      title: slug.replace("-", " "),
      stage: "ready",
      stage_since: ROUND3_NOW_S - (5 - i) * HOUR,
      overlay: null,
      overlay_reason: null,
      pr_ids: [`pr-${number}`],
      tasks_summary: {
        total: 1,
        done: 1,
        ready: 0,
        running: 0,
        blocked: 0,
        failed: 0,
        critical_path_min: null,
      },
      pl: {
        ...template.pl!,
        session_id: `pl-${slug}`,
        title: `PL · ${slug}`,
        running: false,
        status: "idle",
        // What the live leads said: never shown by the card.
        summary: `PR #${number} — PR #${number} @00d863e READY FOR HUMAN MERGE (verified, demo PASS)`,
      },
    };
    return {
      project,
      pr: {
        ...prTemplate,
        id: `pr-${number}`,
        number,
        project_slug: slug,
        title: titles[i]!,
        ready_since: ROUND3_NOW_S - (4 - i) * HOUR,
      },
    };
  });
  const validating: ProjectView = {
    ...template,
    slug: "validate-build-tier",
    title: "Validate build tier",
    stage: "verify",
    stage_since: ROUND3_NOW_S - 50 * 60,
    stage_note: "PR merged · 1 task still open",
    overlay: null,
    overlay_reason: null,
    pr_ids: ["pr-9774"],
    tasks_summary: {
      total: 2,
      done: 1,
      ready: 0,
      running: 1,
      blocked: 0,
      failed: 0,
      critical_path_min: null,
    },
    active_task: { ref: "T-9", title: "Check the build tier on a preview", state: "running" },
    pl: { ...template.pl!, title: "PL · validate-build-tier", running: true, status: "running" },
  };
  snapshot.projects = [...ready.map((r) => r.project), validating];
  snapshot.prs = [
    ...ready.map((r) => r.pr),
    {
      ...prTemplate,
      id: "pr-9774",
      number: 9774,
      project_slug: "validate-build-tier",
      state: "merged",
      ready: false,
    },
  ];
  snapshot.decisions = [];
  snapshot.totals = { ...snapshot.totals, usd_per_h: 3.1 };
  atlasSays(
    snapshot,
    "10 proj (4 done) · 4 PRs ready · 0 ⚠ · #9826 #9827 #9828 #9829 verified on previews, " +
      "waiting for a human merge since this morning; next beat checks the demos again",
  );
  const root = snapshot.members.find((m) => m.session_id === snapshot.factory.root_session_id);
  if (root) root.last_activity_at = ROUND3_NOW_S - 52 * 60;
  return snapshot;
}

/** Luka's Slack DMs about the live factory's first PR: the first DM, then a reminder. */
export function liveNotified(): Record<string, NotifiedView[]> {
  const user = round3Snapshot().viewer?.user_id ?? "luka@caffeine.ai";
  return {
    "pr:caffeinelabs/app#9826": [
      { user_id: user, at: ROUND3_NOW_S - 4 * HOUR, stage: "ready", ok: true },
      { user_id: user, at: ROUND3_NOW_S - 10 * 60, stage: "r4h", ok: true },
    ],
  };
}

/**
 * The live factory at 11:45 UTC on 2 Nov: four projects only wait on Luka to
 * merge their PRs (listed once, under Waiting on you, with Review →), one
 * still validates its preview; Atlas's note links the PRs it names.
 */
export function liveNow(): StatusSummary {
  return statusSummaryOf(liveSnapshot(), { last24hUsd: 61.44, notified: liveNotified() });
}

/** The live factory once validate-build-tier is done too: idle, and four PRs wait on Luka. */
export function idleWaitingNow(): StatusSummary {
  const snapshot = liveSnapshot();
  snapshot.projects = snapshot.projects.filter((p) => p.slug !== "validate-build-tier");
  return statusSummaryOf(snapshot, { last24hUsd: 61.44, notified: liveNotified() });
}

/** Nothing in flight since the docs project closed, yet the leads still spend $2.71/h. */
export function idleNow(): StatusSummary {
  return statusSummaryOf(quietSnapshot(), { last24hUsd: 65.04 });
}

/**
 * The live factory's evidence: idle, while Atlas's proposal (D-19) waited 2d 18h
 * for an answer, a ready PR waits for a merge and Mia owes an answer elsewhere.
 */
export function waitingOnYouNow(): StatusSummary {
  const snapshot = quietSnapshot();
  const round3 = round3Snapshot();
  const d2 = round3.decisions.find((d) => d.ref === "D-2")!;
  const proposal = round3.decisions.find((d) => d.ref === "D-33")!;
  snapshot.decisions = [
    {
      ...proposal,
      ref: "D-19",
      number: 19,
      blocking: false,
      question: "Atlas proposes C-4: Read Sentinel's retros at each heartbeat",
      created_at: ROUND3_NOW_S - (2 * 24 + 18) * HOUR,
    },
    { ...d2, created_at: ROUND3_NOW_S - 40 * 60 },
  ];
  snapshot.prs = round3.prs.filter((pr) => pr.number === 9630);
  return statusSummaryOf(snapshot, { last24hUsd: 65.04 });
}

/** Alice paused the whole factory with a note; one project had already failed. */
export function pausedNow(): StatusSummary {
  const snapshot = round3Snapshot();
  snapshot.factory.state = "paused";
  return statusSummaryOf(snapshot, {
    incidents: round3IssueList(),
    last24hUsd: 41.2,
    pause: {
      summary: "alice paused the factory",
      by: "alice",
      note: "Budget review at 14:00, back after",
      since: ROUND3_NOW_S - 25 * 60,
    },
  });
}

/** Four new projects queued a few minutes ago while their lead agents start. */
export function startingNow(): StatusSummary {
  const snapshot = quietSnapshot();
  const template = round3Snapshot().projects.find((p) => p.slug === "settings-row")!;
  const steps = [
    ["billing-export", "provisioning", "Provisioning sandbox", 5],
    ["search-index", "cloning", "Cloning repository", 4],
    ["audit-log", "connecting", "Starting agent", 3],
    ["quota-alerts", "briefing", "Briefing the PL", 2],
  ] as const;
  snapshot.projects = steps.map(([slug, stage, label, minutes]) => ({
    ...template,
    slug,
    title: slug.replace("-", " "),
    overlay: null,
    overlay_reason: null,
    stage_since: ROUND3_NOW_S - minutes * 60,
    provisioning: { stage, label },
  }));
  return statusSummaryOf(snapshot, { last24hUsd: 65.04 });
}

/**
 * Three new projects queued: cache-warm's lead agent never got a machine (its
 * sandbox failed), the others are still starting.
 */
export function cantStartNow(): StatusSummary {
  const snapshot = quietSnapshot();
  const template = round3Snapshot().projects.find((p) => p.slug === "cache-warm")!;
  const queued = (slug: string, minutes: number): ProjectView => ({
    ...template,
    slug,
    title: slug.replace("-", " "),
    stage: "queued",
    stage_since: ROUND3_NOW_S - minutes * 60,
    overlay: null,
    overlay_reason: null,
    pr_ids: [],
    team: [],
    provisioning: null,
  });
  snapshot.projects = [
    { ...queued("cache-warm", 6), overlay: "failed", overlay_reason: "PL sandbox failed" },
    {
      ...queued("audit-log", 4),
      provisioning: { stage: "cloning", label: "Cloning repository" },
      start: { state: "starting", phase: "cloning", label: "Cloning repository", since: null },
    },
    {
      ...queued("quota-alerts", 2),
      pl: null,
      start: {
        state: "queued",
        phase: "pod_cap",
        label: "All 12 pods in use",
        since: ROUND3_NOW_S - 2 * 60,
        ahead: 1,
        pods_in_use: 12,
        pod_cap: 12,
      },
    },
  ];
  atlasSays(snapshot, "Starting 3 new projects from the brief");
  return statusSummaryOf(snapshot, { last24hUsd: 65.04 });
}

/**
 * validate-build-tier's PR merged an hour ago; its lead deployed a preview to
 * validate it and checks on it at its next beat (between turns, not running).
 */
export function validatingNow(): StatusSummary {
  const snapshot = quietSnapshot();
  const round3 = round3Snapshot();
  const template = round3.projects.find((p) => p.slug === "api-version")!;
  const pr = round3.prs.find((p) => p.number === 9630)!;
  snapshot.prs = [
    {
      ...pr,
      id: "pr-9774",
      number: 9774,
      title: "build: validate the build tier on previews",
      state: "merged",
      ready: false,
      project_slug: "validate-build-tier",
    },
  ];
  snapshot.projects = [
    {
      ...template,
      slug: "validate-build-tier",
      title: "Validate build tier",
      stage: "merged",
      stage_since: ROUND3_NOW_S - HOUR,
      pr_ids: ["pr-9774"],
      pl: {
        ...template.pl!,
        title: "PL · validate-build-tier",
        status: "idle",
        running: false,
        summary: "Preview deploying for #9774; checking it at the next beat",
        last_activity_at: ROUND3_NOW_S - 8 * 60,
      },
    },
  ];
  atlasSays(snapshot, "validate-build-tier merged · its lead is checking the preview");
  return statusSummaryOf(snapshot, { last24hUsd: 65.04 });
}
