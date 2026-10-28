/**
 * One factory project (`/factory/:factoryId/projects/:slug`) as a page of
 * anchored sections: Overview (charter, progress, plan), Tasks, PRs, Demos
 * (every recorded run, playing inline, older runs per PR), Previews & env,
 * Decisions, Team, Loops and Activity, with a link to the project's Room
 * thread. `#demos` and the other section ids scroll to their section;
 * `?decision=<D-n>` highlights a decision.
 */

import { useEffect, useMemo, useState } from "react";
import { ChevronRightIcon } from "lucide-react";
import { ActivityList } from "@/components/factory/activity/ActivityList";
import { projectPrList } from "@/components/factory/board/boardModel";
import { DecisionsRail } from "@/components/factory/decisions/DecisionsRail";
import {
  PROJECT_SECTIONS,
  type ProjectSection,
  factoryPageHref,
  prPageHref,
  projectsPageHref,
} from "@/components/factory/factoryRoutes";
import { ProjectDemos } from "@/components/factory/project/ProjectDemos";
import { ProjectHeader } from "@/components/factory/project/ProjectHeader";
import { projectDecisions } from "@/components/factory/project/projectModel";
import { CharterCard, PlanCard } from "@/components/factory/project/ProjectOverview";
import { ProjectPreviews } from "@/components/factory/project/ProjectPreviews";
import { ProjectPrs } from "@/components/factory/project/ProjectPrs";
import { EditCharterDialog } from "@/components/factory/controls/EditCharterDialog";
import { ProjectLoops } from "@/components/factory/loops/LoopSections";
import {
  PageSection,
  ProgressCard,
  SectionNav,
  TasksSection,
  TeamTable,
  roomLink,
} from "@/components/factory/projects/ProjectSections";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { useHashScroll } from "@/components/factory/shell/scroll";
import { findPr } from "@/components/factory/shell/shellRoutes";
import { plural } from "@/components/factory/factoryFormat";
import type { FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { Link, useNavigate, useParams, useSearchParams } from "@/lib/routing";

function Breadcrumb({ factoryId, title }: { factoryId: string; title: string }) {
  return (
    <nav aria-label="Breadcrumb" data-testid="project-breadcrumb">
      <ol className="flex min-w-0 items-center gap-1 text-sm text-muted-foreground">
        <li className="shrink-0">
          <Link
            to={projectsPageHref(factoryId)}
            componentId="factory.project.crumb_projects"
            className="hover:text-foreground"
          >
            Projects
          </Link>
        </li>
        <li className="flex min-w-0 items-center gap-1" aria-current="page">
          <ChevronRightIcon aria-hidden className="size-3.5 shrink-0 opacity-60" />
          <span className="min-w-0 truncate text-foreground">{title}</span>
        </li>
      </ol>
    </nav>
  );
}

function NotOnBoard({ factoryId, slug }: { factoryId: string; slug: string }) {
  return (
    <div
      data-testid="project-missing"
      className="flex flex-col items-start gap-2 rounded-xl border border-dashed border-border p-6"
    >
      <p className="text-ui">
        There is no project <span className="font-mono">{slug}</span> in this factory, or you can’t
        see it.
      </p>
      <p className="text-sm text-muted-foreground">
        A project whose project lead you can’t open stays hidden.
      </p>
      <Link
        to={factoryPageHref(factoryId)}
        componentId="factory.project.back_to_overview"
        className="text-sm underline underline-offset-2 hover:text-foreground"
      >
        Back to the Overview
      </Link>
    </div>
  );
}

/** The section the reader is in: the last one whose top passed a line near the top. */
function useActiveSection(ids: readonly ProjectSection[]): ProjectSection {
  const [active, setActive] = useState<ProjectSection>(ids[0] ?? "overview");
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const seen = new Map<string, boolean>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) seen.set(entry.target.id, entry.isIntersecting);
        const first = ids.find((id) => seen.get(id));
        if (first) setActive(first);
      },
      // A band just under the stuck nav, where a jumped-to section's top lands.
      { rootMargin: "-12% 0px -65% 0px" },
    );
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [ids]);
  return active;
}

