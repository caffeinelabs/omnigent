// The Now card's own scenarios, from the round-3 factory: busy, idle and still
// spending, waiting on you (the live factory's D-19), paused and starting up.

import type { FactorySnapshot } from "@/lib/factoryApi";
import type { StatusSummary } from "@/lib/factoryStatusApi";
import { round3IssueList } from "../../agents/__fixtures__/opsFixtures";
import { factoryPageHref } from "../../factoryRoutes";
import { ROUND3_NOW_S, round3Snapshot } from "../../shell/__fixtures__/shellFixtures";
import { statusSummaryOf } from "./nowFixtures";

const HOUR = 3600;

/** Atlas's factory as Luka (creator): eight things wait on him, four projects in flight. */
export function busyNow(): StatusSummary {
  const snapshot = round3Snapshot();
  // Proposal questions as the server words them today.
  const questions: Record<string, string> = {
    "D-33": "Atlas proposes C-4: split billing v2 into 3 projects",
    "D-35": "Sentinel proposes C-15 (rules): PLs ask before a second preview",
  };
  for (const decision of snapshot.decisions) {
    decision.question = questions[decision.ref] ?? decision.question;
  }
  return statusSummaryOf(snapshot, { incidents: round3IssueList(), last24hUsd: 41.2 });
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
  const root = snapshot.members.find((m) => m.session_id === snapshot.factory.root_session_id);
  if (root) root.summary = "No projects in flight · waiting for the next brief";
  return snapshot;
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
  const summary = busyNow();
  return {
    ...summary,
    state: "paused",
    headline: "Paused by alice. 8 things wait on you.",
    health: {
      state: "paused",
      items: [
        {
          kind: "paused",
          text: "alice paused the factory",
          by: "alice",
          note: "Budget review at 14:00, back after",
          since: ROUND3_NOW_S - 25 * 60,
          href: factoryPageHref(summary.factory_id),
        },
        ...summary.health.items,
      ],
    },
  };
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
