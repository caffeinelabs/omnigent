/**
 * The project page's header: title, slug, priority, repo#branch, the stage
 * stepper with the PL's own words, why the project is where it is, cost and
 * time, the project controls and Open PL chat.
 */

import { GitBranchIcon, MessageSquareIcon, TimerIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { projectElapsed, projectPrList } from "../board/boardModel";
import { LiveDot } from "../board/FactoryHeader";
import { ClaimChip, StatusTag } from "../board/ProjectCard";
import { ProvisioningTimeline, StageStepper } from "../board/StageStepper";
import { ProjectControlsMenu } from "../controls/ProjectControlsMenu";
import { STAGE_LABEL, formatElapsed, formatUsd } from "../factoryFormat";

export function ProjectHeader({
  snapshot,
  project,
  nowS,
  onDecision,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  nowS: number;
  /** A blocked project's `D-n` link: show that decision. */
  onDecision: (ref: string) => void;
}) {
  const prs = projectPrList(snapshot, project);
  const cost = formatUsd(project.cost_usd);
  const elapsed = formatElapsed(projectElapsed(project, snapshot.server_time, nowS));
  const inStage = formatElapsed(nowS - project.stage_since);
  const paused = project.overlay === "paused";
  return (
    <header data-testid="project-header" className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-2xl font-semibold">
            <span className="min-w-0 break-words">{project.title}</span>
            <span
              title="Priority"
              className="shrink-0 rounded-sm border border-border px-1 font-mono text-[11px] leading-4 font-normal text-muted-foreground"
            >
              P{project.priority}
            </span>
          </h1>
          <p className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <span className="font-mono" data-testid="project-slug">
              {project.slug}
            </span>
            {project.repo && (
              <span
                data-testid="project-repo"
                className="inline-flex min-w-0 items-center gap-1 font-mono break-all"
              >
                <GitBranchIcon aria-hidden className="size-3.5 shrink-0" />
                {project.repo}
                {project.base_branch ? `#${project.base_branch}` : ""}
              </span>
            )}
            <LiveDot />
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {project.pl && (
            <Button asChild variant="outline" size="sm">
              <Link
                to={project.pl.url}
                componentId="factory.project.open_pl_chat"
                data-testid="project-open-pl"
              >
                <MessageSquareIcon />
                Open PL chat
              </Link>
            </Button>
          )}
          <ProjectControlsMenu
            factoryId={snapshot.factory.id}
            project={project}
            prs={prs}
            members={snapshot.members}
          />
        </div>
      </div>

      <div
        data-testid="project-stage"
        className={cn(
          "@container flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4 max-md:p-3",
          paused && "opacity-80",
        )}
      >
        <div className="flex min-w-0 flex-col gap-3 @3xl:flex-row @3xl:items-center @3xl:gap-6">
          <StageStepper
            stage={project.stage}
            overlay={project.overlay}
            running={project.pl?.running === true}
            dimmed={project.stage === "queued" && !project.pl}
            className="@3xl:w-[26rem] @3xl:shrink-0"
          />
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
              <span className="font-medium text-foreground">{STAGE_LABEL[project.stage]}</span>
              {inStage && <span className="text-muted-foreground tabular-nums">· {inStage}</span>}
              {project.overlay && <StatusTag project={project} onDecision={onDecision} />}
              <ClaimChip project={project} />
            </p>
            {project.pl?.summary && (
              <p
                data-testid="project-pl-beacon"
                className="line-clamp-2 text-sm text-muted-foreground"
                title={project.pl.summary}
              >
                PL: “{project.pl.summary}”
              </p>
            )}
          </div>
          {(cost || elapsed) && (
            <dl className="flex shrink-0 gap-5 text-sm @3xl:flex-col @3xl:gap-0.5 @3xl:text-right">
              {cost && (
                <div className="flex items-baseline gap-1.5 @3xl:justify-end">
                  <dt className="text-muted-foreground">Cost</dt>
                  <dd className="font-mono tabular-nums" data-testid="project-cost">
                    {cost}
                  </dd>
                </div>
              )}
              {elapsed && (
                <div className="flex items-baseline gap-1.5 @3xl:justify-end">
                  <dt className="text-muted-foreground">
                    <TimerIcon aria-label="Elapsed" className="inline size-3.5 align-[-2px]" />
                  </dt>
                  <dd className="font-mono tabular-nums" data-testid="project-elapsed">
                    {elapsed}
                  </dd>
                </div>
              )}
            </dl>
          )}
        </div>
        {project.stage === "queued" && project.provisioning && (
          <ProvisioningTimeline stage={project.provisioning.stage} />
        )}
      </div>
    </header>
  );
}
