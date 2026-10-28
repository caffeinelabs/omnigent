// What each detector rule's runbook buttons do: a control (restart, pause,
// cadence, preview), an intervention on the agent or its task, an answer to
// the loop's open decision, a link to the page that holds the fix, or a
// disabled button saying why not. Pure; one test case per rule pins it.

import type {
  DecisionView,
  FactoryControlRequest,
  FactorySnapshot,
  IssueView,
  LoopView,
  MemberView,
} from "@/lib/factoryApi";
import type { InterventionKind, InterventionTarget } from "../agents/interventionModel";
import { agentPageHref, factoryPageHref, prPageHref, projectPageHref } from "../factoryRoutes";
import type { Lever } from "../shell/viewerCan";

export type RunbookAction =
  | {
      id: string;
      label: string;
      kind: "control";
      request: FactoryControlRequest;
      lever: Lever;
      /** What the confirmation says happens. */
      confirm: string;
    }
  | {
      id: string;
      label: string;
      kind: "intervene";
      intervention: InterventionKind;
      target: InterventionTarget;
      lever: Lever;
    }
  | {
      id: string;
      label: string;
      kind: "decision";
      decision: DecisionView;
      optionId: string;
      lever: Lever;
    }
  | { id: string; label: string; kind: "link"; href: string }
  | { id: string; label: string; kind: "assign"; lever: Lever }
  | { id: string; label: string; kind: "disabled"; reason: string };

/** Everything the mapping may consult besides the issue. */
export interface RunbookContext {
  snapshot: FactorySnapshot;
  /** The factory's loops (loop rules), when loaded. */
  loops?: readonly LoopView[];
  /** An agent's task ref (`T-4`), from the fleet, when known. */
  taskOf?: (sessionId: string) => string | null;
}

const LABEL: Record<string, string> = {
  restart: "Restart",
  relaunch: "Relaunch with spec",
  pause_project: "Pause project",
  open_agent: "Open agent",
  interrupt: "Interrupt",
  redispatch: "Redispatch",
  reassign: "Reassign vendor",
  message: "Message",
  message_pl: "Message PL",
  raise_budget: "Raise budget",
  switch_vendor: "Switch vendor",
  fresh_restart: "Fresh restart",
  abandon: "Abandon",
  cadence_normal: "Cadence normal",
  open_pr: "Open PR",
  open_decision: "Open decision",
  assign: "Assign…",
  redeploy_preview: "Redeploy preview",
  open_graph: "Open graph",
  propose_rule: "Propose rule",
  propose_cadence: "Propose cadence",
  open_task: "Open task",
};

/** The loop decision's option action for a loop runbook id. */
export const LOOP_OPTION: Record<string, string> = {
  raise_budget: "raise_budget",
  switch_vendor: "switch_vendor",
  fresh_restart: "fresh_restart",
  abandon: "abandon_task",
};

export const PROPOSALS_PENDING =
  "Proposing changes from findings arrives with the Improvements page";

function norm(id: string): string {
  return id.replace(/-/g, "").toLowerCase();
}

/** The issue's target, split: `member:<id>` → `["member", id]`. */
export function issueTarget(issue: Pick<IssueView, "target">): [string, string] {
  const at = issue.target.indexOf(":");
  return at === -1 ? [issue.target, ""] : [issue.target.slice(0, at), issue.target.slice(at + 1)];
}

export function memberOf(snapshot: FactorySnapshot, id: string): MemberView | null {
  return snapshot.members.find((m) => norm(m.session_id) === norm(id)) ?? null;
}

/** The agent an issue is about (its target, or a loop's worker is unknown). */
export function issueMember(snapshot: FactorySnapshot, issue: IssueView): MemberView | null {
  const [kind, id] = issueTarget(issue);
  return kind === "member" ? memberOf(snapshot, id) : null;
}

function interventionTarget(member: MemberView, taskRef: string | null): InterventionTarget {
  return {
    sessionId: member.session_id,
    title: member.title,
    role: member.role,
    project: member.project,
    taskRef,
    harness: member.harness,
  };
}

/** The PL of a project, as a member. */
function projectLead(snapshot: FactorySnapshot, slug: string | null): MemberView | null {
  if (!slug) return null;
  const pl = snapshot.projects.find((p) => p.slug === slug)?.pl;
  return pl ? memberOf(snapshot, pl.session_id) : null;
}

function topLevel(snapshot: FactorySnapshot, member: MemberView): MemberView {
  if (member.is_top_level) return member;
  const parent = member.parent_session_id ? memberOf(snapshot, member.parent_session_id) : null;
  return parent ? topLevel(snapshot, parent) : member;
}

/** The loop's open decision option for `action`: its own decision, or one whose option targets it. */
export function openLoopDecision(
  snapshot: FactorySnapshot,
  loopId: string,
  action: string,
  decisionId: string | null = null,
): { decision: DecisionView; optionId: string; label: string } | null {
  for (const decision of snapshot.decisions) {
    if (decision.status !== "open") continue;
    const own = decisionId != null && norm(decision.id) === norm(decisionId);
    const option = decision.options.find(
      (o) =>
        o.action === action &&
        (own || (o.target != null && norm(o.target) === norm(`loop:${loopId}`))),
    );
    if (option) return { decision, optionId: option.id, label: option.label };
  }
  return null;
}

/**
 * The buttons of `issue`'s runbook, in the rule's order.
 *
 * @example runbookActions(inc13, ctx) // Interrupt, Redispatch, Reassign vendor, Message
 */
