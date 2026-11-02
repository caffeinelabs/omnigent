/**
 * Sample bets on projects proposals for the card stories and tests. Every
 * text is sample data; the bet sits on C-4 of the round-3 fixture.
 */

import type { BetView, ProposalView } from "@/lib/factoryApi";
import { ROOM_NOW_S, proposalInState } from "../../room/__fixtures__/roomFixtures";

/** Lead B · Sentinel in the round-3 snapshot. */
export const SENTINEL_ID = "713c96e676515bd992f5c15015101a2e";

export function sampleBet(over: Partial<BetView> = {}): BetView {
  return {
    kind: "outcome",
    goal: "G-1",
    hypothesis:
      "New users publish more in week one once they can share a draft privately first, because going live too early is what stops them (S-1042, S-1107) (sample).",
    outcome: "Users can share a draft privately before publishing (sample)",
    metric: "first_week_publish (sample)",
    expected: { direction: "up", low: 1, high: 4, unit: "pp" },
    guardrails: ["support load per 1k users stays flat (sample)"],
    flag: { key: "share-draft", convention: "kebab-case keys, off by default (sample)" },
    measurement: { method: "signal_trend", readout_days: [7, 28] },
    kill: {
      state: "lift under 0.5 pp with 2,000 new users exposed",
      date: "28 days after release",
    },
    cost: { agent_usd: 120, human_review_min: 45, risk: "low" },
    ...over,
  };
}

const CASE_AGAINST = [
  "14 of 23 accounts come from one forum thread (sample).",
  "Returning users never asked for this; the evidence is all new accounts (sample).",
  "A private link may only move the problem to the share step (sample).",
];

export type BetState = "cosigning" | "waiting" | "alone" | "applied";

/**
 * C-4 carrying a bet: `cosigning` waits for Sentinel's case against;
 * `waiting` is co-signed and waits for a person; `alone` had no other lead to
 * write one; `applied` is applied with Undo.
 */
/** One new project, product-neutral, as a bet's proposal creates it. */
const DRAFT_SHARE = {
  op: "create",
  project: {
    slug: "draft-share",
    title: "Draft share (sample)",
    repo: "example-org/example-app",
    kind: "work",
    goal: "Let a user share a private draft link before publishing (sample).",
    dod: ["A private link opens the draft for anyone who has it (sample)"],
    scope_paths: ["src/drafts/**"],
    env_needed: true,
    preview: { enabled: true, slots: 1 },
    budget_ics: 2,
    priority: 1,
  },
};

export function betProposal(state: BetState, nowS = ROOM_NOW_S): ProposalView {
  const base = proposalInState(state === "applied" ? "applied" : "pending", nowS);
  const withBet: ProposalView = {
    ...base,
    title: "create draft-share",
    ops: [DRAFT_SHARE],
    rationale: "O-12 is the top validated opportunity under G-1 (sample).",
    analysis: null,
    warnings: [],
    on_behalf_of: null,
    based_on: {},
    decided_by: state === "applied" ? "owner@example.org" : base.decided_by,
    result:
      state === "applied" ? { spawning: ["draft-share"], held: {}, why: ["draft-share"] } : null,
    opportunity: "O-12",
    bet: sampleBet(),
    case_against: null,
    cosigned_by: null,
  };
  switch (state) {
    case "cosigning":
      return { ...withBet, status: "awaiting_cosign", decision_id: null, decision_ref: null };
    case "waiting":
    case "applied":
      return { ...withBet, case_against: CASE_AGAINST, cosigned_by: SENTINEL_ID };
    case "alone":
      return {
        ...withBet,
        warnings: [
          ...withBet.warnings,
          "no_peer_lead: no other lead writes a case against; a person decides",
        ],
      };
  }
}

/** A project an opportunity asked for without a bet: requested, not measured. */
export function requestedFromOpportunity(): ProposalView {
  return {
    ...betProposal("waiting"),
    rationale: "An owner asked to plan O-7 (sample).",
    opportunity: "O-7",
    bet: null,
    case_against: null,
    cosigned_by: null,
    on_behalf_of: "owner@example.org",
  };
}
