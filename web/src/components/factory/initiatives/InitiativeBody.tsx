/**
 * An initiative's page: the ask in the person's own words, the acceptance
 * checklist with what the last run on the base env saw (screenshots, the
 * video, the tasks its failures became, steps the envs cannot run), the
 * projects as a small flow by wave (the walking skeleton first, each
 * dependency with its reason, what each waits on) and the next wave.
 */

import { ArrowLeftIcon, FilmIcon, ImageIcon, PlayIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import type { InitiativeView } from "@/lib/factoryInitiativesApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { factoryPageHref, projectPageHref } from "../factoryRoutes";
import { LeverGated } from "../shell/viewerCan";
import { StageBar } from "../ui/StageBar";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import { MarkIcon, MergedBar, StateChip, TryItLink } from "./InitiativeCard";
import {
  type FlowProject,
  askedText,
  factsText,
  flowWaves,
  lastRun,
  runText,
  stateMeta,
  stepRows,
  tryOnBase,
} from "./initiativeModel";

const LINK = cn("underline decoration-dotted underline-offset-2", FOCUS_RING);

function Card({
  title,
  children,
  aside,
  testId,
  className,
}: {
  title: string;
  children: ReactNode;
  aside?: ReactNode;
  testId?: string;
  className?: string;
}) {
  return (
    <section
      data-testid={testId}
      className={cn("min-w-0 rounded-xl border border-border bg-card", className)}
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pt-3 pb-2">
        <h2 className="grow text-base font-semibold">{title}</h2>
        {aside}
      </header>
      <div className="border-t border-border px-4 py-3">{children}</div>
    </section>
  );
}

function Acceptance({
  view,
  factoryId,
  onRunNow,
  running,
}: {
  view: InitiativeView;
  factoryId: string;
  onRunNow?: () => void;
  running?: boolean;
}) {
  const rows = stepRows(view.acceptance);
  const run = lastRun(view);
  const open = run != null && (run.status === "due" || run.status === "requested");
  const canRun = view.status === "open" && onRunNow != null;
  return (
    <Card
      title="Acceptance on the base env"
      testId="initiative-acceptance"
      aside={
        canRun ? (
          <LeverGated lever="apply_projects">
            <Button
              size="sm"
              variant="outline"
              onClick={onRunNow}
              disabled={running || open}
              data-testid="initiative-run-now"
            >
              <PlayIcon />
              {open ? "Run on its way" : "Run now"}
            </Button>
          </LeverGated>
        ) : undefined
      }
    >
      {run && (
        <p
          className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground"
          data-testid="initiative-last-run"
        >
          <span className={cn(run.status === "FAIL" && MEANING.broken.text)}>{runText(run)}</span>
          {run.video_url && (
            <a href={run.video_url} target="_blank" rel="noreferrer" className={LINK}>
              <FilmIcon aria-hidden className="mr-0.5 inline size-3.5" />
              Video
            </a>
          )}
          {run.note && <span>· {run.note}</span>}
        </p>
      )}
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No acceptance steps yet: the lead adds them with the next wave, or edit them here.
        </p>
      ) : (
        <ol className="m-0 flex list-none flex-col gap-2.5 p-0">
          {rows.map((row) => (
            <li
              key={row.n}
              data-testid={`initiative-step-${row.n}`}
              data-mark={row.mark}
              className="flex min-w-0 gap-2"
            >
              <MarkIcon mark={row.mark} className="mt-0.5" />
              <div className="flex min-w-0 flex-col gap-0.5">
                <p className="text-sm text-foreground">
                  <span className="text-muted-foreground tabular-nums">{row.n}.</span> {row.step}
                  {row.expect && <span className="text-muted-foreground"> → {row.expect}</span>}
                </p>
                <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                  {row.owner && (
                    <Link
                      to={projectPageHref(factoryId, row.owner)}
                      componentId="factory.initiative.step_owner"
                      className={cn("font-mono", LINK)}
                    >
                      {row.owner}
                    </Link>
                  )}
                  {row.needs.map((need) => (
                    <span key={need} className="rounded border border-border px-1">
                      needs {need}
                    </span>
                  ))}
                  {row.screenshotUrl && (
                    <a href={row.screenshotUrl} target="_blank" rel="noreferrer" className={LINK}>
                      <ImageIcon aria-hidden className="mr-0.5 inline size-3.5" />
                      Screenshot
                    </a>
                  )}
                </p>
                {row.note && (
                  <p
                    className={cn(
                      "text-xs",
                      row.mark === "fail" ? MEANING.broken.text : "text-muted-foreground",
                    )}
                  >
                    {row.cannotRun ? `Cannot run in envs: ${row.note}` : row.note}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function FlowCard({ project, factoryId }: { project: FlowProject; factoryId: string }) {
  return (
    <li
      data-testid={`initiative-project-${project.slug}`}
      data-role={project.role}
      className={cn(
        "flex min-w-0 flex-col gap-1.5 rounded-lg border border-border p-2.5",
        project.role === "skeleton" && "border-status-blue/55",
        project.cancelled && "opacity-60",
      )}
    >
      <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
        <Link
          to={project.href ?? projectPageHref(factoryId, project.slug)}
          componentId="factory.initiative.project"
          className={cn(
            "min-w-0 text-sm font-semibold text-foreground underline-offset-2 hover:underline",
            FOCUS_RING,
          )}
        >
          {project.title}
        </Link>
        {project.roleLabel && (
          <span
            className={cn(
              "rounded-full border px-1.5 text-[11px]",
              project.role === "skeleton"
                ? cn(MEANING.selected.border, MEANING.selected.text)
                : "border-border text-muted-foreground",
            )}
          >
            {project.roleLabel}
          </span>
        )}
      </p>
      {project.stage && <StageBar stage={project.stage} />}
      {project.dependsOn.map((dep) => (
        <p key={dep.slug} className="text-xs text-muted-foreground">
          Uses <span className="font-mono">{dep.slug}</span>
          {dep.why ? `: ${dep.why}` : ""}
          {dep.met ? " (merged)" : ""}
        </p>
      ))}
      {project.waiting.length > 0 && (
        <p className={cn("text-xs", MEANING.needsYou.text)}>
          Waiting on {project.waiting.join(", ")}
        </p>
      )}
      {project.merged && <p className={cn("text-xs", MEANING.working.text)}>Merged</p>}
    </li>
  );
}

function Flow({ view, factoryId }: { view: InitiativeView; factoryId: string }) {
  const waves = flowWaves(view);
  return (
    <Card title="Projects" testId="initiative-flow">
      <div className="flex min-w-0 flex-col gap-3">
        {waves.map((wave) => (
          <div key={wave.n} className="min-w-0" data-testid={`initiative-wave-${wave.n}`}>
            <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              {wave.label}
            </h3>
            <ul className="m-0 grid list-none gap-2 p-0 sm:grid-cols-2">
              {wave.projects.map((project) => (
                <FlowCard key={project.slug} project={project} factoryId={factoryId} />
              ))}
            </ul>
          </div>
        ))}
        {view.next_wave && (
          <div
            data-testid="initiative-next-wave"
            className="rounded-lg border border-dashed border-border p-2.5 text-sm"
          >
            <span className="font-semibold">Next: wave {view.next_wave.n}</span>
            <span className="text-muted-foreground"> · {view.next_wave.goal}</span>
            {view.next_wave.projects.length > 0 && (
              <span className="font-mono text-xs text-muted-foreground">
                {" "}
                ({view.next_wave.projects.join(", ")})
              </span>
            )}
          </div>
        )}
        {view.hidden > 0 && (
          <p className="text-xs text-muted-foreground">{view.hidden} more you cannot see.</p>
        )}
      </div>
    </Card>
  );
}

export function InitiativeBody({
  view,
  factoryId,
  onRunNow,
  running,
}: {
  view: InitiativeView;
  factoryId: string;
  onRunNow?: () => void;
  running?: boolean;
}) {
  const state = stateMeta(view.state);
  const tryIt = tryOnBase(view);
  return (
    <div data-testid="initiative-page" className="flex min-w-0 flex-col gap-4">
      <Link
        to={factoryPageHref(factoryId)}
        componentId="factory.initiative.home"
        className={cn(
          "inline-flex w-fit items-center gap-1 text-xs text-muted-foreground hover:text-foreground",
          FOCUS_RING,
        )}
      >
        <ArrowLeftIcon aria-hidden className="size-3.5" />
        Home
      </Link>
      <header className="flex min-w-0 flex-col gap-2">
        <h1 className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-2xl font-semibold">
          <span className="min-w-0 break-words">{view.title}</span>
          <span className="font-mono text-sm font-normal text-muted-foreground">{view.ref}</span>
          <StateChip {...state} />
        </h1>
        {view.outcome && <p className="text-sm text-muted-foreground">{view.outcome}</p>}
        <div className="flex min-w-0 max-w-xl items-center gap-3">
          <MergedBar merged={view.merged} total={view.total} />
        </div>
        <p className="text-sm text-muted-foreground" data-testid="initiative-facts">
          {factsText(view)}
        </p>
        {tryIt && <TryItLink tryIt={tryIt} className="text-sm" testId="initiative-try" withHint />}
      </header>
      <div className="flex min-w-0 flex-col gap-4 lg:grid lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] lg:items-start">
        <div className="flex min-w-0 flex-col gap-4">
          <Card title="The ask" testId="initiative-ask">
            <blockquote className="border-l-2 border-border pl-3 text-sm whitespace-pre-wrap text-foreground">
              {view.ask}
            </blockquote>
            <p className="mt-2 text-xs text-muted-foreground">
              {askedText(view)}
              {view.created_by ? ` · planned as ${view.created_by}` : ""}
            </p>
          </Card>
          <Acceptance view={view} factoryId={factoryId} onRunNow={onRunNow} running={running} />
        </div>
        <Flow view={view} factoryId={factoryId} />
      </div>
    </div>
  );
}
