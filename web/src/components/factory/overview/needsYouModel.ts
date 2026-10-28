// The Overview's Needs-you list, split by whom it waits on: "mine" (assigned
// to me, or claimed by me), "role" (assigned to a role I hold: operators or
// owners) and everything else. Decisions follow their assignee and claim;
// incidents follow the server's "mine"; PRs ready for review and alerts are
// for operators. Pure; tests pin it.

import type {
  DecisionView,
  FactorySnapshot,
  IssueView,
  PrView,
  ProjectView,
  ViewerBlock,
} from "@/lib/factoryApi";
import { type FactoryAlert, needsYou } from "../board/boardModel";

export type NeedsScope = "mine" | "role" | "other";

export type NeedsItem =
  | { kind: "decision"; key: string; scope: NeedsScope; at: number; decision: DecisionView }
  | { kind: "pr"; key: string; scope: NeedsScope; at: number; pr: PrView }
  | { kind: "alert"; key: string; scope: NeedsScope; at: number; alert: FactoryAlert }
  | { kind: "claim"; key: string; scope: NeedsScope; at: number; project: ProjectView }
  | { kind: "issue"; key: string; scope: NeedsScope; at: number; issue: IssueView };

/** Open incidents as the issues route lists them, with which ones are the viewer's. */
export interface NeedsIssues {
  data: readonly IssueView[];
  /** Ids of the issues for the viewer or their role. */
  mine: readonly string[];
}

export interface NeedsYouSplit {
  mine: NeedsItem[];
  role: NeedsItem[];
  /** Everything open, mine and my role's first. */
  all: NeedsItem[];
}

/** Session levels: edit (operator), manage (owner). */
const LEVEL_EDIT = 2;
const LEVEL_MANAGE = 3;

/** Whom an open decision waits on, from the viewer's side. */
export function decisionScope(decision: DecisionView, viewer: ViewerBlock | null): NeedsScope {
  const me = viewer?.user_id ?? null;
  // No viewer block: one person runs everything.
  const level = viewer ? viewer.level : 4;
  if (decision.claimed_by) return decision.claimed_by === me ? "mine" : "other";
  const assignee = decision.assignee ?? "operators";
  if (assignee === "operators") return level >= LEVEL_EDIT ? "role" : "other";
  if (assignee === "owners") return level >= LEVEL_MANAGE ? "role" : "other";
  return assignee === me ? "mine" : "other";
}

/** Whom an open incident waits on: me (assigned to me), my role (the server's "mine"), others. */
export function issueScope(
  issue: IssueView,
  viewer: ViewerBlock | null,
  mine: ReadonlySet<string>,
): NeedsScope {
  if (viewer?.user_id && issue.assignee === viewer.user_id) return "mine";
  if (mine.has(issue.id) || !viewer) return "role";
  return "other";
}

const SEVERITY_RANK: Record<IssueView["severity"], number> = {
  critical: 0,
  warning: 1,
  notice: 2,
  info: 3,
};

function rank(item: NeedsItem): number {
  switch (item.kind) {
    case "issue":
      return item.issue.severity === "critical" ? -1 : 0;
    case "alert":
      return 0;
    case "decision":
      return item.decision.blocking ? 1 : 3;
    case "pr":
      return 2;
    case "claim":
      return 4;
  }
}

/** Alerts first, then blocking decisions, ready PRs, other decisions and claims; oldest first. */
function ordered(items: NeedsItem[]): NeedsItem[] {
  return [...items].sort((a, b) => rank(a) - rank(b) || a.at - b.at);
}

/**
 * Everything waiting on a human, split by whom it waits on.
 *
 * @param viewer - The snapshot's viewer block (null: every lever, one person).
 */
export function splitNeedsYou(
  snapshot: FactorySnapshot,
  viewer: ViewerBlock | null = snapshot.viewer ?? null,
  issues: NeedsIssues | null = null,
): NeedsYouSplit {
  const needs = needsYou(snapshot);
  const operator = viewer ? viewer.level >= LEVEL_EDIT : true;
  const opScope: NeedsScope = operator ? "role" : "other";
  const incidents = (issues?.data ?? []).filter(
    (i) => i.class === "incident" && (i.status === "open" || i.status === "acked"),
  );
  // An incident on a member or PR stands in for the snapshot's alert about it.
  const covered = new Set(incidents.map((i) => i.target.replace(/-/g, "").toLowerCase()));
  const alerts = needs.alerts.filter((alert) => {
    const dead = /^dead-(.+)$/.exec(alert.key);
    return !(dead && covered.has(`member:${dead[1]!.replace(/-/g, "").toLowerCase()}`));
  });
  const mineIds = new Set(issues?.mine ?? []);
  const items: NeedsItem[] = [
    ...incidents
      .slice()
      .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity])
      .map((issue): NeedsItem => ({
        kind: "issue",
        key: `issue:${issue.id}`,
        scope: issueScope(issue, viewer, mineIds),
        at: issue.first_seen,
        issue,
      })),
    ...alerts.map((alert): NeedsItem => ({
      kind: "alert",
      key: `alert:${alert.key}`,
      scope: opScope,
      at: 0,
      alert,
    })),
    ...needs.openDecisions.map((decision): NeedsItem => ({
      kind: "decision",
      key: `decision:${decision.id}`,
      scope: decisionScope(decision, viewer),
      at: decision.created_at,
      decision,
    })),
    ...needs.readyPrs.map((pr): NeedsItem => ({
      kind: "pr",
      key: `pr:${pr.id}`,
      scope: opScope,
      at: pr.ready_since ?? 0,
      pr,
    })),
    ...needs.claimMismatches.map((project): NeedsItem => ({
      kind: "claim",
      key: `claim:${project.slug}`,
      scope: opScope,
      at: project.stage_since,
      project,
    })),
  ];
  const mine = ordered(items.filter((i) => i.scope === "mine"));
  const role = ordered(items.filter((i) => i.scope === "role"));
  const other = ordered(items.filter((i) => i.scope === "other"));
  return { mine, role, all: [...mine, ...role, ...other] };
}

export type NeedsTab = "mine" | "role" | "all";

/** The tab to open on: mine when something is mine, else my role's, else all. */
export function defaultNeedsTab(split: NeedsYouSplit): NeedsTab {
  if (split.mine.length > 0) return "mine";
  if (split.role.length > 0) return "role";
  return "all";
}

/** Open decisions waiting on the viewer or their role (the "Decisions n" count). */
export function myDecisionCount(split: NeedsYouSplit): number {
  return [...split.mine, ...split.role].filter((item) => item.kind === "decision").length;
}