export function runbookActions(issue: IssueView, ctx: RunbookContext): RunbookAction[] {
  const { snapshot } = ctx;
  const fid = snapshot.factory.id;
  const [kind, id] = issueTarget(issue);
  const member = kind === "member" ? memberOf(snapshot, id) : null;
  const pr = kind === "pr" ? (snapshot.prs.find((p) => norm(p.id) === norm(id)) ?? null) : null;
  const loop = kind === "loop" ? (ctx.loops?.find((l) => norm(l.id) === norm(id)) ?? null) : null;
  const project =
    issue.project_slug ??
    member?.project ??
    pr?.project_slug ??
    loop?.project_slug ??
    (kind === "project" ? id : null);
  const taskRef = member ? (ctx.taskOf?.(member.session_id) ?? null) : (loop?.task_ref ?? null);
  const out: RunbookAction[] = [];
  const no = (action: string, reason: string) =>
    out.push({ id: action, label: LABEL[action] ?? action, kind: "disabled", reason });

  for (const action of issue.runbook) {
    const label = LABEL[action] ?? action;
    switch (action) {
      case "restart":
      case "relaunch": {
        if (!member) {
          no(action, "No agent to restart");
          break;
        }
        const top = topLevel(snapshot, member);
        out.push({
          id: action,
          label,
          kind: "control",
          lever: "restart",
          request: { action, target: `member:${top.session_id}` },
          confirm:
            action === "restart"
              ? `${top.title} stops its turn and starts again on the same sandbox, re-reading its plan.`
              : `${top.title} restarts with the launch args its agent spec declares now.`,
        });
        break;
      }
      case "pause_project": {
        if (!project) {
          no(action, "No project to pause");
          break;
        }
        out.push({
          id: action,
          label,
          kind: "control",
          lever: "control",
          request: {
            action: "pause",
            target: `project:${project}`,
            options: { keep_previews: true },
          },
          confirm: `${project} stops dispatching and its running agents finish their step; previews stay up.`,
        });
        break;
      }
      case "open_agent": {
        if (member)
          out.push({
            id: action,
            label,
            kind: "link",
            href: agentPageHref(fid, member.session_id),
          });
        else no(action, "The agent is gone");
        break;
      }
      case "interrupt":
      case "message":
      case "redispatch":
      case "reassign": {
        if (!member) {
          no(action, "No agent to act on");
          break;
        }
        if ((action === "redispatch" || action === "reassign") && !taskRef) {
          no(action, `${member.title} works on no known task`);
          break;
        }
        out.push({
          id: action,
          label,
          kind: "intervene",
          intervention: action,
          target: interventionTarget(member, taskRef),
          lever: "intervene",
        });
        break;
      }
      case "message_pl": {
        const pl = projectLead(snapshot, project);
        if (!pl) {
          no(action, "The project has no PL");
          break;
        }
        out.push({
          id: action,
          label,
          kind: "intervene",
          intervention: "message",
          target: interventionTarget(pl, null),
          lever: "intervene",
        });
        break;
      }
      case "raise_budget":
      case "switch_vendor":
      case "fresh_restart":
      case "abandon": {
        const found =
          kind === "loop"
            ? openLoopDecision(snapshot, id, LOOP_OPTION[action]!, loop?.decision_id ?? null)
            : null;
        if (found) {
          out.push({
            id: action,
            label: found.label,
            kind: "decision",
            decision: found.decision,
            optionId: found.optionId,
            lever: "answer",
          });
          break;
        }
        // Without a decision, switching and restarting act on the loop's task directly.
        const pl = projectLead(snapshot, project);
        if ((action === "switch_vendor" || action === "fresh_restart") && loop?.task_ref && pl) {
          out.push({
            id: action,
            label,
            kind: "intervene",
            intervention: action === "switch_vendor" ? "reassign" : "redispatch",
            target: interventionTarget(pl, loop.task_ref),
            lever: "intervene",
          });
          break;
        }
        no(action, "No open loop decision to answer (the engine opens one in assist mode)");
        break;
      }
      case "cadence_normal":
        out.push({
          id: action,
          label,
          kind: "control",
          lever: "control",
          request: { action: "cadence", target: "factory", options: { cadence: "normal" } },
          confirm: "Leads and PLs wake on the normal heartbeat cadence (fewer turns, lower spend).",
        });
        break;
      case "open_pr":
        if (pr) out.push({ id: action, label, kind: "link", href: prPageHref(fid, pr.id) });
        else no(action, "The PR is no longer tracked");
        break;
      case "open_decision": {
        const decision =
          kind === "decision"
            ? snapshot.decisions.find((d) => norm(d.id) === norm(id) || d.ref === id)
            : undefined;
        out.push({
          id: action,
          label,
          kind: "link",
          href: `${factoryPageHref(fid)}?decision=${encodeURIComponent(decision?.ref ?? id)}#needs-you`,
        });
        break;
      }
      case "assign":
        out.push({ id: action, label, kind: "assign", lever: "control" });
        break;
      case "redeploy_preview": {
        if (!pr || !pr.project_slug) {
          no(action, "No open PR to redeploy");
          break;
        }
        out.push({
          id: action,
          label,
          kind: "control",
          lever: "review",
          request: {
            action: "preview",
            target: `project:${pr.project_slug}`,
            options: { mode: "up", pr: pr.number },
          },
          confirm: `The PL redeploys the preview of !${pr.number} at its head.`,
        });
        break;
      }
      case "open_graph":
        out.push({
          id: action,
          label,
          kind: "link",
          href: `${factoryPageHref(fid)}/graph?kind=bus`,
        });
        break;
      case "open_task":
        out.push({
          id: action,
          label,
          kind: "link",
          href: project
            ? projectPageHref(fid, project, { section: "tasks" })
            : factoryPageHref(fid),
        });
        break;
      case "propose_rule":
      case "propose_cadence":
        no(action, PROPOSALS_PENDING);
        break;
      default:
        no(action, "This build doesn’t know that action yet");
    }
  }
  return out;
}
