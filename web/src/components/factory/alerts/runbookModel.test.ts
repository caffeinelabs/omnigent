import { describe, expect, it } from "vitest";
import type { IssueView } from "@/lib/factoryApi";
import {
  IC_ROUTE_R2,
  PL_CACHE,
  PL_DB,
  PR_9631,
  round3IssueList,
  round3LoopList,
} from "../agents/__fixtures__/opsFixtures";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { ALERT_RULES } from "./alertRules";
import { type RunbookAction, issuePr, prTarget, runbookActions } from "./runbookModel";

const snapshot = round3Snapshot();
const FID = snapshot.factory.id;
const LOOP_T4 = "814ca975587e5811a185af63317e48c8";
const D2 = snapshot.decisions.find((d) => d.ref === "D-2")!;

const TARGET: Record<string, string> = {
  "member.dead": `member:${PL_CACHE}`,
  "agent.repeat": `member:${IC_ROUTE_R2}`,
  "agent.quiet": `member:${IC_ROUTE_R2}`,
  "loop.exhausted": `loop:${LOOP_T4}`,
  "loop.thrashing": `loop:${LOOP_T4}`,
  "loop.stalled": `loop:${LOOP_T4}`,
  "spend.rate": "project:db-version",
  "pr.ready_unreviewed": `pr:${PR_9631}`,
  "decision.waiting": `decision:${D2.id}`,
  "preview.gone_ready": `pr:${PR_9631}`,
  "mail.stuck": `member:${PL_DB}`,
  "bus.pingpong": "factory",
  "bus.orphan": "factory",
  "gate.flapping": `pr:${PR_9631}`,
  "claim.mismatch": `pr:${PR_9631}`,
  // The detector names the head it flaked on.
  "ci.flaky": `pr:${PR_9631}@5e1c2d4`,
  "ask.unneeded": "factory",
  "task.unplanned": "project:db-version",
  "heartbeat.waste": "factory",
  "human.report": `member:${IC_ROUTE_R2}`,
};

function issueFor(rule: string): IssueView {
  const spec = ALERT_RULES.find((r) => r.rule === rule)!;
  return {
    ...round3IssueList()[0]!,
    rule,
    class: spec.class,
    severity: spec.severity,
    target: TARGET[rule]!,
    project_slug:
      rule.startsWith("bus.") || rule === "ask.unneeded" || rule === "heartbeat.waste"
        ? null
        : "db-version",
    runbook: spec.runbook,
  };
}

const ctx = {
  snapshot,
  loops: round3LoopList(),
  taskOf: (sid: string) => (sid === IC_ROUTE_R2 ? "T-4" : null),
};

/** `kind:what` per action, e.g. `control:restart→member:…`, `link:/factory/…`. */
function shape(action: RunbookAction): string {
  switch (action.kind) {
    case "control":
      return `control:${action.request.action}→${action.request.target}${action.request.options ? ` ${JSON.stringify(action.request.options)}` : ""}`;
    case "intervene":
      return `intervene:${action.intervention}→${action.target.title}${action.target.taskRef ? `/${action.target.taskRef}` : ""}`;
    case "decision":
      return `decision:${action.decision.ref}/${action.optionId}`;
    case "link":
      return `link:${action.href}`;
    case "assign":
      return "assign";
    case "disabled":
      return `disabled:${action.reason}`;
  }
}

