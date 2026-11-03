/**
 * Home (`/factory/:factoryId` in the new cockpit): is it OK, what needs me,
 * how is everything going. The date and one sentence built from facts, the
 * loop the factory runs (Build and Measure only while discovery is off), then
 * the projects (outcome, why, stage, team with models) beside Needs you (the
 * server's one list, answered in place with Undo), What users need and the
 * cold-start checklist; Learned this week folds under the projects. On a
 * phone Needs you comes first. `?peek=merge` opens the merge panel and
 * `?peek=agent:<handle>` an agent's panel. A server without the asks route
 * keeps today's Now card in place of Needs you.
 */

import { ExternalLinkIcon, UsersIcon } from "lucide-react";
import { useCallback, useMemo } from "react";
import { AgentPanel } from "@/components/factory/agents/AgentPanel";
import { waitingOnViewer } from "@/components/factory/agents/fleetModel";
import { type TeamMember, stateCounts, teamMember } from "@/components/factory/agents/teamsModel";
import {
  agentPageHref,
  discoveryPageHref,
  projectsPageHref,
  steerPageHref,
} from "@/components/factory/factoryRoutes";
import {
  dayBudget,
  discoveryChecklist,
  discoveryRuns,
  headlineDate,
  homeAgentRows,
  homeFacts,
  homeHeadline,
  homeLoop,
  homeProjects,
  loopLeads,
  mergeProjects,
  movingCount,
  needsView,
  usersNeed,
  waitingMergeCount,
  yourMergeCount,
} from "@/components/factory/home/homeModel";
import {
  isMissing,
  useDiscoveryHome,
  useFactoryAsks,
  useTopOpportunities,
} from "@/components/factory/home/homeQueries";
import { MergePeekBody, mergePrs } from "@/components/factory/home/MergePeek";
import { NeedsYou } from "@/components/factory/home/NeedsYou";
import { ProjectRows } from "@/components/factory/home/ProjectRows";
import { useDeferredAnswers } from "@/components/factory/home/useDeferredAnswers";
import { DiscoveryChecklist } from "@/components/factory/home/DiscoveryChecklist";
import { LearnedThisWeek } from "@/components/factory/home/LearnedThisWeek";
import { WhatUsersNeed } from "@/components/factory/home/WhatUsersNeed";
import { useFactoryDefinition } from "@/components/factory/insights/learnQueries";
import { NowCard } from "@/components/factory/overview/NowCard";
import { useFactoryStatusSummary } from "@/components/factory/overview/statusSummaryQuery";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { useViewerCan } from "@/components/factory/shell/viewerCan";
import { LoopStrip } from "@/components/factory/ui/LoopStrip";
import { Peek } from "@/components/factory/ui/Peek";
import { FactoryToastProvider } from "@/components/factory/ui/Toast";
import { Button } from "@/components/ui/button";
import { Link, useSearchParams } from "@/lib/routing";

const AGENT_PEEK = "agent:";

