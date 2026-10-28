/**
 * Projects (`/factory/:factoryId/projects`): a dense table by default once
 * there are more than four projects, or the cards of the round-2 board
 * (`?view=cards|table`). Environments and closed projects sit below the table.
 */

import { LayoutGridIcon, PlusIcon, TableIcon } from "lucide-react";
import { FactoryBoard } from "@/components/factory/board/FactoryBoard";
import { prPageHref, factoryPageHref } from "@/components/factory/factoryRoutes";
import { ProjectsTable } from "@/components/factory/projects/ProjectsTable";
import { findPr } from "@/components/factory/shell/shellRoutes";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { jumpTo } from "@/components/factory/shell/scroll";
import { LeverGated } from "@/components/factory/shell/viewerCan";
import { Button } from "@/components/ui/button";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { useNavigate, useSearchParams } from "@/lib/routing";
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
