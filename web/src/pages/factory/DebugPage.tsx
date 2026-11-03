/**
 * Debug (`/debug/<view>`): operator depth, one level down from the five
 * places. Today's views are kept as they are: the fleet table, the Timeline,
 * Activity, the Graph (work, bus, flow), Incidents and findings (and one
 * incident at `/debug/incidents/<ref>`), Insights, and an agent's eight tabs
 * (`/debug/agent/<session id>`). It is reached from the avatar menu, ⌘K and
 * the Health panel; the classic addresses of these pages land here.
 */

import { BugIcon, ChevronRightIcon } from "lucide-react";
import { ActivityList } from "@/components/factory/activity/ActivityList";
import { ActivityHeatmap } from "@/components/factory/overview/ActivityHeatmap";
import { LiveFeed } from "@/components/factory/overview/LiveFeed";
import { DEBUG_VIEWS, type DebugView, debugPageHref } from "@/components/factory/factoryRoutes";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { FOCUS_RING } from "@/components/factory/ui/statusTokens";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { AgentPage } from "./AgentPage";
import { AgentsPage } from "./AgentsPage";
import { AlertsPage } from "./AlertsPage";
import { GraphPage } from "./GraphPage";
import { IncidentPage } from "./IncidentPage";
import { InsightsPage } from "./InsightsPage";
import { TimelinePage } from "./TimelinePage";

export const DEBUG_LABEL: Record<DebugView, string> = {
  fleet: "Fleet table",
  timeline: "Timeline",
  activity: "Activity",
  graph: "Graph",
  incidents: "Incidents and findings",
  insights: "Insights",
  agent: "Agent depth",
};

/** The view a Debug address names (the fleet table when it names none). */
export function debugViewOf(id: string | null | undefined): DebugView {
  return (DEBUG_VIEWS as readonly string[]).includes(id ?? "") ? (id as DebugView) : "fleet";
}

/** Debug › Activity: the last hour per project and the live feed (Home's before), then every event. */
function ActivityView() {
  const { snapshot, nowS } = useFactoryShell();
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="grid min-w-0 grid-cols-1 items-start gap-4 2xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <ActivityHeatmap snapshot={snapshot} nowS={nowS} />
        <LiveFeed snapshot={snapshot} />
      </div>
      <ActivityList
        factoryId={snapshot.factory.id}
        projects={snapshot.projects}
        members={snapshot.members}
      />
    </div>
  );
}

/** Debug › Agent depth without an agent: pick one. */
function AgentPicker() {
  const { snapshot } = useFactoryShell();
  const fid = snapshot.factory.id;
  const members = [...snapshot.members].sort(
    (a, b) =>
      Number(b.is_top_level) - Number(a.is_top_level) ||
      (a.project ?? "").localeCompare(b.project ?? "") ||
      a.title.localeCompare(b.title),
  );
  if (!members.length) {
    return (
      <EmptyState
        testId="debug-agent-none"
        title="No agents in this factory yet"
        body="Each agent's transcript, tool calls, terminal, diff, mail, task and cost show here once it runs."
      />
    );
  }
  return (
    <section
      aria-label="Pick an agent"
      data-testid="debug-agent-picker"
      className="flex min-w-0 flex-col gap-2"
    >
      <p className="text-sm text-muted-foreground">
        Transcript, tool calls, terminal, diff, mail, task and cost of one agent. Pick one.
      </p>
      <ul className="flex min-w-0 flex-col divide-y divide-border rounded-xl border border-border">
        {members.map((member) => (
          <li key={member.session_id}>
            <Link
              to={debugPageHref(fid, "agent", { ref: member.session_id })}
              componentId="factory.debug.agent"
              className={cn(
                "flex min-w-0 items-center gap-2 px-3 py-2 text-sm hover:bg-muted/60",
                FOCUS_RING,
              )}
            >
              <span className="min-w-0 truncate">{member.title}</span>
              {member.project && (
                <span className="truncate font-mono text-xs text-muted-foreground">
                  #{member.project}
                </span>
              )}
              <ChevronRightIcon aria-hidden className="ml-auto size-3.5 text-muted-foreground" />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function DebugBody({ view, sub }: { view: DebugView; sub: string | null }) {
  switch (view) {
    case "fleet":
      return <AgentsPage />;
    case "timeline":
      return <TimelinePage />;
    case "activity":
      return <ActivityView />;
    case "graph":
      return <GraphPage />;
    case "incidents":
      return sub ? <IncidentPage /> : <AlertsPage />;
    case "insights":
      return <InsightsPage />;
    case "agent":
      return sub ? <AgentPage /> : <AgentPicker />;
  }
}

export function DebugPage() {
  const { snapshot, location } = useFactoryShell();
  const fid = snapshot.factory.id;
  const view = debugViewOf(location.id);
  const sub = location.sub ?? null;
  return (
    <div data-testid="factory-debug-page" data-view={view} className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <BugIcon aria-hidden className="size-5 text-muted-foreground" />
          Debug
        </h1>
        <p className="text-sm text-muted-foreground">
          Operator depth: today's views, kept intact, one level down. Reach them from ⌘K, the Health
          panel or your avatar menu.
        </p>
      </div>
      <nav
        aria-label="Debug views"
        data-testid="debug-tabs"
        className="flex min-w-0 items-center gap-3 overflow-x-auto border-b border-border [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {DEBUG_VIEWS.map((key) => {
          const on = key === view;
          return (
            <Link
              key={key}
              to={debugPageHref(fid, key)}
              componentId={`factory.debug.tab.${key}`}
              data-testid={`debug-tab-${key}`}
              aria-current={on ? "page" : undefined}
              className={cn(
                "-mb-px inline-flex h-8 shrink-0 items-center border-b-2 px-0.5 text-sm font-medium whitespace-nowrap",
                FOCUS_RING,
                on
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {DEBUG_LABEL[key]}
            </Link>
          );
        })}
      </nav>
      <DebugBody view={view} sub={sub} />
    </div>
  );
}
