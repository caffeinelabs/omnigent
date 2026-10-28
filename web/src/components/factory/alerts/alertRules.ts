// The detector rules as the Alerts page lists them (mirrors the server's
// `detectors.RULES`): class, default severity, what triggers it and the
// runbook its incidents offer. Thresholds come from the factory definition.

import type { EventSeverity, IssueClass } from "@/lib/factoryApi";

export interface AlertRule {
  rule: string;
  class: IssueClass;
  severity: EventSeverity;
  trigger: string;
  runbook: string[];
}

const LOOP_RUNBOOK = ["raise_budget", "switch_vendor", "fresh_restart", "abandon"];

export const ALERT_RULES: AlertRule[] = [
  {
    rule: "member.dead",
    class: "incident",
    severity: "critical",
    trigger: "a lead or PL is dead, or its relaunch failed",
    runbook: ["restart", "relaunch", "pause_project", "open_agent"],
  },
  {
    rule: "agent.repeat",
    class: "incident",
    severity: "warning",
    trigger: "5+ identical failing tool calls in 30 min",
    runbook: ["interrupt", "redispatch", "reassign", "message"],
  },
  {
    rule: "agent.quiet",
    class: "incident",
    severity: "warning",
    trigger: "running with no event for 10 min",
    runbook: ["interrupt", "message"],
  },
  {
    rule: "loop.exhausted",
    class: "incident",
    severity: "warning",
    trigger: "a loop used its budget",
    runbook: LOOP_RUNBOOK,
  },
  {
    rule: "loop.thrashing",
    class: "incident",
    severity: "warning",
    trigger: "a fixed review finding came back",
    runbook: LOOP_RUNBOOK,
  },
  {
    rule: "loop.stalled",
    class: "incident",
    severity: "warning",
    trigger: "no progress within the loop's window",
    runbook: LOOP_RUNBOOK,
  },
  {
    rule: "spend.rate",
    class: "incident",
    severity: "warning",
    trigger: "$/h above the alert threshold",
    runbook: ["pause_project", "cadence_normal"],
  },
  {
    rule: "pr.ready_unreviewed",
    class: "incident",
    severity: "notice",
    trigger: "ready over 4 h with no human review",
    runbook: ["open_pr"],
  },
  {
    rule: "decision.waiting",
    class: "incident",
    severity: "notice",
    trigger: "a blocking decision open over 30 min",
    runbook: ["open_decision", "assign"],
  },
  {
    rule: "preview.gone_ready",
    class: "incident",
    severity: "warning",
    trigger: "the preview of a ready PR is gone",
    runbook: ["redeploy_preview"],
  },
  {
    rule: "mail.stuck",
    class: "incident",
    severity: "warning",
    trigger: "mail queued over 10 min",
    runbook: ["open_agent"],
  },
  {
    rule: "bus.pingpong",
    class: "finding",
    severity: "notice",
    trigger: "two agents mail back and forth without progress",
    runbook: ["open_graph"],
  },
  {
    rule: "bus.orphan",
    class: "finding",
    severity: "notice",
    trigger: "a request no one answered",
    runbook: ["open_graph"],
  },
  {
    rule: "gate.flapping",
    class: "finding",
    severity: "notice",
    trigger: "ready ↔ not ready 3+ times in 2 h",
    runbook: ["open_pr"],
  },
  {
    rule: "claim.mismatch",
    class: "finding",
    severity: "notice",
    trigger: "a PL's READY claim the gates don't back",
    runbook: ["open_pr", "message_pl"],
  },
  {
    rule: "ci.flaky",
    class: "finding",
    severity: "info",
    trigger: "CI fail → pass on the same sha",
    runbook: [],
  },
  {
    rule: "ask.unneeded",
    class: "finding",
    severity: "info",
    trigger: "the recommended option picked in under 2 min, 3 times",
    runbook: ["propose_rule"],
  },
  {
    rule: "task.unplanned",
    class: "finding",
    severity: "info",
    trigger: "a worker the binder couldn't tie to a planned task",
    runbook: ["open_task"],
  },
  {
    rule: "heartbeat.waste",
    class: "finding",
    severity: "notice",
    trigger: "over $5/day of heartbeats that changed nothing",
    runbook: ["propose_cadence"],
  },
  {
    rule: "human.report",
    class: "incident",
    severity: "notice",
    trigger: "someone reported a problem",
    runbook: ["open_agent"],
  },
];

/** The rule's row, when the build knows it. */
export function alertRule(rule: string): AlertRule | null {
  return ALERT_RULES.find((r) => r.rule === rule) ?? null;
}
