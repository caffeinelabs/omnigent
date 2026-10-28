/**
 * One PR (`/factory/:factoryId/prs/:prId`; a PR number resolves too) as a
 * page: which PR and whether it is ready for a human merge, its gates, its
 * loops, the review actions, then the demo (video, steps, screenshots, API
 * calls), the factory preview and which commit it runs, and every gate's
 * evidence. On a phone the demo comes first, then the gates, then Approve or
 * Request changes. `?preview=unavailable` says the PR's stable preview link
 * found no running preview.
 */

import type { ReactNode } from "react";
import { ChevronRightIcon, ExternalLinkIcon, MessagesSquareIcon } from "lucide-react";
import { ReportProblemButton } from "@/components/factory/feedback/ReportProblem";
import { GateChip, shownGates } from "@/components/factory/board/GateChip";
import {
  factoryPageHref,
  projectPageHref,
  projectsPageHref,
  roomPageHref,
} from "@/components/factory/factoryRoutes";
import { PrLoopStrip } from "@/components/factory/loops/LoopSections";
import { PrTimeline } from "@/components/factory/timeline/PrTimeline";
import { DemoPanel } from "@/components/factory/review/DemoPanel";
import { EvidencePanel } from "@/components/factory/review/EvidencePanel";
import { PreviewPanel } from "@/components/factory/review/PreviewPanel";
import {
  PrMeta,
  PreviewUnavailableNotice,
  StatusLine,
} from "@/components/factory/review/PrSummary";
import { ReviewActions } from "@/components/factory/review/ReviewActions";
import { demoRecorder } from "@/components/factory/review/reviewModel";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { findPr } from "@/components/factory/shell/shellRoutes";
import { Button } from "@/components/ui/button";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import type { FactorySnapshot, PrView, ProjectView } from "@/lib/factoryApi";
import { Link, useParams, useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";

function Breadcrumb({
  factoryId,
  project,
  pr,
}: {
  factoryId: string;
  project: ProjectView | null;
  pr: PrView;
}) {
  const sep = <ChevronRightIcon aria-hidden className="size-3.5 shrink-0 opacity-60" />;
  return (
    <nav aria-label="Breadcrumb" data-testid="pr-breadcrumb">
      <ol className="flex min-w-0 items-center gap-1 text-sm text-muted-foreground">
        <li className="shrink-0">
          <Link
            to={projectsPageHref(factoryId)}
            componentId="factory.pr.crumb_projects"
            className="hover:text-foreground"
          >
            Projects
          </Link>
        </li>
        {project && (
          <li className="flex min-w-0 items-center gap-1">
            {sep}
            <Link
              to={projectPageHref(factoryId, project.slug, { section: "prs" })}
              componentId="factory.pr.crumb_project"
              data-testid="pr-breadcrumb-project"
              className="min-w-0 truncate font-mono hover:text-foreground"
            >
              {project.slug}
            </Link>
          </li>
        )}
        <li className="flex shrink-0 items-center gap-1" aria-current="page">
          {sep}
          <span className="font-mono text-foreground">!{pr.number}</span>
        </li>
      </ol>
    </nav>
  );
}

function Gates({ pr, nowS }: { pr: PrView; nowS: number }) {
  return (
    <section
      aria-label="Gates"
      data-testid="pr-gates"
      className="flex min-w-0 flex-wrap items-center gap-1.5"
    >
      <span className="mr-0.5 text-sm text-muted-foreground">Gates</span>
      {shownGates(pr).map((gate) => (
        <GateChip key={gate} pr={pr} gate={gate} nowS={nowS} />
      ))}
    </section>
  );
}

function Panel({
  children,
  testId,
  className,
}: {
  children: ReactNode;
  testId?: string;
  className?: string;
}) {
  return (
    <div
      data-testid={testId}
      className={cn("min-w-0 rounded-xl border border-border bg-card p-4 max-md:p-3", className)}
    >
      {children}
    </div>
  );
}

function PrBody({ snapshot, pr, nowS }: { snapshot: FactorySnapshot; pr: PrView; nowS: number }) {
  const [params] = useSearchParams();
  const phone = useIsMobileViewport();
  const fid = snapshot.factory.id;
  const project = snapshot.projects.find((p) => p.slug === pr.project_slug) ?? null;
  const members = snapshot.members;
  const expectedBase = project?.base_branch ?? snapshot.factory.base?.branch ?? null;
  const unavailable = params.get("preview") === "unavailable";
  const demo = (
    <DemoPanel
      factoryId={fid}
      pr={pr}
      project={project}
      demo={pr.demo}
      nowS={nowS}
      recorder={demoRecorder(pr, members)}
    />
  );
  const preview = <PreviewPanel factoryId={fid} pr={pr} project={project} nowS={nowS} />;
  const evidence = <EvidencePanel pr={pr} members={members} nowS={nowS} />;
  const actions = (
    <Panel testId="pr-actions" className="py-3">
      <ReviewActions factoryId={fid} pr={pr} project={project} nowS={nowS} />
    </Panel>
  );
  const room = (
    <Link
      to={roomPageHref(fid, `pr:${pr.id}`)}
      componentId="factory.pr.room"
      data-testid="pr-room-link"
      className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
    >
      <MessagesSquareIcon aria-hidden className="size-3.5" />
      Room !{pr.number}
    </Link>
  );
  return (
    <article data-testid="pr-page" data-pr={pr.number} className="flex min-w-0 flex-col gap-4">
      <header className="flex min-w-0 flex-col gap-2">
        <Breadcrumb factoryId={fid} project={project} pr={pr} />
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <h1 className="min-w-0 text-xl leading-snug font-semibold break-words">
            <span className="font-mono text-muted-foreground">!{pr.number}</span> {pr.title}
          </h1>
          <div className="flex shrink-0 items-center gap-2">
            <ReportProblemButton
              factoryId={fid}
              target={`pr:${pr.id}`}
              targetLabel={`!${pr.number}`}
              project={pr.project_slug}
              size="sm"
              testId="pr-report-problem"
              className="max-sm:hidden"
            />
            {room}
            <Button asChild variant="outline" size="sm">
              <a href={pr.url} target="_blank" rel="noreferrer" data-testid="review-open-pr">
                Open on GitHub
                <ExternalLinkIcon />
              </a>
            </Button>
          </div>
        </div>
        <PrMeta pr={pr} expectedBase={expectedBase} />
        <StatusLine pr={pr} nowS={nowS} />
        {unavailable && <PreviewUnavailableNotice pr={pr} />}
      </header>
      {phone ? (
        <>
          <Panel testId="pr-demo">{demo}</Panel>
          <Gates pr={pr} nowS={nowS} />
          {actions}
          <PrLoopStrip snapshot={snapshot} pr={pr} nowS={nowS} />
          <Panel testId="pr-preview">{preview}</Panel>
          <Panel testId="pr-evidence">{evidence}</Panel>
          <Panel>
            <PrTimeline snapshot={snapshot} pr={pr} nowS={nowS} />
          </Panel>
        </>
      ) : (
        <>
          <Gates pr={pr} nowS={nowS} />
          <PrLoopStrip snapshot={snapshot} pr={pr} nowS={nowS} />
          {actions}
          <div className="grid min-w-0 grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
            <Panel testId="pr-demo">{demo}</Panel>
            <div className="flex min-w-0 flex-col gap-4">
              <Panel testId="pr-preview">{preview}</Panel>
              <Panel testId="pr-evidence">{evidence}</Panel>
            </div>
          </div>
          <Panel>
            <PrTimeline snapshot={snapshot} pr={pr} nowS={nowS} />
          </Panel>
        </>
      )}
    </article>
  );
}

function NotTracked({ factoryId, prId }: { factoryId: string; prId: string }) {
  return (
    <div
      data-testid="pr-missing"
      className="flex flex-col items-start gap-2 rounded-xl border border-dashed border-border p-6"
    >
      <p className="text-ui">
        This factory doesn’t track PR <span className="font-mono">{prId}</span>, or its project is
        hidden from you.
      </p>
      <Link
        to={factoryPageHref(factoryId)}
        componentId="factory.pr.back"
        className="text-sm underline underline-offset-2 hover:text-foreground"
      >
        Back to the Overview
      </Link>
    </div>
  );
}

export function PrPage() {
  const { prId = "" } = useParams<{ prId: string }>();
  const { snapshot, nowS } = useFactoryShell();
  const pr = findPr(snapshot.prs, prId);
  if (!pr) return <NotTracked factoryId={snapshot.factory.id} prId={prId} />;
  return <PrBody snapshot={snapshot} pr={pr} nowS={nowS} />;
}
