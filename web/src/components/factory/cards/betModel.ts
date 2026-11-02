/**
 * Plain words for a bet on a projects proposal (`C-n`) and the other lead's
 * case against it. The bet panel in Discovery reads the same words.
 */

import type {
  BetCostView,
  BetExpectedView,
  BetFlagView,
  BetKillView,
  BetKind,
  BetMethod,
  FactorySnapshot,
  ProposalView,
} from "@/lib/factoryApi";
import { actorName } from "./proposalModel";

export const BET_KIND_LABEL: Record<BetKind, string> = {
  outcome: "An outcome bet",
  test: "A test",
  enabler: "An enabler",
  hunch: "A hunch bet",
};

export const METHOD_LABEL: Record<BetMethod, string> = {
  controlled: "a controlled experiment",
  before_after: "before and after release",
  adoption: "adoption of the new feature",
  signal_trend: "the signal trend",
  human_verdict: "a person's verdict",
};

/** `4`, `0.01`, `2.5`: no trailing zeros, at most 4 decimals. */
export function numberText(value: number): string {
  return String(Number(value.toFixed(4)));
}

/** A signed change: `+4`, `-0.5`, `0`. */
export function signedText(value: number): string {
  const text = numberText(value);
  return value > 0 ? `+${text}` : text;
}

/**
 * `"expects up +1 to +4 pp"`, `"expects down -4 to -1 pp"`: low and high are
 * the signed predicted change.
 */
export function expectedText(expected: BetExpectedView): string {
  const [low, high] =
    expected.low <= expected.high ? [expected.low, expected.high] : [expected.high, expected.low];
  return `expects ${expected.direction} ${signedText(low)} to ${signedText(high)} ${expected.unit}`;
}

/** `"days 7 and 28 after release"`, `"day 28 after release"`. */
export function readoutText(days: readonly number[]): string {
  const sorted = [...new Set(days)].sort((a, b) => a - b);
  if (sorted.length === 0) return "no readout day";
  if (sorted.length === 1) return `day ${sorted[0]} after release`;
  const head = sorted.slice(0, -1).join(", ");
  return `days ${head} and ${sorted[sorted.length - 1]} after release`;
}

/** `"funnel-events, off by default (kebab-case keys)"` or `"none: no flags yet"`. */
export function flagText(flag: BetFlagView): string {
  if (flag.none) return `none: ${flag.none}`;
  const convention = flag.convention ? ` (${flag.convention})` : "";
  return `${flag.key ?? "not named"}, off by default${convention}`;
}

/** `"no readable funnel · by 28 days after release"`. */
export function killText(kill: BetKillView): string {
  return `${kill.state} · by ${kill.date}`;
}

/** `"$40 agent · 20 min review · low risk"`, or null without a cost. */
export function costText(cost: BetCostView | null | undefined): string | null {
  if (!cost) return null;
  const parts = [
    cost.agent_usd != null ? `$${numberText(cost.agent_usd)} agent` : null,
    cost.human_review_min != null ? `${cost.human_review_min} min review` : null,
    cost.risk ? `${cost.risk} risk` : null,
  ].filter((p): p is string => p != null);
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** `"from O-12 · under G-1"` (empty when the bet names neither). */
export function betOrigin(proposal: ProposalView): string {
  return [
    proposal.opportunity ? `from ${proposal.opportunity}` : null,
    proposal.bet?.goal ? `under ${proposal.bet.goal}` : null,
  ]
    .filter((p): p is string => p != null)
    .join(" · ");
}

/**
 * `written`: lines exist; `waiting`: the bet waits for them; `later`: the
 * other lead may still write them, which blocks nothing (a person asked for
 * the work); `missing`: no other lead will.
 */
export type CaseState = "written" | "waiting" | "later" | "missing";

/** The server's warning when no other lead writes a case against. */
export const NO_PEER_WARNING = "no_peer_lead";

export function caseState(proposal: ProposalView): CaseState {
  if (proposal.case_against && proposal.case_against.length > 0) return "written";
  if (proposal.status === "awaiting_cosign") return "waiting";
  return proposal.warnings.some((w) => w.startsWith(NO_PEER_WARNING)) ? "missing" : "later";
}

/** The case-against box's heading and its text when no lines exist. */
export function caseCopy(
  proposal: ProposalView,
  snapshot: FactorySnapshot | null,
): { heading: string; empty: string | null } {
  const state = caseState(proposal);
  if (state === "written") {
    return { heading: `Case against · ${actorName(proposal.cosigned_by, snapshot)}`, empty: null };
  }
  if (state === "waiting") {
    return {
      heading: "Case against · not written yet",
      empty: "The other lead writes up to three lines against this bet before it can be approved.",
    };
  }
  if (state === "later") {
    return {
      heading: "Case against · not written yet",
      empty: "The other lead may still add up to three lines. It does not block approval.",
    };
  }
  return {
    heading: "Case against · none",
    empty: "No other lead writes one. Read the evidence before you approve.",
  };
}

/** A bet that still waits for its case against: nobody may apply it yet. */
export function waitsForCosign(proposal: ProposalView): boolean {
  return proposal.bet != null && proposal.status === "awaiting_cosign";
}
