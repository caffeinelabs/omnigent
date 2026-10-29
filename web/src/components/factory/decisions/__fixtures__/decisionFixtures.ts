// Decision fixtures from the live run: D-2 (an agent's blocking question),
// D-3 (the supervisor's relaunch-failed decision with actions), a
// blocked-approval decision with a link option, the answered and delivered
// forms of D-2, and D-11 (a done check whose PL is gone). Every export is a
// fresh copy.

import type { DecisionView, OpenDecisionItem } from "@/lib/factoryApi";
import { LIVE_RUN_NOW_S, liveRunSnapshot } from "../../__fixtures__/liveRun";

function find(ref: string): DecisionView {
  const decision = liveRunSnapshot().decisions.find((d) => d.ref === ref);
  if (!decision) throw new Error(`no ${ref}`);
  return decision;
}

/** D-2: PL · db-version asks which table holds the DB version (blocking). */
export function agentDecision(): DecisionView {
  return find("D-2");
}

/** D-3: Sentinel's relaunch failed 3×; Restart / Relaunch / Pause / Dismiss. */
export function relaunchFailedDecision(): DecisionView {
  return find("D-3");
}

/** D-1, answered and delivered to the api-version PL. */
export function deliveredDecision(): DecisionView {
  return find("D-1");
}

/** D-2 as the answer POST returns it: answered, its mail queued, not delivered yet. */
export function answeredDecision(optionId = "a"): DecisionView {
  return {
    ...agentDecision(),
    status: "answered",
    answer_option_id: optionId,
    answer_text: null,
    answered_by: "Luka",
    answered_at: LIVE_RUN_NOW_S,
    delivered_mail_id: "mail_9a8b7c6d5e4f3a2b",
    delivered_at: null,
    delivered_to_title: "PL · db-version",
    updated_at: LIVE_RUN_NOW_S,
  };
}

/** D-2 once the mailer delivered the answer. */
export function answeredAndDelivered(): DecisionView {
  return { ...answeredDecision(), delivered_at: LIVE_RUN_NOW_S + 6 };
}

/** D-4: a sub-agent's tool approval blocks it; the supervisor links to the Inbox. */
export function blockedApprovalDecision(): DecisionView {
  return {
    ...relaunchFailedDecision(),
    id: "5b2c9d0e1f2a3b4c5d6e7f8091a2b3c4",
    ref: "D-4",
    number: 4,
    project_slug: "db-version",
    source: "blocked_approval",
    blocking: true,
    question: "ic:db-version-route waits on a tool approval for 12 min",
    context: "`pnpm -F caffeine-db migrate:status` needs your approval in the Inbox.",
    options: [
      {
        id: "open",
        label: "Open approval",
        description: null,
        recommended: true,
        action: null,
        href: "/inbox",
        target: null,
      },
      {
        id: "dismiss",
        label: "Dismiss",
        description: null,
        recommended: false,
        action: "dismiss",
        href: null,
        target: null,
      },
    ],
    created_at: LIVE_RUN_NOW_S - 12 * 60,
    updated_at: LIVE_RUN_NOW_S - 12 * 60,
  };
}

/** D-11: the supervisor's "Mark it done?" of a project whose PL already wrapped up. */
export function doneCheckDecision(): DecisionView {
  return {
    ...relaunchFailedDecision(),
    id: "7d4e1f2a3b4c5d6e7f8091a2b3c4d5e6",
    ref: "D-11",
    number: 11,
    project_slug: "docs-factory-demo-guide",
    asked_by_session_id: null,
    asked_by_title: null,
    asked_by_role: null,
    source: "done_check",
    blocking: false,
    question: "Every task of docs-factory-demo-guide is closed. Mark it done?",
    context: null,
    options: [
      {
        id: "done",
        label: "Mark done",
        description: null,
        recommended: true,
        action: "mark_done",
        href: null,
        target: "loop:4f5e6d7c8b9a0f1e2d3c4b5a69788796",
      },
      {
        id: "not_yet",
        label: "Not yet",
        description: null,
        recommended: false,
        action: "dismiss",
        href: null,
        target: "loop:4f5e6d7c8b9a0f1e2d3c4b5a69788796",
      },
    ],
    created_at: LIVE_RUN_NOW_S - 25 * 60,
    updated_at: LIVE_RUN_NOW_S - 25 * 60,
  };
}

/** D-11 answered with its PL gone: recorded, nobody to tell. */
export function answeredNobodyToTell(): DecisionView {
  return {
    ...doneCheckDecision(),
    status: "answered",
    answer_option_id: "done",
    answered_by: "Luka",
    answered_at: LIVE_RUN_NOW_S,
    delivered_mail_id: null,
    delivered_at: LIVE_RUN_NOW_S,
    delivered_to_title: null,
    nobody_to_tell: true,
    updated_at: LIVE_RUN_NOW_S,
  };
}

/** A non-blocking question that has waited 40 min (its wait time is red). */
export function lateQuestion(): DecisionView {
  return {
    ...agentDecision(),
    id: "6c3d0e1f2a3b4c5d6e7f8091a2b3c4d5",
    ref: "D-5",
    number: 5,
    blocking: false,
    question: "Should /db/version be public, like /version?",
    context: null,
    options: [
      {
        id: "yes",
        label: "Public",
        description: null,
        recommended: true,
        action: null,
        href: null,
        target: null,
      },
      {
        id: "no",
        label: "Behind auth",
        description: null,
        recommended: false,
        action: null,
        href: null,
        target: null,
      },
    ],
    created_at: LIVE_RUN_NOW_S - 40 * 60,
    updated_at: LIVE_RUN_NOW_S - 40 * 60,
  };
}

/** The Inbox's open decisions across two factories. */
export function inboxDecisionItems(): OpenDecisionItem[] {
  const atlas = { id: liveRunSnapshot().factory.id, name: "Atlas factory" };
  const docs = { id: "0d6e1c2b3a4f5e6d7c8b9a0f1e2d3c4b", name: "Docs factory" };
  return [
    { factory: atlas, decision: agentDecision() },
    { factory: atlas, decision: relaunchFailedDecision() },
    {
      factory: docs,
      decision: {
        ...lateQuestion(),
        factory_id: docs.id,
        project_slug: "docs-factory-envs",
        asked_by_title: "PL · docs-factory-envs",
      },
    },
  ];
}
