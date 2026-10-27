/**
 * One project on the board: title, priority and goal; the stage stepper with
 * the PL's own words beside it; its PRs; then team, dev env, cost and time.
 * Ready cards get a green edge, blocked amber, failed red; paused ones dim.
 */

import type { ReactNode } from "react";
import {
  ChevronRightIcon,
  CircleXIcon,
  GitMergeIcon,
  PauseIcon,
  StarIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { RunningDot } from "@/components/RunningDot";
import type { FactorySnapshot, PrView, ProjectView } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { useProjectControlParts } from "../controls/ProjectControlsMenu";
import { ReadyForMergeBanner } from "../review/ReadyForMergeBanner";
import { STAGE_LABEL, formatElapsed, formatUsd, plural } from "../factoryFormat";
import { ageText, projectAnchorId, projectElapsed, projectPrList } from "./boardModel";
import { DevEnvPill } from "./DevEnvPill";
import { PrRow } from "./PrRow";
import { ProvisioningTimeline, StageStepper } from "./StageStepper";
import { TeamStrip } from "./TeamStrip";

/** The status rail on a card's left edge: ready green, blocked amber, failed red. */
const EDGE = { pass: "bg-success", warning: "bg-warning", fail: "bg-destructive" } as const;

/** How many merged/closed PRs a card lists before deferring to the drawer. */
const CLOSED_PRS_SHOWN = 3;

export interface ProjectCardProps {
  snapshot: FactorySnapshot;
  project: ProjectView;
  nowS: number;
  /** Open the project drawer (`?project=<slug>`). */
  onOpenProject: (slug: string) => void;
  /** Open the review drawer (`?review=<number>`). */
  onReview: (number: number) => void;
  /** Highlight a decision in the rail (`?decision=<ref>`). */
  onDecision?: (ref: string) => void;
  /** PR ids first seen after the board loaded (they slide in). */
  newPrIds?: ReadonlySet<string>;
}

function Tag({ className, children }: { className: string; children: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex h-6 shrink-0 items-center gap-1 rounded-md px-2 text-sm font-medium whitespace-nowrap",
        className,
      )}
    >
      {children}
    </span>
  );
}

/** The card's headline tag: why it is where it is. */
function StatusTag({
  project,
  onDecision,
}: {
  project: ProjectView;
  onDecision?: (ref: string) => void;
}) {
  if (project.stage === "ready" && !project.overlay) {
    return (
      // Short: the ready banner below spells out "Ready for your review".
      <Tag className="bg-success/10 text-success">
        <StarIcon aria-hidden className="size-3.5 fill-current" />
        Ready
      </Tag>
    );
  }
  switch (project.overlay) {
    case "blocked": {
      const ref = project.overlay_reason?.match(/\bD-\d+\b/)?.[0];
      const body = (
        <>
          <TriangleAlertIcon aria-hidden className="size-3.5" />
          {project.overlay_reason ?? "Blocked"}
        </>
      );
      return ref && onDecision ? (
        <button
          type="button"
          onClick={() => onDecision(ref)}
          data-testid={`blocked-link-${project.slug}`}
          className="inline-flex h-6 shrink-0 cursor-pointer items-center gap-1 rounded-md bg-warning/15 px-2 text-sm font-medium whitespace-nowrap text-warning hover:bg-warning/25"
        >
          {body}
        </button>
      ) : (
        <Tag className="bg-warning/15 text-warning">{body}</Tag>
      );
    }
    case "failed":
      return (
        <Tag className="bg-destructive/10 text-destructive">
          <CircleXIcon aria-hidden className="size-3.5" />
          {project.overlay_reason ?? "Failed"}
        </Tag>
      );
    case "paused":
      return (
        <Tag className="bg-muted text-muted-foreground">
          <PauseIcon aria-hidden className="size-3.5" />
          Paused
        </Tag>
      );
    case "pr_closed":
      return (
        <Tag className="bg-warning/15 text-warning">
          <TriangleAlertIcon aria-hidden className="size-3.5" />
          PR closed without merge
        </Tag>
      );
    default:
      if (project.stage === "merged") {
        return (
          <Tag className="bg-brand-accent/10 text-brand-accent">
            <GitMergeIcon aria-hidden className="size-3.5" />
            Merged
          </Tag>
        );
      }
      return null;
  }
}

/** `"PL says review · GitHub: building"`, shown when the PL's claim and the gates disagree. */
function ClaimChip({ project }: { project: ProjectView }) {
  if (!project.claim_mismatch) return null;
  const claim = project.agent_stage
    ? STAGE_LABEL[project.agent_stage].toLowerCase()
    : "another stage";
  return (
    <span
      data-testid={`claim-mismatch-${project.slug}`}
      title="The PL's stage claim disagrees with what GitHub and the gates show; the board follows the gates."
      className="inline-flex h-5 shrink-0 items-center gap-1 rounded-md bg-warning/15 px-1.5 text-sm whitespace-nowrap text-warning"
    >
      <TriangleAlertIcon aria-hidden className="size-3" />
      PL says {claim} · gates say {STAGE_LABEL[project.stage].toLowerCase()}
    </span>
  );
}

