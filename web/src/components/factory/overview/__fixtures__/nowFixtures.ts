// The "Now" a snapshot implies: a fixture-only sketch of `status_summary.py`,
// so every Overview story's Now card matches its snapshot. The card's own
// scenarios (busy, idle, waiting on you, paused, starting) are in
// `nowScenarios.ts`.

import type {
  DecisionView,
  FactorySnapshot,
  IssueView,
  ProjectView,
  PrView,
} from "@/lib/factoryApi";
import type {
  StatusHealthItem,
  StatusSummary,
  StatusWaitingItem,
  StatusWorkingItem,
} from "@/lib/factoryStatusApi";
import {
  agentPageHref,
  alertPageHref,
  decisionsPageHref,
  insightsPageHref,
  prPageHref,
  projectPageHref,
} from "../../factoryRoutes";
import { decisionScope, issueScope } from "../needsYouModel";

const PROVISIONING_TEXT: Record<string, string> = {
  provisioning: "getting a machine ready",
  cloning: "copying the repository",
  starting: "connecting its machine",
  connecting: "starting its lead agent",
  briefing: "briefing its lead agent",
};
const STAGE_RANK = [
  "queued",
  "planning",
  "building",
  "review",
  "verify",
  "ready",
  "merged",
  "done",
];

function isLive(project: ProjectView): boolean {
  return (project.status == null || project.status === "active") && project.kind !== "integration";
}

function running(project: ProjectView): boolean {
  return Boolean(project.pl?.running) || project.team.some((m) => m.running);
}

function agentName(decision: DecisionView): string | null {
  const title = decision.asked_by_title ?? "";
  if (title.startsWith("PL · ")) return `the ${title.slice(5)} lead`;
  return title || null;
}

function decisionText(decision: DecisionView): [string, string | null] {
  const who = decision.asked_by_session_id ? agentName(decision) : null;
  if (decision.source === "proposal") {
    const keep = /^Keep or revert (C-\d+)\b/.exec(decision.question);
    if (keep) return [`keep or revert ${keep[1]}`, decision.question];
    const proposes = /^(.+?) proposes (C-\d+)(?: \([^)]*\))?: (.+)$/.exec(decision.question);
    if (proposes) return [`approve ${proposes[1]}'s proposal`, `${proposes[2]}: ${proposes[3]}`];
    return [who ? `approve ${who}'s proposal` : "approve a proposal", decision.question];
  }
  if (who) return [`answer ${who}`, decision.question];
  return [
    decision.project_slug
      ? `answer a question about ${decision.project_slug}`
      : "answer a question",
    decision.question,
  ];
}

function problem(project: ProjectView): string | null {
  const [head = "", ...rest] = (project.overlay_reason ?? "").split(" · ");
  const extra = rest.length ? ` · ${rest.join(" · ")}` : "";
  switch (project.overlay) {
    case "paused":
      return project.paused_by ? `paused by ${project.paused_by}` : "paused";
    case "blocked": {
      const ref = /^Blocked on (D-\d+)/.exec(head)?.[1];
      return ref
        ? `blocked until someone answers ${ref}${extra}`
        : `its lead agent says it is blocked${extra}`;
    }
    case "failed":
      return `its lead agent stopped${extra}`;
    default:
      return null;
  }
}

function primaryPr(project: ProjectView, prs: PrView[]): PrView | null {
  const own = prs.filter((pr) => project.pr_ids.includes(pr.id));
  return (
    own.find((pr) => pr.state === "open" && pr.ready) ??
    own.find((pr) => pr.state === "open") ??
    [...own].reverse().find((pr) => pr.state === "merged") ??
    null
  );
}

/**
 * The "Now" the server would build for `snapshot`'s viewer (fixtures only):
 * open decisions, ready PRs and warning-or-worse `incidents` that wait; live
 * projects past the queue; queued ones; spend from the totals.
 */
