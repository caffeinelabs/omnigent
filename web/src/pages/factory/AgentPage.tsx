/**
 * One agent (`/factory/:factoryId/agents/:sid`), a lead, PL or sub-agent:
 * the NOW header with any open issue and its runbook, the levers
 * (Interrupt at once; Redispatch, Reassign and Message behind a
 * confirmation; each tells the PL), the tabs (Live, Tool calls, Transcript,
 * Terminal, Diff, Mail, Task & loop, Cost; `?tab=`) and a composer posting
 * as the viewer. `?at=<ms>` (from the Timeline) marks that moment.
 */

import { useCallback, useMemo, useState } from "react";
import { BotIcon } from "lucide-react";
import { AgentComposer, AGENT_COMPOSER_ID } from "@/components/factory/agents/AgentComposer";
import { AgentHeader } from "@/components/factory/agents/AgentHeader";
import {
  AgentTabBar,
  CostPanel,
  DiffPanel,
  LivePanel,
  MailPanel,
  TaskLoopPanel,
  TerminalPanel,
  ToolsPanel,
  TranscriptPanel,
} from "@/components/factory/agents/AgentPanels";
import { InterventionDialog, useIntervene } from "@/components/factory/agents/InterventionDialog";
import {
  type InterventionKind,
  type InterventionTarget,
  planIntervention,
} from "@/components/factory/agents/interventionModel";
import {
  isMissingRoute,
  useFactoryAgent,
  useFactoryAgents,
  useFactoryLoops,
  useLoopSpans,
} from "@/components/factory/agents/opsQueries";
import { useAgentStream } from "@/components/factory/agents/useAgentStream";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { AGENT_TABS, type AgentTab, agentsPageHref } from "@/components/factory/factoryRoutes";
import { clock } from "@/components/factory/overview/LiveFeed";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { useFactoryLive } from "@/components/factory/board/factoryLive";
import type { AgentDetailResponse, FactorySnapshot } from "@/lib/factoryApi";
import { Link, useParams, useSearchParams } from "@/lib/routing";
import { rowsFromSnapshot } from "@/components/factory/agents/fleetModel";

/** While the page is open, its data re-reads this often besides the stream's nudges. */
const AGENT_POLL_MS = 10_000;

function AgentSkeleton() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading the agent"
      data-testid="agent-loading"
      className="flex flex-col gap-3"
    >
      <div className="h-4 w-56 animate-pulse rounded bg-muted" />
      <div className="h-7 w-80 animate-pulse rounded bg-muted" />
      <div className="h-12 animate-pulse rounded-xl bg-muted" />
      <div className="h-8 animate-pulse rounded bg-muted" />
      <div className="h-72 animate-pulse rounded-xl bg-muted" />
    </div>
  );
}

function NotInFactory({ factoryId, sid }: { factoryId: string; sid: string }) {
  return (
    <EmptyState
      testId="agent-missing"
      icon={<BotIcon aria-hidden className="size-6 text-muted-foreground/50" />}
      title="No such agent in this factory"
      body={
        <>
          <span className="font-mono">{sid}</span> isn’t a member you can read.{" "}
          <Link
            to={agentsPageHref(factoryId)}
            componentId="factory.agent.back"
            className="underline underline-offset-2 hover:text-foreground"
          >
            Back to Agents
          </Link>
        </>
      }
    />
  );
}

/** The page data from the snapshot alone (a server without the fleet API). */
function detailFromSnapshot(snapshot: FactorySnapshot, sid: string): AgentDetailResponse | null {
  const row = rowsFromSnapshot(snapshot).find((r) => r.session_id === sid);
  return row
    ? { agent: row, tools: [], lines: [], mail: [], tasks: [], issues: [], cost_series: [] }
    : null;
}

