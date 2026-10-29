// Pure model of the Improvements page: the tabs' counts, which proposals each
// tab lists, a trial's progress, the change lines a definition proposal makes,
// where an evidence id links, and the "Copy as issue" text of a code
// proposal while the Smith lane is off.

import type { ProposalView, ScorecardView } from "@/lib/factoryApi";
import { formatElapsed } from "../factoryFormat";
import {
  alertPageHref,
  decisionsPageHref,
  improvementPageHref,
  prPageHref,
  projectPageHref,
} from "../factoryRoutes";
import { formatValue, goodDirection, metricLabel } from "../insights/insightsModel";

export interface ImprovementCounts {
  trial: number;
  proposed: number;
  kept: number;
  reverted: number;
  history: number;
}

const PROPOSED = new Set(["pending", "awaiting_cosign"]);

export function improvementCounts(proposals: readonly ProposalView[]): ImprovementCounts {
  return {
    trial: proposals.filter((p) => p.status === "trial").length,
    proposed: proposals.filter((p) => PROPOSED.has(p.status)).length,
    kept: proposals.filter((p) => p.status === "kept").length,
    reverted: proposals.filter((p) => p.status === "reverted").length,
    history: proposals.length,
  };
}

/** On trial, soonest end first. */
export function onTrial(proposals: readonly ProposalView[]): ProposalView[] {
  return proposals
    .filter((p) => p.status === "trial")
    .sort((a, b) => (a.trial_until ?? Infinity) - (b.trial_until ?? Infinity));
}

/** Waiting on someone: pending (owners) and awaiting a peer lead's co-sign; oldest first. */
export function proposed(proposals: readonly ProposalView[]): ProposalView[] {
  return proposals.filter((p) => PROPOSED.has(p.status)).sort((a, b) => a.number - b.number);
}

export interface TrialProgress {
  elapsedH: number;
  totalH: number;
  pct: number;
  /** Unattended trials revert at twice the window. */
  overdue: boolean;
}

export function trialProgress(proposal: ProposalView, nowS: number): TrialProgress | null {
  if (proposal.applied_at == null || proposal.trial_until == null) return null;
  const total = Math.max(1, proposal.trial_until - proposal.applied_at);
  const elapsed = Math.max(0, nowS - proposal.applied_at);
  return {
    elapsedH: Math.floor(elapsed / 3600),
    totalH: Math.round(total / 3600),
    pct: Math.min(100, (elapsed / total) * 100),
    overdue: nowS > proposal.trial_until,
  };
}

function plain(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  const rule = value as { id?: string; role?: string; project?: string; text?: string };
  if (typeof rule.text === "string") {
    const scope = [
      rule.role ? `role=${rule.role}` : null,
      rule.project && rule.project !== "*" ? `#${rule.project}` : null,
    ]
      .filter(Boolean)
      .join(" ");
    return `${scope ? `${scope} · ` : ""}“${rule.text}”`;
  }
  return JSON.stringify(value);
}

export interface ChangeLine {
  sign: "+" | "~" | "-";
  path: string;
  before: string | null;
  after: string | null;
}

/**
 * What a tuning or rules proposal changes: its `before`/`after` maps (rule ids
 * once it ran; a rule it adds is `rules.new` until then), else its ops.
 */
export function changeLines(proposal: ProposalView): ChangeLine[] {
  const before = (proposal.before ?? {}) as Record<string, unknown>;
  const after = (proposal.after ?? {}) as Record<string, unknown>;
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  if (keys.length > 0) {
    return keys.map((path) => {
      const b = before[path];
      const a = after[path];
      return {
        sign: b == null ? "+" : a == null ? "-" : "~",
        path,
        before: b == null ? null : plain(b),
        after: a == null ? null : plain(a),
      };
    });
  }
  return proposal.ops.map((op) => {
    const kind = String(op.op ?? "replace");
    return {
      sign: kind === "add" ? "+" : kind === "remove" ? "-" : "~",
      path: String(op.path ?? ""),
      before: null,
      after: kind === "remove" ? null : plain(op.value),
    };
  });
}

