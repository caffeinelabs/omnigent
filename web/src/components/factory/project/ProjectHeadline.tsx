/**
 * The new cockpit's project header: a breadcrumb, what users get as the H1,
 * then slug · repo → base · priority · started · the health chip; Discuss
 * (the Room drawer on the project's thread) and the ⋯ controls (pause,
 * restart or relaunch the lead, previews, env status, edit the charter,
 * stop), with the project's next step beside them when it has one.
 */

import { ChevronRightIcon, MessageSquareIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import type { FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { Link, useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { projectPrList } from "../board/boardModel";
import { ProjectControlsMenu } from "../controls/ProjectControlsMenu";
import { projectsPageHref } from "../factoryRoutes";
import { FOCUS_RING } from "../ui/statusTokens";
import { HealthChip } from "./HealthChip";
import type { HealthChip as HealthChipData } from "./projectModel";

/** `1 Nov` in the viewer's time zone. */
export function startedDate(atS: number, locale?: string): string {
  return new Date(atS * 1000).toLocaleDateString(locale ?? "en-GB", {
    day: "numeric",
    month: "short",
  });
}

export function ProjectHeadline({
  snapshot,
  project,
  chip,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  chip: HealthChipData | null;
}) {
  const fid = snapshot.factory.id;
  const [, setParams] = useSearchParams();
  const outcome = project.charter?.outcome?.trim() || project.title;
  const started =
    project.elapsed_s != null ? startedDate(snapshot.server_time - project.elapsed_s) : null;
  const discuss = () =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("room", `project:${project.slug}`);
        return next;
      },
      { replace: true },
    );
  const meta: { key: string; node: ReactNode }[] = [
    {
      key: "slug",
      node: (
        <span className="font-mono" data-testid="project-slug">
          {project.slug}
        </span>
      ),
    },
  ];
  if (project.repo) {
    meta.push({
      key: "repo",
      node: (
        <span className="font-mono break-words" data-testid="project-repo">
          {project.repo}
          {project.base_branch ? ` → ${project.base_branch}` : ""}
        </span>
      ),
    });
  }
  meta.push({ key: "priority", node: <span title="Priority">P{project.priority}</span> });
  if (started) meta.push({ key: "started", node: <span>started {started}</span> });
  return (
    <header data-testid="project-headline" className="flex min-w-0 flex-col gap-2">
      <nav aria-label="Breadcrumb" data-testid="project-breadcrumb">
        <ol className="m-0 flex min-w-0 list-none items-center gap-1 p-0 text-sm text-muted-foreground">
          <li className="shrink-0">
            <Link
              to={projectsPageHref(fid)}
              componentId="factory.project.crumb_projects"
              className={cn("hover:text-foreground", FOCUS_RING)}
            >
              Projects
            </Link>
          </li>
          <li className="flex min-w-0 items-center gap-1">
            <ChevronRightIcon aria-hidden className="size-3.5 shrink-0 opacity-60" />
            <span aria-current="page" className="min-w-0 truncate text-foreground">
              {project.slug}
            </span>
          </li>
        </ol>
      </nav>
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h1 className="text-[22px] leading-tight font-semibold break-words text-foreground">
            {outcome}
          </h1>
          <p className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-sm text-muted-foreground">
            {meta.map((part, i) => (
              <span key={part.key} className="inline-flex items-center gap-1.5">
                {i > 0 && <span aria-hidden>·</span>}
                {part.node}
              </span>
            ))}
            {chip && <HealthChip chip={chip} testId="project-health" className="ml-1" />}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={discuss}
            data-testid="project-discuss"
            componentId="factory.project.discuss"
          >
            <MessageSquareIcon aria-hidden />
            Discuss
          </Button>
          <ProjectControlsMenu
            factoryId={fid}
            project={project}
            prs={projectPrList(snapshot, project)}
            members={snapshot.members}
          />
        </div>
      </div>
    </header>
  );
}