/** What a card says under its stepper while there is nothing else to show. */
function StageNote({ snapshot, project }: { snapshot: FactorySnapshot; project: ProjectView }) {
  if (project.stage === "queued") {
    if (project.provisioning) return <ProvisioningTimeline stage={project.provisioning.stage} />;
    return (
      <p
        data-testid="queued-note"
        className="flex items-center gap-1.5 text-sm text-muted-foreground"
      >
        {project.pl ? (
          <>
            <RunningDot className="size-3" />
            The PL is starting
          </>
        ) : (
          "Waiting for Atlas to spawn the PL · usually under 5 min"
        )}
      </p>
    );
  }
  if (project.overlay === "paused") {
    const live = projectPrList(snapshot, project).filter(
      (pr) =>
        pr.state === "open" &&
        pr.preview &&
        ["deploying", "ready", "stale"].includes(pr.preview.state),
    ).length;
    const env = project.env?.reported?.state;
    return (
      <p data-testid="paused-note" className="text-sm text-muted-foreground">
        Paused{project.paused_by ? ` by ${project.paused_by}` : ""} ·{" "}
        {project.keep_previews && live > 0
          ? `${plural(live, "preview")} kept up`
          : live > 0
            ? `${plural(live, "preview")} going down`
            : "no previews up"}{" "}
        · dev env {env === "ready" ? "up" : "down"}
      </p>
    );
  }
  if (project.stage === "planning" && project.pr_ids.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Planning · the first PR usually lands in 15–30 min
      </p>
    );
  }
  return null;
}

function ClosedPrsNote({ count, onOpen }: { count: number; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="self-start text-sm text-muted-foreground hover:text-foreground"
    >
      {plural(count, "more PR")} in the project ▸
    </button>
  );
}

