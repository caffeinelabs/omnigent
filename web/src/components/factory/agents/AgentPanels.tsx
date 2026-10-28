/**
 * The Agent page's tabs: Live (server-observed activity, newest first),
 * Tool calls, Transcript (the chat renderer, read-only), Terminal (read
 * attach; typing is the creator's), Diff (the session's PR diff), Mail in
 * and out, Task & loop, and Cost by day. `?at=` (from the Timeline) marks
 * the moment on Live and Tool calls.
 */

import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowDownLeftIcon,
  ArrowUpRightIcon,
  BrainIcon,
  CircleXIcon,
  MessageSquareIcon,
  UserIcon,
  WrenchIcon,
} from "lucide-react";
import { BubbleView, bubbleKey } from "@/components/chat/chatBubbleParts";
import { TerminalView } from "@/components/blocks/TerminalView";
import { Button } from "@/components/ui/button";
import { findAgentTerminal, useTerminals } from "@/hooks/useTerminals";
import type {
  AgentDetailResponse,
  AgentToolCallView,
  FactorySnapshot,
  LiveLineView,
  LoopView,
} from "@/lib/factoryApi";
import { itemsToBlocks } from "@/lib/itemsToBlocks";
import { buildBubbles, foldFactoryHeartbeats } from "@/lib/renderItems";
import { Link } from "@/lib/routing";
import { fetchSessionItemsPage, flattenSessionItem } from "@/lib/sessionsApi";
import { cn } from "@/lib/utils";
import { GithubPanel } from "@/shell/GithubPanel";
import { FactoryLoadError } from "../FactoryLoadError";
import { useFactoryCost } from "./opsQueries";
import { AGENT_TABS, type AgentTab, prPageHref } from "../factoryRoutes";
import { formatElapsed, formatUsd, plural } from "../factoryFormat";
import { LoopStrip } from "../loops/LoopStrip";
import { loopLines } from "../loops/loopStripModel";
import { clock } from "../overview/LiveFeed";
import { EmptyState } from "../project/projectParts";
import { LEVER_REASON, useViewerCan } from "../shell/viewerCan";

export const TAB_LABEL: Record<AgentTab, string> = {
  live: "Live",
  tools: "Tool calls",
  transcript: "Transcript",
  terminal: "Terminal (read)",
  diff: "Diff",
  mail: "Mail",
  task: "Task & loop",
  cost: "Cost",
};

