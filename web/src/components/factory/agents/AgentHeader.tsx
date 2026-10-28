/**
 * The top of an Agent page: who it is (role, model, which PL's pod, task and
 * attempt, PR, who else is watching), the NOW line (what it runs, for how
 * long, turn, spend), a banner per open issue with its runbook, and the
 * levers: Interrupt (at once; the PL is told), Redispatch, Reassign and
 * Message. Every lever reads `can.intervene`.
 */

import { Fragment, type ReactElement, type ReactNode } from "react";
import { ChevronRightIcon, ExternalLinkIcon, RadioIcon, TriangleAlertIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { AgentRowView, FactorySnapshot, IssueView, LoopView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { HarnessGlyph, ROLE_NAME } from "../board/TeamStrip";
import { TONE_CHIP, formatElapsed, formatUsd } from "../factoryFormat";
import {
  agentPageHref,
  agentsPageHref,
  alertPageHref,
  prPageHref,
  projectPageHref,
} from "../factoryRoutes";
import { RunbookButtons } from "../alerts/IssueActions";
import { PresenceFace } from "../shell/FactoryPresence";
import { LeverGated } from "../shell/viewerCan";
import { agentState, nowText } from "./fleetModel";
import type { InterventionKind } from "./interventionModel";
import type { StreamState } from "./useAgentStream";

const STATE_WORD: Record<ReturnType<typeof agentState>, string> = {
  running: "running",
  waiting: "waiting",
  idle: "idle",
  failed: "failed",
  dead: "dead",
  done: "done",
};

function Crumbs({
  factoryId,
  agent,
  parentTitle,
}: {
  factoryId: string;
  agent: AgentRowView;
  parentTitle: string | null;
}) {
  const sep = <ChevronRightIcon aria-hidden className="size-3.5 shrink-0 opacity-60" />;
  return (
    <nav aria-label="Breadcrumb" data-testid="agent-breadcrumb">
      <ol className="flex min-w-0 items-center gap-1 text-sm text-muted-foreground">
        <li className="shrink-0">
          <Link
            to={agentsPageHref(factoryId)}
            componentId="factory.agent.crumb_agents"
            className="hover:text-foreground"
          >
            Agents
          </Link>
        </li>
        {agent.project && (
          <li className="flex min-w-0 items-center gap-1">
            {sep}
            <Link
              to={projectPageHref(factoryId, agent.project, { section: "team" })}
              componentId="factory.agent.crumb_project"
              className="min-w-0 truncate font-mono hover:text-foreground"
            >
              {agent.project}
            </Link>
          </li>
        )}
        {parentTitle && agent.parent_session_id && (
          <li className="flex min-w-0 items-center gap-1 max-sm:hidden">
            {sep}
            <Link
              to={agentPageHref(factoryId, agent.parent_session_id)}
              componentId="factory.agent.crumb_parent"
              className="min-w-0 truncate hover:text-foreground"
            >
              {parentTitle}
            </Link>
          </li>
        )}
        <li className="flex min-w-0 items-center gap-1" aria-current="page">
          {sep}
          <span className="min-w-0 truncate text-foreground">{agent.title}</span>
        </li>
      </ol>
    </nav>
  );
}

/** Keyed items joined by ` · ` as flowing text, so a long line wraps between words. */
function joinDots(items: (ReactElement | false | null | undefined | "")[]): ReactNode[] {
  const shown = items.filter((item): item is ReactElement => Boolean(item));
  return shown.flatMap((item, i) =>
    i === 0 ? [item] : [<Fragment key={`dot-${String(item.key)}`}> · </Fragment>, item],
  );
}

function since(nowS: number, at: number | null | undefined): string | null {
  if (at == null) return null;
  const s = Math.max(0, nowS - at);
  return s < 60 ? `${Math.floor(s)}s` : formatElapsed(s);
}

function IssueBanner({
  factoryId,
  issue,
  nowS,
}: {
  factoryId: string;
  issue: IssueView;
  nowS: number;
}) {
  return (
    <div
      data-testid={`agent-issue-${issue.ref ?? issue.id}`}
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-lg border px-3 py-2 text-sm sm:flex-row sm:items-center",
        issue.severity === "critical"
          ? "border-destructive/40 bg-destructive/5"
          : "border-warning/40 bg-warning/5",
      )}
    >
      <span className="flex min-w-0 flex-1 items-start gap-2">
        <TriangleAlertIcon
          aria-hidden
          className={cn(
            "mt-0.5 size-3.5 shrink-0",
            issue.severity === "critical" ? "text-destructive" : "text-warning",
          )}
        />
        <span className="min-w-0">
          <Link
            to={alertPageHref(factoryId, issue.ref ?? issue.id)}
            componentId="factory.agent.issue"
            className="mr-1.5 font-mono font-medium hover:underline"
          >
            {issue.ref}
          </Link>
          <span className="break-words">{issue.summary}</span>
          {issue.body && (
            <span className="block truncate text-muted-foreground" title={issue.body}>
              {issue.body}
            </span>
          )}
          <span className="text-xs text-muted-foreground">
            {" "}
            · {since(nowS, issue.first_seen)} · {issue.status}
          </span>
        </span>
      </span>
      <RunbookButtons issue={issue} className="shrink-0 sm:justify-end" />
    </div>
  );
}