export function ProjectCard({
  snapshot,
  project,
  nowS,
  onOpenProject,
  onReview,
  onDecision,
  newPrIds,
}: ProjectCardProps) {
  const prs = projectPrList(snapshot, project);
  const open = prs.filter((pr) => pr.state === "open");
  const closed = prs.filter((pr) => pr.state !== "open");
  const shownClosed = closed.slice(0, CLOSED_PRS_SHOWN);
  const inStage = formatElapsed(nowS - project.stage_since);
  const elapsed = formatElapsed(projectElapsed(project, snapshot.server_time, nowS));
  const cost = formatUsd(project.cost_usd);
  const charter = project.charter;
  const paused = project.overlay === "paused";
  const ready = project.stage === "ready" && !project.overlay;
  const openProject = () => onOpenProject(project.slug);
  const showEnv = project.env != null || (charter?.env_needed === true && project.pl != null);
  const controls = useProjectControlParts(
    { factoryId: snapshot.factory.id, project, prs, members: snapshot.members },
    "sm:max-w-[32rem]",
  );
  const edge: keyof typeof EDGE | null =
    project.overlay === "failed"
      ? "fail"
      : ready
        ? "pass"
        : project.overlay === "blocked" || project.overlay === "pr_closed"
          ? "warning"
          : null;
  return (
    <article
      id={projectAnchorId(project.slug)}
      data-testid={`project-card-${project.slug}`}
      data-stage={project.stage}
      data-overlay={project.overlay ?? "none"}
      aria-label={project.title}
      className="@container relative flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4 max-md:p-3"
    >
      {edge && (
        <span
          aria-hidden
          data-testid={`project-edge-${project.slug}`}
          data-tone={edge}
          className={cn("absolute inset-y-3 left-0 w-[3px] rounded-r-full", EDGE[edge])}
        />
      )}
      <header className={cn("flex min-w-0 items-start gap-2", paused && "opacity-70")}>
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
            <button
              type="button"
              onClick={openProject}
              data-testid={`project-open-${project.slug}`}
              className="min-w-0 cursor-pointer truncate text-left text-ui font-semibold hover:underline"
            >
              {project.title}
            </button>
            <span
              title="Priority"
              className="shrink-0 rounded-sm border border-border px-1 font-mono text-[10px] leading-4 text-muted-foreground"
            >
              P{project.priority}
            </span>
            {project.repo && (
              <span className="min-w-0 truncate font-mono text-sm text-muted-foreground">
                {project.repo}
                {project.base_branch ? ` · ${project.base_branch}` : ""}
              </span>
            )}
            <span className="flex shrink-0 items-center @md:ml-auto">
              <StatusTag project={project} onDecision={onDecision} />
            </span>
          </div>
          {charter?.goal && (
            <p className="line-clamp-2 text-sm text-muted-foreground" title={charter.goal}>
              {charter.goal}
            </p>
          )}
        </div>
        {controls.menu}
      </header>
      {controls.quick && (
        <div
          data-testid={`project-next-${project.slug}`}
          className="-mt-1 flex min-w-0 flex-wrap items-center gap-1.5"
        >
          {controls.quick}
        </div>
      )}

      <div
        className={cn(
          "flex min-w-0 flex-col gap-2 @3xl:flex-row @3xl:items-center @3xl:gap-5",
          paused && "opacity-70",
        )}
      >
        <StageStepper
          stage={project.stage}
          overlay={project.overlay}
          running={project.pl?.running === true}
          dimmed={project.stage === "queued" && !project.pl}
          className="@3xl:w-[23rem] @3xl:shrink-0"
        />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <p className="flex flex-wrap items-center gap-x-1.5 text-sm">
            <span className="font-medium text-foreground">{STAGE_LABEL[project.stage]}</span>
            {inStage && <span className="text-muted-foreground tabular-nums">· {inStage}</span>}
            <ClaimChip project={project} />
          </p>
          {project.pl?.summary && (
            <p
              data-testid={`pl-beacon-${project.slug}`}
              className="truncate text-sm text-muted-foreground"
              title={project.pl.summary}
            >
              PL: “{project.pl.summary}”
            </p>
          )}
        </div>
      </div>

      <StageNote snapshot={snapshot} project={project} />

      {/* One keyed list, so a PR that merges keeps its row (and pulses) instead of remounting. */}
      {open.length + shownClosed.length > 0 && (
        <div className="flex min-w-0 flex-col gap-2">
          {[...open, ...shownClosed].map((pr: PrView) => (
            <PrRow
              key={pr.id}
              pr={pr}
              project={project}
              nowS={nowS}
              onReview={onReview}
              isNew={newPrIds?.has(pr.id) ?? false}
              readyBanner={
                !paused && (
                  <ReadyForMergeBanner
                    pr={pr}
                    variant="card"
                    project={project}
                    factoryId={snapshot.factory.id}
                    onReview={onReview}
                    nowS={nowS}
                  />
                )
              }
            />
          ))}
          {closed.length > CLOSED_PRS_SHOWN && (
            <ClosedPrsNote count={closed.length - CLOSED_PRS_SHOWN} onOpen={openProject} />
          )}
        </div>
      )}

      <footer
        className={cn(
          "flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 border-t border-border pt-3",
          paused && "opacity-70",
        )}
      >
        <TeamStrip pl={project.pl} team={project.team} members={snapshot.members} nowS={nowS} />
        {showEnv && <DevEnvPill env={project.env} nowS={nowS} />}
        <span className="ml-auto flex items-center gap-3 text-sm text-muted-foreground">
          {(cost || elapsed) && (
            <span className="font-mono tabular-nums" title="Cost and time so far">
              {[cost, elapsed].filter(Boolean).join(" · ")}
            </span>
          )}
          <button
            type="button"
            onClick={openProject}
            className="inline-flex cursor-pointer items-center gap-0.5 hover:text-foreground"
          >
            Details
            <ChevronRightIcon aria-hidden className="size-3.5" />
          </button>
        </span>
      </footer>
    </article>
  );
}

/** A finished project inside the collapsed Done group: one line. */
export function DoneProjectRow({
  snapshot,
  project,
  nowS,
  onOpenProject,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  nowS: number;
  onOpenProject: (slug: string) => void;
}) {
  const merged = projectPrList(snapshot, project).filter((pr) => pr.merged);
  const last = merged[0];
  const cost = formatUsd(project.cost_usd);
  return (
    <button
      type="button"
      onClick={() => onOpenProject(project.slug)}
      data-testid={`project-card-${project.slug}`}
      data-stage={project.stage}
      data-overlay={project.overlay ?? "none"}
      className="flex w-full min-w-0 cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-muted"
    >
      <GitMergeIcon aria-hidden className="size-3.5 shrink-0 text-brand-accent" />
      <span className="shrink-0 font-medium text-foreground">{project.title}</span>
      <span className="min-w-0 truncate text-muted-foreground">
        {last
          ? `merged #${last.number}${last.merged?.by ? ` by @${last.merged.by}` : ""}${last.merged ? ` · ${ageText(nowS - last.merged.at)}` : ""}`
          : "done"}
        {merged.length > 1 && ` · ${plural(merged.length, "PR")}`}
      </span>
      {cost && (
        <span className="ml-auto shrink-0 font-mono text-muted-foreground tabular-nums">
          {cost}
        </span>
      )}
    </button>
  );
}
