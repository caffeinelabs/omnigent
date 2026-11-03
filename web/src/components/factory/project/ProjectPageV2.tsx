/**
 * A project in the new cockpit (`/projects/:slug[/:tab]`): the header (what
 * users get, the facts, the health chip, Discuss and the controls), why it
 * exists (the why-chain and the stage bar), its team as an org chart, then
 * four tabs: Overview, Work, History and Charter. A classic section link
 * (`#prs`, `#activity`, `?decision=`) opens the tab that holds it.
 */

import { Navigate } from "react-router-dom";
import type { FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { Link, useLocation } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { PROJECT_TABS, type ProjectTab, projectTabHref } from "../factoryRoutes";
import { discoveryRuns, mergeProjects } from "../home/homeModel";
import { useFactoryAsks } from "../home/homeQueries";
import { useFactoryDefinition } from "../insights/learnQueries";
import { useHashScroll } from "../shell/scroll";
import { TeamSection } from "../team/TeamSection";
import { FOCUS_RING } from "../ui/statusTokens";
import { ProjectCharterTab } from "./ProjectCharterTab";
import { ProjectHeadline } from "./ProjectHeadline";
import { ProjectHistoryTab } from "./ProjectHistoryTab";
import { healthChip, projectWhy, tabOfSection } from "./projectModel";
import { ProjectOverviewTab } from "./ProjectOverviewTab";
import { useOpportunityFacts } from "./projectQueries";
import { ProjectWorkTab } from "./ProjectWorkTab";
import { WhyStrip } from "./WhyStrip";

export const TAB_LABEL: Record<ProjectTab, string> = {
  overview: "Overview",
  work: "Work",
  history: "History",
  charter: "Charter",
};

/** The tab a path segment names (Overview when it names none). */
export function tabOf(value: string | null | undefined): ProjectTab | null {
  if (!value) return "overview";
  return (PROJECT_TABS as readonly string[]).includes(value) ? (value as ProjectTab) : null;
}

export function ProjectPageV2({
  snapshot,
  project,
  tab,
  nowS,
  clockOffsetS,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  tab: ProjectTab;
  nowS: number;
  clockOffsetS: number;
}) {
  const fid = snapshot.factory.id;
  const location = useLocation();
  const asks = useFactoryAsks(fid);
  const definition = useFactoryDefinition(fid).data;
  const discoveryOn = discoveryRuns(definition);
  const why = projectWhy(project);
  const opportunity = useOpportunityFacts(fid, why.opportunity, discoveryOn);
  const mergeFor = mergeProjects(asks.data?.factory_id === fid ? asks.data.asks : undefined);
  const ready = project.stage === "ready" && project.overlay !== "failed";
  const chip = healthChip(project, {
    mergeForYou: ready && (mergeFor == null || mergeFor.has(project.slug)),
    nowS,
  });
  useHashScroll();
  // A classic section link opens the tab that now holds the section.
  const fromHash = tabOfSection(location.hash);
  if (tab === "overview" && fromHash && fromHash !== "overview" && location.hash !== "#team") {
    return (
      <Navigate
        to={`${projectTabHref(fid, project.slug, fromHash)}${location.search}${location.hash}`}
        replace
      />
    );
  }
  const prCount = snapshot.prs.filter((p) => project.pr_ids.includes(p.id)).length;
  return (
    <div
      data-testid={`project-page-${project.slug}`}
      data-cockpit="v2"
      className="flex min-w-0 flex-col gap-4"
    >
      <ProjectHeadline snapshot={snapshot} project={project} chip={chip} />
      <WhyStrip
        factoryId={fid}
        project={project}
        why={why}
        opportunity={opportunity.data ?? null}
        discoveryOn={discoveryOn}
      />
      <TeamSection snapshot={snapshot} project={project} nowS={nowS} sectionId="team" />
      <div
        role="tablist"
        aria-label="Project views"
        data-testid="project-tabs"
        className="flex min-w-0 gap-1 overflow-x-auto border-b border-border [scrollbar-width:none]"
      >
        {PROJECT_TABS.map((key) => (
          <Link
            key={key}
            id={`project-tab-${key}`}
            role="tab"
            aria-selected={key === tab}
            aria-controls="project-tabpanel"
            to={projectTabHref(fid, project.slug, key)}
            replace
            componentId={`factory.project.tab_${key}`}
            data-testid={`project-tab-${key}`}
            className={cn(
              "-mb-px inline-flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-sm whitespace-nowrap",
              FOCUS_RING,
              key === tab
                ? "border-foreground font-semibold text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {TAB_LABEL[key]}
            {key === "work" && prCount > 0 && (
              <span className="rounded-full bg-muted px-1.5 text-xs leading-4 font-normal text-foreground tabular-nums">
                {prCount} {prCount === 1 ? "PR" : "PRs"}
              </span>
            )}
          </Link>
        ))}
      </div>
      <div
        id="project-tabpanel"
        role="tabpanel"
        aria-labelledby={`project-tab-${tab}`}
        data-testid={`project-tabpanel-${tab}`}
        className="min-w-0"
      >
        {tab === "overview" && (
          <ProjectOverviewTab
            snapshot={snapshot}
            project={project}
            // The update card says how the work goes, not what waits on you.
            chip={healthChip(project, { nowS })}
            nowS={nowS}
            clockOffsetS={clockOffsetS}
          />
        )}
        {tab === "work" && <ProjectWorkTab snapshot={snapshot} project={project} nowS={nowS} />}
        {tab === "history" && (
          <ProjectHistoryTab snapshot={snapshot} project={project} nowS={nowS} />
        )}
        {tab === "charter" && (
          <ProjectCharterTab snapshot={snapshot} project={project} nowS={nowS} />
        )}
      </div>
    </div>
  );
}
