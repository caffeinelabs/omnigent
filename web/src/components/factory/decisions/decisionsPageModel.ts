// The Decisions page's model: filters (whom, source, project), open
// decisions grouped mine → my role → others, and the history merged from the
// snapshot's recent answers and the full list. Pure; tests pin it.

import type {
  DecisionSource,
  DecisionView,
  OpenDecisionItem,
  ProposalView,
  ViewerBlock,
} from "@/lib/factoryApi";
import { decisionScope } from "../overview/needsYouModel";

export type AssignedFilter = "all" | "me" | "role" | "owners" | "operators";

export interface DecisionFilters {
  assigned: AssignedFilter;
  source: DecisionSource | "all";
  project: string | "all";
}

export const NO_FILTERS: DecisionFilters = { assigned: "all", source: "all", project: "all" };

export const SOURCE_LABEL: Record<DecisionSource, string> = {
  agent: "Agent question",
  relaunch_failed: "Relaunch failed",
  blocked_approval: "Blocked approval",
  pr_closed: "PR closed",
  storm: "Restart storm",
  proposal: "Proposal",
  loop: "Loop",
  access_request: "Access request",
  done_check: "Done check",
};

/** Whether `decision` passes the filters, from `viewer`'s side. */
export function matchesFilters(
  decision: DecisionView,
  filters: DecisionFilters,
  viewer: ViewerBlock | null,
): boolean {
  if (filters.source !== "all" && decision.source !== filters.source) return false;
  if (filters.project !== "all" && decision.project_slug !== filters.project) return false;
  const assignee = decision.assignee ?? "operators";
  switch (filters.assigned) {
    case "all":
      return true;
    case "me":
      return decision.status === "open"
        ? decisionScope(decision, viewer) === "mine"
        : assignee === viewer?.user_id || decision.claimed_by === viewer?.user_id;
    case "role":
      return decisionScope(decision, viewer) === "role";
    case "owners":
    case "operators":
      return assignee === filters.assigned;
  }
}

export interface OpenGroups {
  mine: DecisionView[];
  role: DecisionView[];
  others: DecisionView[];
}

/** Open decisions in groups, blocking first then oldest in each. */
export function groupOpen(
  decisions: readonly DecisionView[],
  viewer: ViewerBlock | null,
): OpenGroups {
  const open = decisions
    .filter((d) => d.status === "open")
    .sort((a, b) => Number(b.blocking) - Number(a.blocking) || a.created_at - b.created_at);
  const groups: OpenGroups = { mine: [], role: [], others: [] };
  for (const decision of open) {
    const scope = decisionScope(decision, viewer);
    (scope === "mine" ? groups.mine : scope === "role" ? groups.role : groups.others).push(
      decision,
    );
  }
  return groups;
}

/**
 * This factory's closed decisions in `statuses`, newest first: the full list
 * (other factories left out), plus the snapshot's recent ones it may lack.
 */
export function historyOf(
  factoryId: string,
  statuses: readonly string[],
  listed: readonly OpenDecisionItem[] | undefined,
  recent: readonly DecisionView[],
): DecisionView[] {
  const byId = new Map<string, DecisionView>();
  for (const d of recent) if (statuses.includes(d.status)) byId.set(d.id, d);
  for (const item of listed ?? []) {
    if (item.factory.id === factoryId && statuses.includes(item.decision.status)) {
      byId.set(item.decision.id, item.decision);
    }
  }
  return [...byId.values()].sort(
    (a, b) => (b.answered_at ?? b.updated_at) - (a.answered_at ?? a.updated_at),
  );
}

/** The proposal a `proposal` decision mirrors (by `subject: proposal:C-n` or its option target). */
export function proposalOf(
  decision: DecisionView,
  proposals: readonly ProposalView[] | null | undefined,
): ProposalView | null {
  if (decision.source !== "proposal" || !proposals) return null;
  const ref = decision.subject?.startsWith("proposal:") ? decision.subject.slice(9) : null;
  const target = decision.options.find((o) => o.target?.startsWith("proposal:"))?.target?.slice(9);
  return (
    proposals.find((p) => p.decision_id === decision.id) ??
    proposals.find((p) => p.ref === ref || p.id === ref) ??
    proposals.find((p) => p.id === target) ??
    null
  );
}