function ProjectBody({
  snapshot,
  project,
  nowS,
  clockOffsetS,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  nowS: number;
  clockOffsetS: number;
}) {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const fid = snapshot.factory.id;
  const prs = projectPrList(snapshot, project);
  const decisions = projectDecisions(snapshot, project);
  const openDecisions = decisions.filter((d) => d.status === "open").length;
  const demoRuns = prs.reduce((n, pr) => n + (pr.demo ? 1 + pr.older_demos : 0), 0);
  const active = useActiveSection(PROJECT_SECTIONS);
  useHashScroll();
  const onReview = (number: number) =>
    navigate(prPageHref(fid, findPr(snapshot.prs, String(number))?.id ?? number));
  const onDecision = (ref: string) =>
    navigate(`?decision=${encodeURIComponent(ref)}#decisions`, { replace: true });
  const counts: Partial<Record<ProjectSection, number>> = {
    tasks: project.tasks_summary?.total ?? 0,
    prs: prs.length,
    demos: demoRuns,
    decisions: openDecisions,
    team: (project.pl ? 1 : 0) + project.team.length,
  };
  return (
    <div data-testid={`project-page-${project.slug}`} className="flex min-w-0 flex-col gap-4">
      <Breadcrumb factoryId={fid} title={project.title} />
      <ProjectHeader snapshot={snapshot} project={project} nowS={nowS} onDecision={onDecision} />
      <SectionNav
        sections={PROJECT_SECTIONS}
        counts={counts}
        active={active}
        room={roomLink(fid, project)}
      />
      <div className="flex min-w-0 flex-col gap-8">
        <PageSection id="overview" title="Overview">
          <div className="grid min-w-0 grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
            <CharterCard project={project} onEdit={() => setEditing(true)} />
            <div className="flex min-w-0 flex-col gap-4">
              <ProgressCard snapshot={snapshot} project={project} nowS={nowS} />
              <PlanCard project={project} />
            </div>
          </div>
        </PageSection>
        <PageSection id="tasks" title="Tasks" meta="the project’s work graph">
          <TasksSection project={project} />
        </PageSection>
        <PageSection
          id="prs"
          title="PRs"
          meta={prs.length > 0 ? plural(prs.length, "PR") : undefined}
        >
          <ProjectPrs snapshot={snapshot} project={project} nowS={nowS} onReview={onReview} />
        </PageSection>
        <PageSection id="demos" title="Demos" meta="every recorded run, newest first">
          <ProjectDemos
            factoryId={fid}
            project={project}
            prs={prs}
            members={snapshot.members}
            nowS={nowS}
          />
        </PageSection>
        <PageSection id="previews" title="Previews & env">
          <ProjectPreviews snapshot={snapshot} project={project} nowS={nowS} />
        </PageSection>
        <PageSection
          id="decisions"
          title="Decisions"
          meta={openDecisions > 0 ? `${openDecisions} open` : undefined}
        >
          {decisions.length > 0 ? (
            <div className="max-w-3xl">
              <DecisionsRail
                factoryId={fid}
                decisions={decisions}
                highlightId={params.get("decision")}
                clockOffsetS={clockOffsetS}
                showClosed
                hideHeading
              />
            </div>
          ) : (
            <p data-testid="project-decisions-empty" className="text-sm text-muted-foreground">
              Nothing asked yet. Questions from the PL and the supervisor land here.
            </p>
          )}
        </PageSection>
        <PageSection id="team" title="Team" meta="agents on this project">
          <TeamTable project={project} nowS={nowS} factoryId={fid} />
        </PageSection>
        <PageSection id="loops" title="Loops">
          <ProjectLoops snapshot={snapshot} project={project} nowS={nowS} />
        </PageSection>
        <PageSection id="activity" title="Activity">
          <ActivityList
            factoryId={fid}
            projects={snapshot.projects}
            members={snapshot.members}
            project={project.slug}
            heightClassName="max-h-[min(60vh,36rem)]"
          />
        </PageSection>
      </div>
      <EditCharterDialog
        factoryId={fid}
        project={project}
        open={editing}
        onOpenChange={setEditing}
      />
    </div>
  );
}

export function ProjectPage() {
  const { slug = "" } = useParams<{ slug: string }>();
  const { snapshot, nowS, clockOffsetS } = useFactoryShell();
  const project = useMemo(
    () => snapshot.projects.find((p) => p.slug === slug) ?? null,
    [snapshot, slug],
  );
  if (!project) return <NotOnBoard factoryId={snapshot.factory.id} slug={slug} />;
  return (
    <ProjectBody snapshot={snapshot} project={project} nowS={nowS} clockOffsetS={clockOffsetS} />
  );
}