/** Where an evidence id opens: `F-9`, `INC-13`, `D-2`, `!9631`, `C-11`, `#slug`, `T-4`. */
export function evidenceHref(factoryId: string, id: string): string | null {
  if (/^(F|INC)-\d+$/i.test(id)) return alertPageHref(factoryId, id.toUpperCase());
  if (/^D-\d+$/i.test(id)) return decisionsPageHref(factoryId, { decision: id.toUpperCase() });
  if (/^C-\d+$/i.test(id)) return improvementPageHref(factoryId, id.toUpperCase());
  if (/^!\d+$/.test(id)) return prPageHref(factoryId, id.slice(1));
  if (/^#[a-z0-9][a-z0-9-]*$/.test(id)) return projectPageHref(factoryId, id.slice(1));
  return null;
}

/** `"heartbeat_share ↓ by 0.3"`. */
export function effectText(proposal: ProposalView): string | null {
  const effect = proposal.expected_effect;
  if (!effect) return null;
  const arrow = effect.direction === "up" ? "↑" : "↓";
  const by =
    effect.delta != null ? ` by ${metricValue(effect.metric, Math.abs(effect.delta))}` : "";
  return `${metricLabel(effect.metric)} ${arrow}${by}`;
}

/** The toast after a revert: the version now in force, or that nothing was left to undo. */
export function revertToast(proposal: ProposalView): string {
  const result = proposal.result ?? {};
  if (result.revert_changed === false) {
    return `${proposal.ref} reverted · later changes had replaced it, no new version`;
  }
  const version = result.revert_def_version;
  return typeof version === "number"
    ? `${proposal.ref} reverted · v${version} in force`
    : `${proposal.ref} reverted · parent version back in force`;
}

/** A Tier-2 reason in plain words: server setting names stay in the tooltip. */
export function tier2Plain(reason: string | null | undefined): string | null {
  if (!reason) return null;
  if (/_SMITH\b.*\bis not 1/.test(reason)) return "not switched on for this server";
  return reason
    .replace(/\s*\(OMNIGENT_[A-Z0-9_]+\)/g, "")
    .replace(/\bOMNIGENT_[A-Z0-9_]+\b/g, "a server setting");
}

/** What a trial's n counts: heartbeat hours for a cadence change, else merged PRs. */
export function nCounts(card: ScorecardView): string {
  return card.min_n >= 24 ? "heartbeat hours" : card.min_n === 1 ? "merged PR" : "merged PRs";
}

/** The scorecard's verdict as a chip: what the server would do now. */
export function verdictText(card: ScorecardView): string {
  const tail = `${card.n} of ${card.min_n} ${nCounts(card)}`;
  switch (card.outcome) {
    case "kept":
      return card.live ? `on track to keep · ${tail}` : `kept · ${tail}`;
    case "reverted":
      return card.live
        ? `a guard regressed · would revert`
        : `reverted · ${readableReason(card.reason)}`;
    default:
      return `inconclusive so far · ${readableReason(card.reason)}`;
  }
}

/** The server's reason with metric keys as people read them. */
function readableReason(reason: string): string {
  return reason.replace(/\b[a-z]+(?:_[a-z]+)+\b/g, (key) => metricLabel(key));
}

/** A guard or target value in its metric's unit. */
export function metricValue(metric: string, value: number | null | undefined): string {
  const unit =
    metric === "cost_per_merged_pr" || /(^|_)usd(_|$)/.test(metric)
      ? "usd"
      : metric === "human_prompts_per_day"
        ? "per_day"
        : metric === "incidents"
          ? "count"
          : /rate|acceptance|share|yield|agreement|efficiency/.test(metric)
            ? "share"
            : /time|latency|dwell/.test(metric)
              ? "s"
              : "";
  return formatValue(value ?? null, unit);
}

/** The target's movement against what the proposal promised. */
export function targetTone(card: ScorecardView): "pass" | "warning" | "muted" {
  if (card.target.baseline == null || card.target.trial == null) return "muted";
  return card.target.met ? "pass" : "warning";
}

/** Whether a metric moving `before → after` went the good way (for arrows). */
export function improved(
  metric: string,
  before: number | null,
  after: number | null,
): boolean | null {
  if (before == null || after == null || before === after) return null;
  const good = goodDirection(metric);
  if (good == null) return null;
  return after > before === (good === "up");
}

/** A trial or decision age, `31h` or `2d 4h`. */
export function ageShort(seconds: number): string {
  return formatElapsed(seconds) ?? "";
}

/** The markdown issue body for a code proposal while Tier 2 is off. */
export function issueText(proposal: ProposalView, factoryName: string, origin: string): string {
  const evidence = proposal.evidence.map(String);
  const lines = [
    `## ${proposal.ref}: ${proposal.title}`,
    "",
    `Proposed by ${proposal.author_title ?? proposal.author} in the ${factoryName} factory (Tier 2 is off, so the Factory Smith lane can't open this PR).`,
    "",
    "### Why",
    proposal.rationale || "—",
  ];
  if (proposal.expected_effect) {
    lines.push("", "### Expected effect", effectText(proposal) ?? "—");
  }
  if (evidence.length) {
    lines.push("", "### Evidence", ...evidence.map((e) => `- ${e}`));
  }
  lines.push("", `Cockpit: ${origin}${improvementPageHref(proposal.factory_id, proposal.ref)}`);
  return lines.join("\n");
}