const EXPECTED: Record<string, string[]> = {
  "member.dead": [
    `control:restart→member:${PL_CACHE}`,
    `control:relaunch→member:${PL_CACHE}`,
    'control:pause→project:db-version {"keep_previews":true}',
    `link:/factory/${FID}/agents/${PL_CACHE}`,
  ],
  "agent.repeat": [
    "intervene:interrupt→ic:route-r2/T-4",
    "intervene:redispatch→ic:route-r2/T-4",
    "intervene:reassign→ic:route-r2/T-4",
    "intervene:message→ic:route-r2/T-4",
  ],
  "agent.quiet": ["intervene:interrupt→ic:route-r2/T-4", "intervene:message→ic:route-r2/T-4"],
  "loop.exhausted": [
    "decision:D-31/raise",
    "decision:D-31/switch",
    "decision:D-31/fresh",
    "decision:D-31/abandon",
  ],
  "loop.thrashing": [
    "decision:D-31/raise",
    "decision:D-31/switch",
    "decision:D-31/fresh",
    "decision:D-31/abandon",
  ],
  "loop.stalled": [
    "decision:D-31/raise",
    "decision:D-31/switch",
    "decision:D-31/fresh",
    "decision:D-31/abandon",
  ],
  "spend.rate": [
    'control:pause→project:db-version {"keep_previews":true}',
    'control:cadence→factory {"cadence":"normal"}',
  ],
  "pr.ready_unreviewed": [`link:/factory/${FID}/prs/${PR_9631}`],
  "decision.waiting": [`link:/factory/${FID}?decision=D-2#needs-you`, "assign"],
  "preview.gone_ready": ['control:preview→project:db-version {"mode":"up","pr":9631}'],
  "mail.stuck": [`link:/factory/${FID}/agents/${PL_DB}`],
  "bus.pingpong": [`link:/factory/${FID}/graph?kind=bus`],
  "bus.orphan": [`link:/factory/${FID}/graph?kind=bus`],
  "gate.flapping": [`link:/factory/${FID}/prs/${PR_9631}`],
  "claim.mismatch": [`link:/factory/${FID}/prs/${PR_9631}`, "intervene:message→PL · db-version"],
  "ci.flaky": [],
  "ask.unneeded": [`link:/factory/${FID}/improvements?tab=findings`],
  "task.unplanned": [`link:/factory/${FID}/projects/db-version#tasks`],
  "heartbeat.waste": [`link:/factory/${FID}/improvements?tab=findings`],
  "human.report": [`link:/factory/${FID}/agents/${IC_ROUTE_R2}`],
};

describe("runbook mapping per rule", () => {
  it("covers every rule the detectors raise", () => {
    expect(Object.keys(EXPECTED).sort()).toEqual(ALERT_RULES.map((r) => r.rule).sort());
  });

  for (const rule of ALERT_RULES.map((r) => r.rule)) {
    it(`maps ${rule}`, () => {
      expect(runbookActions(issueFor(rule), ctx).map(shape)).toEqual(EXPECTED[rule]);
    });
  }

  it("gates each lever on the role that may pull it", () => {
    const levers = (rule: string) =>
      runbookActions(issueFor(rule), ctx).map((a) => ("lever" in a ? a.lever : null));
    expect(levers("member.dead")).toEqual(["restart", "restart", "control", null]);
    expect(levers("agent.repeat")).toEqual(["intervene", "intervene", "intervene", "intervene"]);
    expect(levers("loop.exhausted")).toEqual(["answer", "answer", "answer", "answer"]);
    expect(levers("preview.gone_ready")).toEqual(["review"]);
  });

  it("falls back to task interventions when the loop has no open decision", () => {
    const noDecision = { ...ctx, snapshot: { ...snapshot, decisions: [] } };
    expect(runbookActions(issueFor("loop.exhausted"), noDecision).map(shape)).toEqual([
      "disabled:No open loop decision to answer (the engine opens one in assist mode)",
      "intervene:reassign→PL · db-version/T-4",
      "intervene:redispatch→PL · db-version/T-4",
      "disabled:No open loop decision to answer (the engine opens one in assist mode)",
    ]);
  });

  it("reads a PR target with or without the head it names", () => {
    expect(prTarget(`pr:${PR_9631}@5e1c2d4`)).toEqual({ prId: PR_9631, sha: "5e1c2d4" });
    expect(prTarget(`pr:${PR_9631}`)).toEqual({ prId: PR_9631, sha: null });
    expect(prTarget(`member:${PL_DB}`)).toBeNull();
    expect(issuePr(snapshot, { target: `pr:${PR_9631}@5e1c2d4` })?.number).toBe(9631);
    // A flaky-CI finding still offers its PR when the rule asks for it.
    const flaky = { ...issueFor("ci.flaky"), runbook: ["open_pr"] };
    expect(runbookActions(flaky, ctx).map(shape)).toEqual([`link:/factory/${FID}/prs/${PR_9631}`]);
  });

  it("disables what it can't resolve: a gone agent, an agent with no task", () => {
    const gone = { ...issueFor("agent.repeat"), target: "member:00000000000000000000000000000000" };
    expect(runbookActions(gone, ctx).every((a) => a.kind === "disabled")).toBe(true);
    const noTask = runbookActions(issueFor("agent.repeat"), { ...ctx, taskOf: () => null }).map(
      shape,
    );
    expect(noTask[1]).toBe("disabled:ic:route-r2 works on no known task");
  });
});
