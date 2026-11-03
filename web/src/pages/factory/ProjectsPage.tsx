/**
 * Projects (`/factory/:factoryId/projects`).
 *
 * The classic cockpit: a dense table by default once there are more than four
 * projects, or the cards of the round-2 board (`?view=cards|table`).
 * Environments and closed projects sit below the table.
 *
 * The new cockpit: outcome-first cards, or a board by stage (`?view=board`),
 * filtered to all, from evidence, requested, needs you or stopped (`?f=`);
 * shipped work folds under the cards with what each readout says. The table
 * lives in Debug (`?view=table` goes there).
 */

import { useMemo } from "react";
import { Navigate } from "react-router-dom";
import { LayoutGridIcon, PlusIcon, TableIcon } from "lucide-react";
import { waitingOnViewer } from "@/components/factory/agents/fleetModel";
import { stateCounts, teamMember } from "@/components/factory/agents/teamsModel";
import { FactoryBoard } from "@/components/factory/board/FactoryBoard";
import {
  debugPageHref,
  prPageHref,
  factoryPageHref,
  projectPageHref,
} from "@/components/factory/factoryRoutes";
import { homeAgentRows, mergeProjects } from "@/components/factory/home/homeModel";
import { useFactoryAsks } from "@/components/factory/home/homeQueries";
import { AgentPeek, agentPeek, usePeekParam } from "@/components/factory/projects/AgentPeek";
import { ProjectBoard } from "@/components/factory/projects/ProjectBoard";
import { ProjectCard } from "@/components/factory/projects/ProjectCard";
import {
  FILTER_LABEL,
  PROJECT_FILTERS,
  type ProjectFilter,
  boardColumns,
  filterCards,
  filterCounts,
  filterOf,
  projectCards,
} from "@/components/factory/projects/projectCards";
import { ProjectsTable } from "@/components/factory/projects/ProjectsTable";
import { EmptyState } from "@/components/factory/project/projectParts";
import { FOCUS_RING } from "@/components/factory/ui/statusTokens";
import { findPr } from "@/components/factory/shell/shellRoutes";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { jumpTo } from "@/components/factory/shell/scroll";
import { LeverGated } from "@/components/factory/shell/viewerCan";
import { Button } from "@/components/ui/button";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { Link, useNavigate, useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";

export type ProjectsView = "table" | "cards";

/** Cards for a handful of projects, the table past four. */
export function defaultProjectsView(snapshot: FactorySnapshot): ProjectsView {
  return snapshot.projects.filter((p) => p.stage !== "done").length > 4 ? "table" : "cards";
}

function ViewSwitch({
  view,
  onChange,
}: {
  view: ProjectsView;
  onChange: (v: ProjectsView) => void;
}) {
  return (
    <div role="radiogroup" aria-label="View" className="inline-flex rounded-lg bg-muted p-[3px]">
      {(
        [
          ["table", "Table", TableIcon],
          ["cards", "Cards", LayoutGridIcon],
        ] as const
      ).map(([key, label, Icon]) => (
        <button
          key={key}
          type="button"
          role="radio"
          aria-checked={view === key}
          onClick={() => onChange(key)}
          data-testid={`projects-view-${key}`}
          className={cn(
            "inline-flex h-6 cursor-pointer items-center gap-1 rounded-md px-2 text-sm font-medium focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
            view === key
              ? "bg-background text-foreground shadow-sm dark:bg-input/30"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Icon aria-hidden className="size-3.5" />
          <span className="max-sm:sr-only">{label}</span>
        </button>
      ))}
    </div>
  );
}

export function ProjectsPage() {
  const { cockpit } = useFactoryShell();
  return cockpit === "v2" ? <ProjectsPageV2 /> : <ClassicProjectsPage />;
}

function ClassicProjectsPage() {
  const { snapshot, nowS, openAddProject } = useFactoryShell();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const fid = snapshot.factory.id;
  const asked = params.get("view");
  const view: ProjectsView =
    asked === "table" || asked === "cards" ? asked : defaultProjectsView(snapshot);
  const setView = (next: ProjectsView) =>
    setParams(
      (prev) => {
        const out = new URLSearchParams(prev);
        out.set("view", next);
        return out;
      },
      { replace: true },
    );
  const addProject = (
    <LeverGated lever="apply_projects">
      <Button
        size="xs"
        variant="outline"
        onClick={openAddProject}
        data-testid="projects-add"
        componentId="factory.projects.add"
      >
        <PlusIcon />
        Project
      </Button>
    </LeverGated>
  );
  return (
    <div
      data-testid="factory-projects-page"
      data-view={view}
      className="flex min-w-0 flex-col gap-4"
    >
      {view === "table" ? (
        <ProjectsTable
          snapshot={snapshot}
          nowS={nowS}
          variant="full"
          actions={
            <>
              <ViewSwitch view={view} onChange={setView} />
              {addProject}
            </>
          }
        />
      ) : (
        <>
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h2 className="text-ui font-semibold">Projects</h2>
            <span className="ml-auto flex items-center gap-2">
              <ViewSwitch view={view} onChange={setView} />
              {addProject}
            </span>
          </div>
          <FactoryBoard
            snapshot={snapshot}
            nowS={nowS}
            onReview={(number) =>
              navigate(prPageHref(fid, findPr(snapshot.prs, String(number))?.id ?? number))
            }
            onDecision={(ref) =>
              setParams((prev) => {
                const out = new URLSearchParams(prev);
                out.set("decision", ref);
                return out;
              })
            }
            onDecisions={() => navigate(`${factoryPageHref(fid)}#needs-you`)}
            onJump={jumpTo}
          />
        </>
      )}
    </div>
  );
}

// ── The new cockpit ────────────────────────────────────────────

type CardsOrBoard = "cards" | "board";

function CardsBoardSwitch({
  view,
  onChange,
}: {
  view: CardsOrBoard;
  onChange: (v: CardsOrBoard) => void;
}) {
  return (
    <div role="group" aria-label="View" className="inline-flex rounded-lg bg-muted p-[3px]">
      {(
        [
          ["cards", "Cards", LayoutGridIcon],
          ["board", "Board", TableIcon],
        ] as const
      ).map(([key, label, Icon]) => (
        <button
          key={key}
          type="button"
          aria-pressed={view === key}
          onClick={() => onChange(key)}
          data-testid={`projects-view-${key}`}
          className={cn(
            "inline-flex h-7 cursor-pointer items-center gap-1 rounded-md px-2 text-sm font-medium",
            FOCUS_RING,
            view === key
              ? "bg-background text-foreground shadow-sm dark:bg-input/30"
              : "text-foreground/75 hover:text-foreground",
          )}
        >
          <Icon aria-hidden className="size-3.5" />
          {label}
        </button>
      ))}
    </div>
  );
}

function ProjectsPageV2() {
  const { snapshot, nowS, openAddProject } = useFactoryShell();
  const [params, setParams] = useSearchParams();
  const [, setPeek] = usePeekParam();
  const fid = snapshot.factory.id;
  const asks = useFactoryAsks(fid);
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
  const { cards, shipped } = useMemo(
    () => projectCards(snapshot, members, nowS, mergeProjects(asksData?.asks)),
    [snapshot, members, nowS, asksData],
  );
  const asked = params.get("view");
  const view: CardsOrBoard = asked === "board" ? "board" : "cards";
  const filter = filterOf(params.get("f"));
  const counts = filterCounts(cards);
  const shown = filterCards(cards, filter);
  const agentsByState = stateCounts(members);
  const set = (key: string, value: string | null) =>
    setParams(
      (prev) => {
        const out = new URLSearchParams(prev);
        if (value) out.set(key, value);
        else out.delete(key);
        return out;
      },
      { replace: true },
    );
  if (asked === "table") {
    // The table moved one level down, to Debug.
    return <Navigate to={debugPageHref(fid, "projects")} replace />;
  }
  const onAgent = (member: Parameters<typeof agentPeek>[0]) => setPeek(agentPeek(member));
  return (
    <div
      data-testid="factory-projects-page"
      data-view={view}
      className="flex min-w-0 flex-col gap-4"
    >
      <header className="flex min-w-0 flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 grow">
          <h1 className="text-xl font-semibold">Projects</h1>
          <p className="text-sm text-muted-foreground">
            Named by what users get. Each says why it exists, or says plainly that it has no user
            evidence.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <CardsBoardSwitch view={view} onChange={(v) => set("view", v === "cards" ? null : v)} />
          <LeverGated lever="apply_projects">
            <Button
              size="sm"
              variant="outline"
              onClick={openAddProject}
              data-testid="projects-add"
              componentId="factory.projects.add"
            >
              <PlusIcon />
              Add project
            </Button>
          </LeverGated>
        </div>
      </header>
      <div
        role="group"
        aria-label="Filter projects"
        className="flex flex-wrap items-center gap-1.5"
      >
        {PROJECT_FILTERS.map((f: ProjectFilter) => (
          <button
            key={f}
            type="button"
            aria-pressed={filter === f}
            data-testid={`projects-filter-${f}`}
            onClick={() => set("f", f === "all" ? null : f)}
            className={cn(
              "inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-full border px-2.5 text-sm",
              FOCUS_RING,
              filter === f
                ? "border-foreground/60 bg-foreground/5 font-semibold text-foreground"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            {FILTER_LABEL[f]}
            <span className="text-xs tabular-nums">{counts[f]}</span>
          </button>
        ))}
        <span className="grow" />
        <span className="text-xs text-muted-foreground" data-testid="projects-agent-counts">
          {[
            agentsByState.working ? `${agentsByState.working} working` : null,
            agentsByState.waiting ? `${agentsByState.waiting} waiting on you` : null,
            agentsByState.stuck ? `${agentsByState.stuck} stuck` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </div>
      {view === "board" ? (
        <ProjectBoard
          factoryId={fid}
          columns={boardColumns(shown, filter === "all" ? shipped : [])}
          stopped={shown.filter((c) => c.broken)}
          onAgent={onAgent}
        />
      ) : shown.length === 0 ? (
        <EmptyState
          testId="projects-empty"
          title={cards.length === 0 ? "No project is in progress" : "No project matches"}
          body={
            cards.length === 0 ? (
              "Add one, or let discovery propose one."
            ) : (
              <button
                type="button"
                className={cn("underline underline-offset-2", FOCUS_RING)}
                onClick={() => set("f", null)}
              >
                Show all projects
              </button>
            )
          }
        />
      ) : (
        <div data-testid="project-cards" className="grid min-w-0 grid-cols-1 gap-3 lg:grid-cols-2">
          {shown.map((card) => (
            <ProjectCard key={card.slug} factoryId={fid} card={card} onAgent={onAgent} />
          ))}
        </div>
      )}
      {view === "cards" && shipped.length > 0 && (
        <details
          data-testid="projects-shipped"
          className="group rounded-xl border border-border bg-card"
        >
          <summary
            className={cn(
              "flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-sm font-semibold",
              FOCUS_RING,
            )}
          >
            Shipped <span className="font-normal text-muted-foreground">{shipped.length}</span>
          </summary>
          <ul className="m-0 list-none border-t border-border p-0">
            {shipped.map((s) => (
              <li
                key={s.slug}
                data-testid={`projects-shipped-${s.slug}`}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-4 py-2 text-sm first:border-t-0"
              >
                <Link
                  to={projectPageHref(fid, s.slug)}
                  componentId="factory.projects.shipped"
                  className={cn("grow hover:underline", FOCUS_RING)}
                >
                  {s.title}
                </Link>
                <span className="rounded-md bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                  {s.readout}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <AgentPeek snapshot={snapshot} members={members} nowS={nowS} />
    </div>
  );
}