export function AgentPage() {
  const { sid = "" } = useParams<{ sid: string }>();
  const { snapshot, nowS } = useFactoryShell();
  const fid = snapshot.factory.id;
  const live = useFactoryLive();
  const [params, setParams] = useSearchParams();
  const tab = (AGENT_TABS as readonly string[]).includes(params.get("tab") ?? "")
    ? (params.get("tab") as AgentTab)
    : "live";
  const atRaw = Number(params.get("at"));
  const atMs = Number.isFinite(atRaw) && atRaw > 0 ? atRaw : null;
  const setTab = useCallback(
    (next: AgentTab) =>
      setParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          if (next === "live") out.delete("tab");
          else out.set("tab", next);
          return out;
        },
        { replace: true },
      ),
    [setParams],
  );
  const query = useFactoryAgent(fid, sid, { liveMs: AGENT_POLL_MS });
  // The header's "now" follows the fleet's row, which every `now` tick refreshes.
  const fleet = useFactoryAgents(fid);
  const liveRow = fleet.data?.data.find((r) => r.session_id === sid) ?? null;
  const missing = isMissingRoute(query.error);
  const detail = useMemo(
    () => query.data ?? (query.error && !query.data ? detailFromSnapshot(snapshot, sid) : null),
    [query.data, query.error, snapshot, sid],
  );
  const stream = useAgentStream(fid, sid, { enabled: live !== false && Boolean(detail) });
  const project = detail?.agent.project ?? null;
  const loops = useFactoryLoops(fid, { project }, { enabled: Boolean(detail?.agent.task) });
  const spans = useLoopSpans(fid, project, Math.floor((nowS - 7 * 86400) / 3600) * 3_600_000, {
    enabled: tab === "task" && Boolean(detail?.agent.task),
  });
  const intervene = useIntervene(fid);
  const [acting, setActing] = useState<InterventionKind | null>(null);
  const [interrupting, setInterrupting] = useState(false);

  if (query.isLoading && !detail) return <AgentSkeleton />;
  if (!detail) {
    if (query.error && !missing && !(query.error as { status?: number }).status) {
      return (
        <FactoryLoadError
          error={query.error}
          updatedAt={0}
          onRetry={() => void query.refetch()}
          what="this agent"
        />
      );
    }
    return <NotInFactory factoryId={fid} sid={sid} />;
  }
  const agent = liveRow ?? detail.agent;
  const target: InterventionTarget = {
    sessionId: agent.session_id,
    title: agent.title,
    role: agent.role,
    project: agent.project,
    taskRef: agent.task?.ref ?? null,
    harness: agent.harness,
  };
  const taskLoop =
    (loops.data?.data ?? []).find(
      (l) => l.kind === "implement" && agent.task != null && l.task_id === agent.task.id,
    ) ?? null;
  const onLever = async (kind: InterventionKind) => {
    if (kind === "interrupt") {
      const plan = planIntervention("interrupt", [target]);
      setInterrupting(true);
      await intervene("interrupt", plan.requests, plan.labels);
      setInterrupting(false);
      return;
    }
    if (kind === "message") {
      const input = document.getElementById(AGENT_COMPOSER_ID);
      input?.scrollIntoView?.({ block: "center" });
      input?.focus();
      return;
    }
    setActing(kind);
  };
  return (
    <div
      data-testid="factory-agent-page"
      data-agent={agent.session_id}
      className="flex min-w-0 flex-col gap-3"
    >
      {query.error && !query.data && (
        <FactoryLoadError
          error={query.error}
          updatedAt={0}
          onRetry={() => void query.refetch()}
          what={missing ? "the agent API (showing the snapshot)" : "this agent"}
          testId="agent-error"
          className="mb-0"
        />
      )}
      <AgentHeader
        snapshot={snapshot}
        agent={agent}
        issues={detail.issues}
        loop={taskLoop}
        nowS={nowS}
        stream={stream}
        onLever={(kind) => void onLever(kind)}
        interrupting={interrupting}
      />
      {atMs != null && (
        <p
          data-testid="agent-at"
          className="flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground"
        >
          Showing what happened around{" "}
          <span className="font-mono text-foreground">{clock(atMs)}</span>
          <button
            type="button"
            onClick={() =>
              setParams(
                (prev) => {
                  const out = new URLSearchParams(prev);
                  out.delete("at");
                  return out;
                },
                { replace: true },
              )
            }
            className="cursor-pointer underline underline-offset-2 hover:text-foreground"
          >
            Back to live
          </button>
        </p>
      )}
      <AgentTabBar tab={tab} onTab={setTab} detail={query.data} />
      {tab === "live" && <LivePanel lines={detail.lines} atMs={atMs} />}
      {tab === "tools" && <ToolsPanel tools={detail.tools} atMs={atMs} />}
      {tab === "transcript" && (
        <TranscriptPanel sessionId={agent.session_id} sessionUrl={agent.url} />
      )}
      {tab === "terminal" && <TerminalPanel sessionId={agent.session_id} />}
      {tab === "diff" && <DiffPanel sessionId={agent.session_id} />}
      {tab === "mail" && <MailPanel detail={detail} />}
      {tab === "task" && (
        <TaskLoopPanel
          snapshot={snapshot}
          detail={detail}
          loops={loops.data?.data ?? []}
          spans={spans.data?.data ?? []}
        />
      )}
      {tab === "cost" && <CostPanel factoryId={fid} detail={detail} />}
      <AgentComposer factoryId={fid} agent={agent} />
      <InterventionDialog
        factoryId={fid}
        kind={acting}
        targets={[target]}
        onOpenChange={(open) => {
          if (!open) setActing(null);
        }}
      />
    </div>
  );
}