export function AgentTabBar({
  tab,
  onTab,
  detail,
}: {
  tab: AgentTab;
  onTab: (tab: AgentTab) => void;
  detail: AgentDetailResponse | undefined;
}) {
  const mailIn = detail?.mail.filter((m) => m.direction === "in").length ?? 0;
  const mailOut = detail ? detail.mail.length - mailIn : 0;
  return (
    <div
      role="tablist"
      aria-label="Agent"
      data-testid="agent-tabs"
      className="-mb-px flex min-w-0 items-center gap-3 overflow-x-auto border-b border-border [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      {AGENT_TABS.map((key) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={tab === key}
          data-testid={`agent-tab-${key}`}
          onClick={() => onTab(key)}
          className={cn(
            "relative inline-flex h-8 shrink-0 cursor-pointer items-center gap-1 px-1 text-ui font-medium whitespace-nowrap focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
            "after:absolute after:inset-x-1 after:-bottom-px after:h-0.5 after:rounded-full after:bg-foreground after:opacity-0",
            tab === key
              ? "text-foreground after:opacity-100"
              : "text-foreground/60 hover:text-foreground dark:text-muted-foreground",
          )}
        >
          {TAB_LABEL[key]}
          {key === "mail" && detail && (
            <span className="text-xs font-normal text-muted-foreground tabular-nums">
              in {mailIn} / out {mailOut}
            </span>
          )}
          {key === "tools" && detail && detail.tools.length > 0 && (
            <span className="text-xs font-normal text-muted-foreground tabular-nums">
              {detail.tools.length}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

function Panel({
  children,
  testId,
  className,
}: {
  children: ReactNode;
  testId: string;
  className?: string;
}) {
  return (
    <div
      role="tabpanel"
      data-testid={testId}
      className={cn("min-w-0 rounded-xl border border-border bg-card", className)}
    >
      {children}
    </div>
  );
}

/** Index of the entry nearest `atMs` among times in epoch seconds. */
function nearest(times: number[], atMs: number | null): number {
  if (atMs == null || times.length === 0) return -1;
  let best = 0;
  for (let i = 1; i < times.length; i += 1) {
    if (Math.abs(times[i]! * 1000 - atMs) < Math.abs(times[best]! * 1000 - atMs)) best = i;
  }
  return best;
}

function useScrollTo(index: number) {
  const ref = useRef<HTMLLIElement | null>(null);
  useEffect(() => {
    if (index >= 0) ref.current?.scrollIntoView?.({ block: "center" });
  }, [index]);
  return ref;
}

const LINE_ICON: Record<LiveLineView["kind"], typeof WrenchIcon> = {
  tool: WrenchIcon,
  result: CircleXIcon,
  message: MessageSquareIcon,
  thinking: BrainIcon,
  human: UserIcon,
};

export function LivePanel({ lines, atMs }: { lines: LiveLineView[]; atMs: number | null }) {
  const newest = useMemo(() => [...lines].reverse(), [lines]);
  const mark = nearest(
    newest.map((l) => l.ts),
    atMs,
  );
  const ref = useScrollTo(mark);
  if (newest.length === 0) {
    return (
      <Panel testId="agent-panel-live" className="p-4">
        <p className="text-sm text-muted-foreground">
          Nothing observed yet. Tool calls, replies and thinking show here as they happen.
        </p>
      </Panel>
    );
  }
  return (
    <Panel testId="agent-panel-live" className="max-h-[min(70vh,44rem)] overflow-y-auto p-2">
      <ol className="flex flex-col">
        {newest.map((line, i) => {
          const Icon = LINE_ICON[line.kind];
          return (
            <li
              key={line.id}
              ref={i === mark ? ref : undefined}
              data-testid={`agent-line-${line.kind}`}
              data-marked={i === mark ? "true" : undefined}
              className={cn(
                "grid min-w-0 grid-cols-[4.25rem_1rem_minmax(0,1fr)] items-baseline gap-x-2 rounded-md px-2 py-1 text-sm",
                i === mark && "bg-warning/10 ring-1 ring-warning/40",
              )}
            >
              <span className="font-mono text-xs whitespace-nowrap text-muted-foreground tabular-nums">
                {clock(line.ts * 1000)}
              </span>
              <Icon
                aria-hidden
                className={cn(
                  "size-3.5 self-center",
                  line.failed ? "text-destructive" : "text-muted-foreground",
                )}
              />
              <span
                className={cn(
                  "min-w-0 break-words",
                  line.kind === "tool" && "font-mono text-xs",
                  line.kind === "thinking" && "text-muted-foreground italic",
                  line.kind === "result" && "font-mono text-xs text-destructive",
                  line.kind === "human" && "font-medium",
                )}
              >
                {line.text}
              </span>
            </li>
          );
        })}
      </ol>
    </Panel>
  );
}

function duration(call: AgentToolCallView): string {
  if (call.duration_s == null) return "running";
  return call.duration_s < 60
    ? `${Math.round(call.duration_s)}s`
    : (formatElapsed(call.duration_s) ?? "—");
}

export function ToolsPanel({ tools, atMs }: { tools: AgentToolCallView[]; atMs: number | null }) {
  const mark = nearest(
    tools.map((t) => t.started_at),
    atMs,
  );
  const ref = useScrollTo(mark);
  if (tools.length === 0) {
    return (
      <Panel testId="agent-panel-tools" className="p-4">
        <p className="text-sm text-muted-foreground">No tool calls in its recent items.</p>
      </Panel>
    );
  }
  return (
    <Panel testId="agent-panel-tools" className="max-h-[min(70vh,44rem)] overflow-y-auto p-2">
      <ol className="flex flex-col">
        {tools.map((call, i) => (
          <li
            key={call.call_id}
            ref={i === mark ? ref : undefined}
            data-testid="agent-tool-call"
            data-failed={call.failed ? "true" : "false"}
            className={cn(
              "rounded-md px-2 py-1",
              i === mark && "bg-warning/10 ring-1 ring-warning/40",
            )}
          >
            <details className="group min-w-0">
              <summary className="grid min-w-0 cursor-pointer list-none grid-cols-[4.25rem_minmax(3.5rem,auto)_minmax(0,1fr)_auto] items-baseline gap-x-2 text-sm">
                <span className="font-mono text-xs text-muted-foreground tabular-nums">
                  {clock(call.started_at * 1000)}
                </span>
                <span className="font-medium">{call.tool}</span>
                <span
                  className="min-w-0 truncate font-mono text-xs text-muted-foreground"
                  title={call.arg ?? undefined}
                >
                  {call.arg ?? ""}
                </span>
                <span
                  className={cn(
                    "shrink-0 text-xs tabular-nums",
                    call.failed ? "text-destructive" : "text-muted-foreground",
                  )}
                >
                  {call.failed ? "✕ " : call.ended_at != null ? "✓ " : ""}
                  {duration(call)}
                </span>
              </summary>
              {call.output_head && (
                <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-muted/60 p-2 font-mono text-xs whitespace-pre-wrap">
                  {call.output_head}
                </pre>
              )}
            </details>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

const TRANSCRIPT_ITEMS = 100;

export function TranscriptPanel({
  sessionId,
  sessionUrl,
}: {
  sessionId: string;
  sessionUrl: string;
}) {
  const items = useQuery({
    queryKey: ["factory-agent-transcript", sessionId],
    queryFn: ({ signal }) => fetchSessionItemsPage(sessionId, { limit: TRANSCRIPT_ITEMS, signal }),
    staleTime: 15_000,
    retry: 1,
  });
  const bubbles = useMemo(
    () =>
      items.data
        ? foldFactoryHeartbeats(
            buildBubbles(itemsToBlocks(items.data.items.map(flattenSessionItem)), null),
          )
        : [],
    [items.data],
  );
  return (
    <Panel testId="agent-panel-transcript" className="p-4 max-md:p-3">
      <div className="mb-3 flex min-w-0 items-center gap-2 text-sm text-muted-foreground">
        <span>Read-only · the last {TRANSCRIPT_ITEMS} items</span>
        <Link
          to={sessionUrl}
          componentId="factory.agent.transcript_open"
          className="ml-auto hover:text-foreground hover:underline"
        >
          Full session →
        </Link>
      </div>
      {items.isLoading && (
        <div
          aria-busy="true"
          aria-label="Loading the transcript"
          className="h-40 animate-pulse rounded-lg bg-muted"
        />
      )}
      {items.error && (
        <FactoryLoadError
          error={items.error}
          updatedAt={0}
          onRetry={() => void items.refetch()}
          what="the transcript"
          className="mb-0"
        />
      )}
      {items.data && bubbles.length === 0 && (
        <p className="text-sm text-muted-foreground">No messages yet.</p>
      )}
      {bubbles.length > 0 && (
        <div data-testid="agent-transcript" className="flex min-w-0 flex-col gap-4">
          {bubbles.map((bubble) => (
            <BubbleView key={bubbleKey(bubble)} bubble={bubble} />
          ))}
        </div>
      )}
    </Panel>
  );
}

export function TerminalPanel({ sessionId }: { sessionId: string }) {
  const { can } = useViewerCan();
  const { terminals, isLoading, error } = useTerminals(sessionId);
  const [typing, setTyping] = useState(false);
  const terminal = findAgentTerminal(terminals) ?? terminals[0] ?? null;
  return (
    <Panel testId="agent-panel-terminal" className="flex flex-col gap-2 p-3">
      <div className="flex min-w-0 items-center gap-2 text-sm text-muted-foreground">
        <span className="min-w-0 truncate">
          {terminal ? `${terminal.name} · ${terminal.running ? "running" : "stopped"}` : "Terminal"}{" "}
          · {typing ? "you are typing into it" : "read-only"}
        </span>
        {terminal && (
          <Button
            size="xs"
            variant={typing ? "secondary" : "outline"}
            className="ml-auto"
            disabled={!can.terminal}
            title={can.terminal ? undefined : LEVER_REASON.terminal}
            onClick={() => setTyping((v) => !v)}
            data-testid="agent-terminal-write"
            componentId="factory.agent.terminal_write"
          >
            {typing ? "Stop typing" : "Type into it"}
          </Button>
        )}
      </div>
      {isLoading && (
        <div
          aria-busy="true"
          aria-label="Loading terminals"
          className="h-64 animate-pulse rounded-lg bg-muted"
        />
      )}
      {error && (
        <p className="text-sm text-muted-foreground">
          Its runner is offline; the terminal shows once it is back.
        </p>
      )}
      {!isLoading && !error && !terminal && (
        <p data-testid="agent-terminal-none" className="text-sm text-muted-foreground">
          This agent has no terminal (SDK harnesses run without one).
        </p>
      )}
      {terminal && (
        <div className="h-[min(60vh,32rem)] min-h-64 overflow-hidden rounded-lg border border-border">
          <TerminalView
            key={`${terminal.id}:${typing ? "w" : "r"}`}
            sessionId={sessionId}
            terminalId={terminal.id}
            readOnly={!typing}
            focusOnConnect={typing}
            directAttachUrl={terminal.directAttachUrl}
          />
        </div>
      )}
    </Panel>
  );
}

export function DiffPanel({ sessionId }: { sessionId: string }) {
  return (
    <Panel testId="agent-panel-diff" className="min-h-64 overflow-hidden">
      <div className="h-[min(70vh,44rem)] min-h-64 overflow-hidden">
        <GithubPanel conversationId={sessionId} />
      </div>
    </Panel>
  );
}

export function MailPanel({ detail }: { detail: AgentDetailResponse }) {
  if (detail.mail.length === 0) {
    return (
      <Panel testId="agent-panel-mail" className="p-4">
        <p className="text-sm text-muted-foreground">No mail in or out in the last 7 days.</p>
      </Panel>
    );
  }
  return (
    <Panel testId="agent-panel-mail" className="max-h-[min(70vh,44rem)] overflow-y-auto p-2">
      <ol className="flex flex-col">
        {detail.mail.map((mail) => (
          <li
            key={mail.id}
            data-testid={`agent-mail-${mail.direction}`}
            className="grid min-w-0 grid-cols-[4.25rem_1rem_minmax(5rem,auto)_minmax(0,1fr)] items-baseline gap-x-2 rounded-md px-2 py-1 text-sm"
          >
            <span className="font-mono text-xs text-muted-foreground tabular-nums">
              {clock(mail.ts)}
            </span>
            {mail.direction === "in" ? (
              <ArrowDownLeftIcon
                aria-label="in"
                className="size-3.5 self-center text-session-active"
              />
            ) : (
              <ArrowUpRightIcon
                aria-label="out"
                className="size-3.5 self-center text-muted-foreground"
              />
            )}
            <span
              className="truncate font-medium"
              title={mail.peer_title ?? mail.peer_id ?? undefined}
            >
              {mail.peer_title ?? mail.peer_id ?? "—"}
            </span>
            <span className="min-w-0 truncate" title={mail.summary}>
              {mail.mail_kind && (
                <span className="mr-1.5 font-mono text-xs text-muted-foreground">
                  {mail.mail_kind}
                </span>
              )}
              {mail.kind === "message.bounced" && (
                <span className="mr-1.5 text-xs text-destructive">bounced</span>
              )}
              {mail.kind === "message.queued" && (
                <span className="mr-1.5 text-xs text-warning">queued</span>
              )}
              {mail.summary}
            </span>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

export function TaskLoopPanel({
  snapshot,
  detail,
  loops,
  spans,
}: {
  snapshot: FactorySnapshot;
  detail: AgentDetailResponse;
  loops: LoopView[];
  spans: Parameters<typeof loopLines>[1];
}) {
  const fid = snapshot.factory.id;
  if (detail.tasks.length === 0) {
    return (
      <Panel testId="agent-panel-task" className="p-4">
        <EmptyState
          title="No task bound"
          body="Workers are bound to a task by their title; leads, planners and env keepers work for the project."
        />
      </Panel>
    );
  }
  return (
    <Panel testId="agent-panel-task" className="flex flex-col gap-3 p-4 max-md:p-3">
      {detail.tasks.map((task) => {
        const own = loops.filter(
          (l) => l.task_id === task.id || (task.pr_id != null && l.pr_id === task.pr_id),
        );
        const pr = task.pr_id ? snapshot.prs.find((p) => p.id === task.pr_id) : null;
        return (
          <div
            key={task.id}
            data-testid={`agent-task-${task.ref}`}
            className="flex min-w-0 flex-col gap-2"
          >
            <h3 className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-sm">
              <span className="font-mono font-medium">{task.ref}</span>
              <span className="min-w-0 truncate">{task.title}</span>
              <span className="text-xs text-muted-foreground">
                {task.state} · {plural(task.attempts, "attempt")}
              </span>
              {pr && (
                <Link
                  to={prPageHref(fid, pr.id)}
                  componentId="factory.agent.task_pr"
                  className="font-mono text-xs hover:underline"
                >
                  !{pr.number}
                </Link>
              )}
            </h3>
            {own.length > 0 ? (
              <LoopStrip
                snapshot={snapshot}
                lines={loopLines(own, spans)}
                title={null}
                testId={`agent-task-loops-${task.ref}`}
              />
            ) : (
              <p className="text-sm text-muted-foreground">No loop reported for this task yet.</p>
            )}
          </div>
        );
      })}
    </Panel>
  );
}

/** Its project's spend by role from the cost ledger, for scale beside the agent's own. */
function ProjectCost({ factoryId, project }: { factoryId: string; project: string }) {
  const cost = useFactoryCost(factoryId, { groupBy: ["role"], project });
  const rows = (cost.data?.data ?? [])
    .filter((r) => r.priced)
    .sort((a, b) => b.cost_usd - a.cost_usd);
  if (!cost.data || rows.length === 0) return null;
  return (
    <div data-testid="agent-cost-project" className="flex min-w-0 flex-col gap-1 text-sm">
      <span className="text-xs text-muted-foreground">
        {project} by role · {formatUsd(cost.data.totals.cost_usd)} in the ledger
      </span>
      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        {rows.map((row) => (
          <li key={String(row.role)} className="tabular-nums">
            <span className="text-muted-foreground">{String(row.role || "leads")}</span>{" "}
            <span className="font-mono">{formatUsd(row.cost_usd)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function CostPanel({
  factoryId,
  detail,
}: {
  factoryId: string;
  detail: AgentDetailResponse;
}) {
  const { agent, cost_series: series } = detail;
  const max = Math.max(0.01, ...series.map((d) => d.cost_usd));
  const total = series.reduce((s, d) => s + d.cost_usd, 0);
  const turns = series.reduce((s, d) => s + d.turns, 0);
  return (
    <Panel testId="agent-panel-cost" className="flex flex-col gap-3 p-4 max-md:p-3">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs text-muted-foreground">Own</dt>
          <dd className="font-mono text-base font-semibold tabular-nums">
            {formatUsd(agent.cost_usd) ?? "–"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">With sub-agents</dt>
          <dd className="font-mono text-base tabular-nums">
            {formatUsd(agent.subtree_cost_usd) ?? "–"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Now</dt>
          <dd className="font-mono text-base tabular-nums">
            {agent.now?.usd_per_h != null ? `${agent.now.usd_per_h.toFixed(2)} $/h` : "–"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Ledger, {series.length} days</dt>
          <dd className="font-mono text-base tabular-nums">
            {formatUsd(total) ?? "–"} · {plural(turns, "turn")}
          </dd>
        </div>
      </dl>
      {series.length === 0 ? (
        <p data-testid="agent-cost-empty" className="text-sm text-muted-foreground">
          No priced turns in the ledger yet (unpriced harnesses show “–”).
        </p>
      ) : (
        <ol
          data-testid="agent-cost-series"
          className="flex h-32 items-end gap-1"
          aria-label="Cost by day"
        >
          {series.map((day) => (
            <li
              key={day.day}
              className="flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1"
              title={`${day.day}: ${formatUsd(day.cost_usd)} · ${day.turns} turns`}
            >
              <span
                className="w-full max-w-8 rounded-t-sm bg-session-active/70"
                style={{ height: `${Math.max(2, (day.cost_usd / max) * 100)}%` }}
              />
              <span className="font-mono text-[10px] text-muted-foreground">
                {day.day.slice(5)}
              </span>
            </li>
          ))}
        </ol>
      )}
      {agent.project && <ProjectCost factoryId={factoryId} project={agent.project} />}
    </Panel>
  );
}