function HomeBody() {
  const { snapshot, nowS, clockOffsetS } = useFactoryShell();
  const fid = snapshot.factory.id;
  const [params, setParams] = useSearchParams();
  const { can } = useViewerCan();
  const asks = useFactoryAsks(fid);
  const summary = useFactoryStatusSummary(fid).data;
  const definition = useFactoryDefinition(fid).data;
  const effective = definition?.effective ?? null;
  // Discovery runs only with both switches on: this server's and the definition's.
  const discoveryOn = discoveryRuns(definition);
  const discovery = useDiscoveryHome(fid, discoveryOn);
  const opportunities = useTopOpportunities(fid, discoveryOn);
  const deferred = useDeferredAnswers(fid);
  const asksMissing = asks.error != null && isMissing(asks.error) && !asks.data;
  const asksData = asks.data?.factory_id === fid ? asks.data : undefined;

  const waitingOnMe = useMemo(
    () => waitingOnViewer(snapshot.decisions, snapshot.viewer),
    [snapshot.decisions, snapshot.viewer],
  );
  const members = useMemo(
    () =>
      homeAgentRows(snapshot).map((row) =>
        teamMember(row, snapshot.factory.root_session_id, waitingOnMe),
      ),
    [snapshot, waitingOnMe],
  );
  const counts = useMemo(() => stateCounts(members), [members]);
  const { rows, shipped } = useMemo(
    () => homeProjects(snapshot, members, nowS, mergeProjects(asksData?.asks)),
    [snapshot, members, nowS, asksData],
  );
  const moving = movingCount(snapshot);
  // The same count Needs you shows (held answers already left the list).
  const needs = asksData ? needsView(asksData.asks, "all", true, deferred.hidden).total : null;
  const headline = homeHeadline({ needs, moving, state: snapshot.factory.state });
  const facts = homeFacts({
    building: moving,
    // The viewer's own merges; every ready project only while the server has no asks route.
    waitingMerge: asksData
      ? yourMergeCount(asksData.asks)
      : asksMissing
        ? waitingMergeCount(snapshot)
        : 0,
    yourMerge: asksData != null,
    todayUsd: summary?.factory_id === fid ? summary.spend.today_usd : null,
    dayBudgetUsd: dayBudget(effective),
    usdPerH: snapshot.totals.usd_per_h,
  });
  const loop = homeLoop({
    snapshot,
    definition: effective,
    discoveryOn,
    asks: asksData?.asks,
    discovery: discovery.data,
    leads: loopLeads(snapshot, members),
    hrefs: {
      signals: discoveryPageHref(fid, "signals"),
      opportunities: discoveryPageHref(fid),
      flow: discoveryPageHref(fid, "flow"),
      projects: projectsPageHref(fid),
      impact: discoveryPageHref(fid, "impact"),
    },
  });
  const checklist = discoveryChecklist(effective, asksData?.asks, discovery.data, discoveryOn);
  const peek = params.get("peek");
  const setPeek = useCallback(
    (value: string | null) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set("peek", value);
          else next.delete("peek");
          return next;
        },
        { replace: false },
      ),
    [setParams],
  );
  const openAgent = (m: TeamMember) => setPeek(`${AGENT_PEEK}${m.row.handle ?? m.row.session_id}`);
  const agentRef = peek?.startsWith(AGENT_PEEK)
    ? peek.slice(AGENT_PEEK.length).toLowerCase()
    : null;
  const agent = agentRef
    ? (members.find((m) => m.row.handle === agentRef || m.row.session_id === agentRef) ?? null)
    : null;
  const users = usersNeed(opportunities.data);
  const betsForYou = new Set(
    (asksData?.asks ?? []).flatMap((a) =>
      a.kind === "bet" && a.opportunity ? [a.opportunity] : [],
    ),
  );

  const needsCard = asksMissing ? (
    <NowCard factoryId={fid} nowS={nowS} snapshot={snapshot} clockOffsetS={clockOffsetS} />
  ) : (
    <NeedsYou
      asks={asksData}
      loading={asks.isPending}
      error={asks.error}
      updatedAt={asks.dataUpdatedAt}
      onRetry={() => void asks.refetch()}
      nowS={nowS}
      moving={moving}
      canAnswer={can.answer}
      deferred={deferred}
      onPeek={setPeek}
    />
  );

  return (
    <div data-testid="factory-home" className="flex min-w-0 flex-col gap-4">
      <header className="min-w-0">
        <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {headlineDate(nowS)}
        </p>
        <h1 data-testid="home-headline" className="mt-1 text-xl font-semibold text-foreground">
          {headline}
        </h1>
        {facts.length > 0 && (
          <p data-testid="home-facts" className="text-sm text-muted-foreground">
            {facts.join(" · ")}
          </p>
        )}
      </header>
      <div className="flex min-w-0 flex-col gap-4 lg:grid lg:grid-cols-[minmax(0,2.1fr)_minmax(0,1fr)] lg:items-start">
        <LoopStrip
          className="order-2 lg:order-none lg:col-span-2"
          stages={loop.stages}
          discoveryOn={loop.discoveryOn}
          goal={loop.goal}
          goalAction={
            loop.discoveryOn
              ? {
                  label: loop.goal?.statement ? "Edit strategy" : "Set a goal",
                  to: steerPageHref(fid),
                }
              : undefined
          }
          // Only a server that runs discovery offers to turn it on.
          turnOn={
            definition?.discovery_server === true
              ? { label: "Turn on discovery", to: discoveryPageHref(fid) }
              : undefined
          }
        />
        <div className="contents lg:flex lg:min-w-0 lg:flex-col lg:gap-4">
          <ProjectRows
            className="order-3"
            factoryId={fid}
            rows={rows}
            shipped={shipped}
            counts={counts}
            onAgent={openAgent}
          />
          {loop.discoveryOn && (
            <LearnedThisWeek
              className="order-5"
              factoryId={fid}
              lines={discovery.data?.learned ?? []}
            />
          )}
        </div>
        <div className="contents lg:flex lg:min-w-0 lg:flex-col lg:gap-4">
          {checklist && <DiscoveryChecklist className="order-1" steps={checklist} />}
          <div className="order-1 min-w-0">{needsCard}</div>
          {loop.discoveryOn && (
            <WhatUsersNeed
              className="order-4"
              factoryId={fid}
              rows={opportunities.data ? users : null}
              total={
                opportunities.data
                  ? opportunities.data.filter((o) => o.band !== "parked").length
                  : null
              }
              loading={opportunities.isPending}
              defVersion={snapshot.def_version ?? null}
              betsForYou={betsForYou}
              // Hunches need a writer and a server that answers discovery (not 404).
              canHunch={can.post && discovery.data != null}
            />
          )}
        </div>
      </div>
      <Peek
        open={peek === "merge"}
        onOpenChange={(open) => !open && setPeek(null)}
        title="Merge ready pull requests"
        description="Each one waits for a person's merge."
      >
        {peek === "merge" && (
          <MergePeekBody
            snapshot={snapshot}
            prs={mergePrs(snapshot, asksData?.asks ?? [])}
            nowS={nowS}
          />
        )}
      </Peek>
      <Peek
        open={agentRef != null}
        onOpenChange={(open) => !open && setPeek(null)}
        title={
          <span className="text-sm font-normal text-muted-foreground">
            {agent ? (agent.seat ? "Leads" : (agent.row.project ?? "Agent")) : "Agent"}
          </span>
        }
        headerExtra={
          agent ? (
            <Button asChild variant="ghost" size="sm">
              <Link
                to={agentPageHref(fid, agent.row.handle ?? agent.row.session_id)}
                componentId="factory.home.peek_full_page"
              >
                Full page
                <ExternalLinkIcon aria-hidden className="size-3.5" />
              </Link>
            </Button>
          ) : undefined
        }
      >
        {agentRef != null && !agent && (
          <EmptyState
            testId="home-agent-missing"
            icon={<UsersIcon aria-hidden className="size-6 text-muted-foreground/50" />}
            title="Not in this factory"
            body={`No agent here is called “${agentRef}”, or you cannot see it.`}
          />
        )}
        {agent && (
          <AgentPanel
            factoryId={fid}
            member={agent}
            memberView={snapshot.members.find((m) => m.session_id === agent.row.session_id) ?? null}
            project={
              agent.row.project
                ? (snapshot.projects.find((p) => p.slug === agent.row.project) ?? null)
                : null
            }
            nowS={nowS}
            defVersion={snapshot.def_version ?? null}
          />
        )}
      </Peek>
    </div>
  );
}

export function HomePage() {
  return (
    <FactoryToastProvider>
      <HomeBody />
    </FactoryToastProvider>
  );
}