export function AgentHeader({
  snapshot,
  agent,
  issues,
  loop,
  nowS,
  stream,
  onLever,
  interrupting,
}: {
  snapshot: FactorySnapshot;
  agent: AgentRowView;
  issues: IssueView[];
  /** The agent's task loop, when it has one. */
  loop: LoopView | null;
  nowS: number;
  stream: StreamState;
  onLever: (kind: InterventionKind) => void;
  interrupting: boolean;
}) {
  const fid = snapshot.factory.id;
  const state = agentState(agent);
  const parent = agent.parent_session_id
    ? snapshot.members.find((m) => m.session_id === agent.parent_session_id)
    : null;
  const watchers = (snapshot.presence ?? []).filter(
    (p, i, all) =>
      (p.location === `agent:${agent.session_id}` || p.sessions.includes(agent.session_id)) &&
      all.findIndex((q) => q.user_id === p.user_id) === i,
  );
  const now = agent.now;
  const tool = nowText(now);
  const pr = agent.task?.pr_id ? snapshot.prs.find((p) => p.id === agent.task!.pr_id) : null;
  const hasTask = agent.task != null;
  const budgetUsd = typeof loop?.spec.usd === "number" ? loop.spec.usd : null;
  const openIssues = issues.filter((i) => i.status !== "resolved" && i.status !== "wontfix");
  return (
    <header data-testid="agent-header" data-state={state} className="flex min-w-0 flex-col gap-2.5">
      <Crumbs factoryId={fid} agent={agent} parentTitle={parent?.title ?? null} />
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="flex min-w-0 items-center gap-2 text-xl leading-snug font-semibold">
            <HarnessGlyph harness={agent.harness} className="size-4" />
            <span className="min-w-0 truncate">{agent.title}</span>
            <span
              className={cn(
                "inline-flex h-5 shrink-0 items-center rounded-sm px-1.5 text-xs font-medium",
                state === "running"
                  ? TONE_CHIP.active
                  : state === "dead" || state === "failed"
                    ? TONE_CHIP.fail
                    : state === "waiting"
                      ? TONE_CHIP.warning
                      : TONE_CHIP.muted,
              )}
            >
              {STATE_WORD[state]}
            </span>
          </h1>
          <p data-testid="agent-meta" className="min-w-0 text-sm text-muted-foreground">
            {joinDots([
              <span key="role">{agent.role ? ROLE_NAME[agent.role] : "Agent"}</span>,
              (agent.model ?? agent.harness) && (
                <span key="model" className="font-mono text-xs">
                  {agent.model ?? agent.harness}
                </span>
              ),
              parent && <span key="pod">in {parent.title}’s pod</span>,
              agent.task && (
                <span key="task">
                  <span className="font-mono">{agent.task.ref}</span> {agent.task.title}
                </span>
              ),
              agent.loop?.kind === "implement" && agent.loop.limit != null && (
                <span
                  key="attempt"
                  className={cn(agent.loop.state === "exhausted" && "text-destructive")}
                >
                  attempt {agent.loop.iteration}/{agent.loop.limit}
                  {agent.loop.state === "exhausted" ? " ⛔" : ""}
                </span>
              ),
              pr && (
                <Link
                  key="pr"
                  to={prPageHref(fid, pr.id)}
                  componentId="factory.agent.pr"
                  className="font-mono hover:text-foreground hover:underline"
                >
                  !{pr.number}
                </Link>
              ),
            ])}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {watchers.length > 0 && (
            <span
              data-testid="agent-watchers"
              className="flex items-center -space-x-1.5"
              title={`${watchers.map((w) => w.name ?? w.user_id).join(", ")} watching`}
            >
              {watchers.slice(0, 3).map((w) => (
                <PresenceFace key={w.user_id} userId={w.user_id} idle={w.idle} />
              ))}
              <span className="pl-2.5 text-xs text-muted-foreground max-sm:hidden">watching</span>
            </span>
          )}
          <Button asChild variant="outline" size="sm">
            <Link
              to={agent.url}
              componentId="factory.agent.open_session"
              data-testid="agent-open-session"
            >
              Open session
              <ExternalLinkIcon />
            </Link>
          </Button>
        </div>
      </div>
      <div
        data-testid="agent-now"
        className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card px-3 py-2.5 lg:flex-row lg:items-center"
      >
        <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2 gap-y-0.5 text-sm">
          <span className="inline-flex shrink-0 items-center gap-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            <RadioIcon
              aria-hidden
              className={cn(
                "size-3",
                stream === "live" ? "text-session-active" : "text-muted-foreground",
              )}
            />
            Now
          </span>
          {tool ? (
            <span className="max-w-full min-w-0 flex-1 truncate font-mono text-xs" title={tool}>
              {tool}
            </span>
          ) : (
            <span className="max-w-full min-w-0 flex-1 truncate text-muted-foreground">
              {agent.summary ?? STATE_WORD[state]}
            </span>
          )}
          <span className="min-w-0 text-xs text-muted-foreground tabular-nums">
            {[
              tool && since(nowS, now?.tool_since),
              now?.turn_n != null ? `turn ${now.turn_n}` : null,
              agent.cost_usd != null
                ? `${formatUsd(agent.cost_usd)}${budgetUsd != null && loop?.spent.usd != null ? ` · task ${formatUsd(loop.spent.usd)} of ${formatUsd(budgetUsd)}` : ""}`
                : "–",
              now?.usd_per_h != null ? `${now.usd_per_h.toFixed(1)} $/h` : null,
              now?.ctx_pct != null ? `ctx ${Math.round(now.ctx_pct)}%` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </span>
        <span data-testid="agent-levers" className="flex shrink-0 flex-wrap items-center gap-1">
          <LeverGated lever="intervene">
            <Button
              size="xs"
              variant="destructive"
              loading={interrupting}
              disabled={state === "dead" || state === "done"}
              onClick={() => onLever("interrupt")}
              data-testid="agent-interrupt"
              componentId="factory.agent.interrupt"
            >
              Interrupt
            </Button>
          </LeverGated>
          <LeverGated lever="intervene">
            <Button
              size="xs"
              variant="outline"
              disabled={!hasTask}
              title={hasTask ? undefined : "Works on no task"}
              onClick={() => onLever("redispatch")}
              data-testid="agent-redispatch"
              componentId="factory.agent.redispatch"
            >
              Redispatch
            </Button>
          </LeverGated>
          <LeverGated lever="intervene">
            <Button
              size="xs"
              variant="outline"
              disabled={!hasTask}
              onClick={() => onLever("reassign")}
              data-testid="agent-reassign"
              componentId="factory.agent.reassign"
            >
              Reassign…
            </Button>
          </LeverGated>
          <LeverGated lever="intervene">
            <Button
              size="xs"
              variant="outline"
              onClick={() => onLever("message")}
              data-testid="agent-message"
              componentId="factory.agent.message"
            >
              Message
            </Button>
          </LeverGated>
        </span>
      </div>
      {openIssues.map((issue) => (
        <IssueBanner key={issue.id} factoryId={fid} issue={issue} nowS={nowS} />
      ))}
    </header>
  );
}