export function statusSummaryOf(
  snapshot: FactorySnapshot,
  options: { incidents?: IssueView[]; last24hUsd?: number | null } = {},
): StatusSummary {
  const fid = snapshot.factory.id;
  const viewer = snapshot.viewer ?? null;
  const operator = viewer ? viewer.level >= 2 : true;
  const waiting: StatusWaitingItem[] = [];
  for (const decision of snapshot.decisions.filter((d) => d.status === "open")) {
    const [text, detail] = decisionText(decision);
    const scope = decisionScope(decision, viewer);
    const target = decision.claimed_by ?? decision.assignee ?? "operators";
    const who = target === viewer?.user_id ? "you" : target.split("@")[0]!;
    waiting.push({
      kind: "decision",
      key: `decision:${decision.id}`,
      ref: decision.ref,
      text,
      detail,
      who,
      for_you: scope !== "other",
      urgent: decision.blocking,
      since: decision.created_at,
      project: decision.project_slug,
      href: decisionsPageHref(fid, { decision: decision.ref }),
    });
  }
  for (const pr of snapshot.prs.filter((p) => p.state === "open" && p.ready)) {
    waiting.push({
      kind: "pr",
      key: `pr:${pr.id}`,
      ref: `PR #${pr.number}`,
      text: "review and merge",
      detail: pr.title,
      who: "operators",
      for_you: operator,
      urgent: false,
      since: pr.ready_since ?? 0,
      project: pr.project_slug,
      href: prPageHref(fid, pr.id),
    });
  }
  const mine = new Set((options.incidents ?? []).map((i) => i.id));
  for (const issue of options.incidents ?? []) {
    const open = issue.status === "open" || issue.status === "acked";
    if (issue.class !== "incident" || !open) continue;
    if (issue.severity !== "warning" && issue.severity !== "critical") continue;
    const ref = issue.ref ?? `INC-${issue.number}`;
    waiting.push({
      kind: "incident",
      key: `incident:${issue.id}`,
      ref,
      text: issue.summary,
      detail: null,
      who: issue.assignee ? issue.assignee.split("@")[0]! : "operators",
      for_you: issueScope(issue, viewer, viewer && viewer.level < 2 ? new Set() : mine) !== "other",
      urgent: issue.severity === "critical",
      since: issue.first_seen,
      project: issue.project_slug,
      severity: issue.severity,
      href: alertPageHref(fid, ref),
    });
  }
  waiting.sort(
    (a, b) =>
      Number(!a.for_you) - Number(!b.for_you) ||
      Number(!a.urgent) - Number(!b.urgent) ||
      a.since - b.since ||
      a.kind.localeCompare(b.kind),
  );

  const live = snapshot.projects.filter(isLive);
  const working: StatusWorkingItem[] = live
    .filter((p) => p.stage !== "queued" && p.stage !== "done")
    .filter((p) => p.stage !== "merged" || running(p))
    .map((p) => {
      const pr = primaryPr(p, snapshot.prs);
      return {
        project: p.slug,
        title: p.title,
        stage: p.stage,
        since: p.stage_since,
        pr: pr
          ? {
              id: pr.id,
              number: pr.number,
              state: pr.state,
              ready: pr.ready,
              title: pr.title,
              href: prPageHref(fid, pr.id),
            }
          : null,
        doing: p.pl?.summary ?? null,
        problem: problem(p),
        running: running(p),
        href: projectPageHref(fid, p.slug),
      };
    })
    .sort(
      (a, b) =>
        Number(a.problem == null) - Number(b.problem == null) ||
        STAGE_RANK.indexOf(b.stage) - STAGE_RANK.indexOf(a.stage) ||
        a.since - b.since,
    );
  const next = live
    .filter((p) => p.stage === "queued")
    .map((p) => ({
      project: p.slug,
      title: p.title,
      reason: p.overlay_reason?.startsWith("waiting on ")
        ? `waits for ${p.overlay_reason.replace(/^waiting on /, "")} to finish`
        : p.provisioning && PROVISIONING_TEXT[p.provisioning.stage]
          ? `starting up: ${PROVISIONING_TEXT[p.provisioning.stage]}`
          : p.pl
            ? "starting up"
            : "not started yet",
      since: p.stage_since,
      href: projectPageHref(fid, p.slug),
    }));
  const root = snapshot.members.find((m) => m.session_id === snapshot.factory.root_session_id);
  const idle = working.length === 0 && next.length === 0;
  const ends = snapshot.projects
    .filter((p) => p.kind !== "integration")
    .map((p) => p.closed_at ?? (p.stage === "merged" || p.stage === "done" ? p.stage_since : 0))
    .map((end) => end ?? 0);
  const rate = snapshot.totals.usd_per_h ?? null;
  const health: StatusHealthItem[] = live
    .filter((p) => p.overlay === "failed" || p.overlay === "blocked" || p.overlay === "paused")
    .map((p) => ({
      kind: p.overlay === "paused" ? "project_paused" : (p.overlay as "failed" | "blocked"),
      text: `${p.slug}: ${problem(p)}`,
      project: p.slug,
      since: p.stage_since,
      href: projectPageHref(fid, p.slug),
    }));
  health.sort((a, b) => (a.kind === "failed" ? 0 : 1) - (b.kind === "failed" ? 0 : 1));
  const forYou = waiting.filter((w) => w.for_you).length;
  const state = idle ? "idle" : "working";
  return {
    factory_id: fid,
    factory_name: snapshot.factory.name,
    generated_at: snapshot.server_time,
    headline: `${idle ? "Idle: no active projects." : `Working on ${working.length} projects.`} ${
      forYou ? `${forYou} things wait on you.` : "Nothing waits on you."
    }`,
    state,
    waiting,
    working,
    next,
    lead: root?.summary
      ? {
          who: root.title,
          text: root.summary,
          session_id: root.session_id,
          href: agentPageHref(fid, root.session_id),
        }
      : null,
    spend: {
      last_24h_usd:
        options.last24hUsd === undefined ? snapshot.totals.cost_usd : options.last24hUsd,
      today_usd: null,
      usd_per_h: rate,
      total_usd: snapshot.factory.cost_usd,
      idle,
      idle_since: idle ? Math.max(snapshot.factory.started_at ?? 0, ...ends) || null : null,
      idle_spend: idle && rate != null && rate >= 0.01,
      href: insightsPageHref(fid),
    },
    health: {
      state: health.some((h) => h.kind !== "project_paused") ? "attention" : "ok",
      items: health,
    },
    counts: {
      waiting_for_you: forYou,
      waiting_total: waiting.length,
      working: working.length,
      queued: next.length,
    },
  };
}
