/**
 * One factory project (`/factory/:factoryId/projects/:slug`), from the same
 * live snapshot as the cockpit: the header, then tabs synced to `?tab=`:
 * Overview (charter, team, decisions, plan), PRs, Demos, Previews & envs and
 * Activity. `?review=<pr number>` opens the review drawer over the page and
 * `?decision=<D-n>` highlights a decision on the Overview.
 */

import { type ReactNode, useCallback, useMemo } from "react";
import { ChevronRightIcon } from "lucide-react";
import { PageScroll } from "@/components/PageScroll";
import { ActivityList } from "@/components/factory/activity/ActivityList";
import { projectDemoCount, projectPrList } from "@/components/factory/board/boardModel";
import { FactoryControlsProvider } from "@/components/factory/controls/FactoryControlsContext";
import { useFactoryServerNowS } from "@/components/factory/factoryClock";
import { type ProjectTab, factoryPageHref, projectTab } from "@/components/factory/factoryRoutes";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { ProjectDemos } from "@/components/factory/project/ProjectDemos";
import { ProjectHeader } from "@/components/factory/project/ProjectHeader";
import { ProjectOverview } from "@/components/factory/project/ProjectOverview";
import { ProjectPreviews } from "@/components/factory/project/ProjectPreviews";
import { ProjectPrs } from "@/components/factory/project/ProjectPrs";
import { ReviewDrawer } from "@/components/factory/review/ReviewDrawer";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { COCKPIT_MIN_REFRESH_MS, useFactory } from "@/hooks/useFactory";
import { snapshotNotices, useFactoryNotifications } from "@/hooks/useFactoryNotifications";
import type { FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { Link, useParams, useSearchParams } from "@/lib/routing";
import { ApiError } from "@/lib/sessionsApi";

function Breadcrumb({
  factoryId,
  factoryName,
  projectTitle,
}: {
  factoryId: string;
  factoryName: string | null;
  projectTitle: string;
}) {
  const sep = <ChevronRightIcon aria-hidden className="size-3.5 shrink-0 opacity-60" />;
  return (
    <nav aria-label="Breadcrumb" data-testid="project-breadcrumb" className="mb-3">
      <ol className="flex min-w-0 items-center gap-1 text-sm text-muted-foreground">
        <li className="shrink-0">
          <Link
            to="/factory"
            componentId="factory.project.crumb_index"
            className="hover:text-foreground"
          >
            Factory
          </Link>
        </li>
        <li className="flex min-w-0 shrink items-center gap-1">
          {sep}
          <Link
            to={factoryPageHref(factoryId)}
            componentId="factory.project.crumb_factory"
            data-testid="project-breadcrumb-factory"
            className="min-w-0 truncate hover:text-foreground"
          >
            {factoryName ?? "…"}
          </Link>
        </li>
        <li className="flex min-w-0 shrink-[2] items-center gap-1" aria-current="page">
          {sep}
          <span className="min-w-0 truncate text-foreground">{projectTitle}</span>
        </li>
      </ol>
    </nav>
  );
}

function ProjectSkeleton() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading project"
      data-testid="project-loading"
      className="flex flex-col gap-4"
    >
      <div className="flex items-center gap-3">
        <div className="h-7 w-64 animate-pulse rounded bg-muted" />
        <div className="ml-auto h-7 w-32 animate-pulse rounded bg-muted" />
      </div>
      <div className="h-4 w-80 max-w-full animate-pulse rounded bg-muted" />
      <div className="h-20 w-full animate-pulse rounded-xl bg-muted" />
      <div className="flex gap-4">
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="h-5 w-20 animate-pulse rounded bg-muted" />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="h-64 animate-pulse rounded-xl bg-muted" />
        <div className="h-40 animate-pulse rounded-xl bg-muted" />
      </div>
    </div>
  );
}

const TAB_LABEL: Record<ProjectTab, string> = {
  overview: "Overview",
  prs: "PRs",
  demos: "Demos",
  previews: "Previews & envs",
  activity: "Activity",
};

function TabCount({ n }: { n: number }) {
  return (
    <span className="rounded-full bg-muted px-1.5 text-xs leading-4 font-normal text-muted-foreground tabular-nums">
      {n}
    </span>
  );
}

/** Line tabs that scroll sideways on a phone rather than wrap. */
function ProjectTabSwitch({
  tab,
  counts,
  onChange,
}: {
  tab: ProjectTab;
  counts: Partial<Record<ProjectTab, number>>;
  onChange: (tab: ProjectTab) => void;
}) {
  return (
    <Tabs
      value={tab}
      onValueChange={(v) => onChange(projectTab(v))}
      componentId="factory.project.tab"
      className="min-w-0"
    >
      <div className="-mx-4 overflow-x-auto border-b border-border px-4 [scrollbar-width:none] md:mx-0 md:px-0 [&::-webkit-scrollbar]:hidden">
        <TabsList variant="line" data-testid="project-tabs" className="h-9 gap-3">
          {(Object.keys(TAB_LABEL) as ProjectTab[]).map((key) => (
            <TabsTrigger
              key={key}
              value={key}
              data-testid={`project-tab-${key}`}
              className="flex-none px-0.5"
            >
              {TAB_LABEL[key]}
              {counts[key] != null && counts[key] > 0 && <TabCount n={counts[key]} />}
            </TabsTrigger>
          ))}
        </TabsList>
      </div>
    </Tabs>
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
        componentId="factory.project.back_to_board"
        className="text-sm underline underline-offset-2 hover:text-foreground"
      >
        Back to the board
      </Link>
    </div>
  );
}

function ProjectBody({
  snapshot,
  project,
  clockOffsetS,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  clockOffsetS: number;
}) {
  const [params, setParams] = useSearchParams();
  const nowS = useFactoryServerNowS(clockOffsetS);
  const tab = projectTab(params.get("tab"));
  const review = params.get("review");
  const prs = projectPrList(snapshot, project);
  useFactoryNotifications(useMemo(() => snapshotNotices(snapshot), [snapshot]));

  const setParam = useCallback(
    (key: string, value: string | null, replace = false) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value == null) next.delete(key);
          else next.set(key, value);
          return next;
        },
        { replace },
      ),
    [setParams],
  );
  const onReview = (number: number) => setParam("review", String(number));
  const onDecision = (ref: string) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("tab");
      next.set("decision", ref);
      return next;
    });
  };
  const reviewPr = review
    ? (snapshot.prs.find((pr) => String(pr.number) === review || pr.id === review) ?? null)
    : null;
  const closeReview = () =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      for (const key of ["review", "pr", "preview"]) next.delete(key);
      return next;
    });

  let body: ReactNode;
  switch (tab) {
    case "prs":
      body = <ProjectPrs snapshot={snapshot} project={project} nowS={nowS} onReview={onReview} />;
      break;
    case "demos":
      body = (
        <ProjectDemos
          factoryId={snapshot.factory.id}
          project={project}
          prs={prs}
          members={snapshot.members}
          nowS={nowS}
          onReview={onReview}
        />
      );
      break;
    case "previews":
      body = <ProjectPreviews snapshot={snapshot} project={project} nowS={nowS} />;
      break;
    case "activity":
      body = (
        <ActivityList
          factoryId={snapshot.factory.id}
          projects={snapshot.projects}
          members={snapshot.members}
          project={project.slug}
        />
      );
      break;
    default:
      body = (
        <ProjectOverview
          snapshot={snapshot}
          project={project}
          nowS={nowS}
          clockOffsetS={clockOffsetS}
          highlightDecision={params.get("decision")}
        />
      );
  }

  return (
    <div data-testid={`project-page-${project.slug}`} className="flex min-w-0 flex-col gap-4">
      <ProjectHeader snapshot={snapshot} project={project} nowS={nowS} onDecision={onDecision} />
      <ProjectTabSwitch
        tab={tab}
        counts={{ prs: prs.length, demos: projectDemoCount(prs) }}
        onChange={(next) => setParam("tab", next === "overview" ? null : next, true)}
      />
      <div data-testid="project-tab-panel" data-tab={tab} className="min-w-0">
        {body}
      </div>
      {review && (
        <ReviewDrawer
          factoryId={snapshot.factory.id}
          pr={reviewPr}
          demo={reviewPr?.demo ?? null}
          onClose={closeReview}
          snapshot={snapshot}
          nowS={nowS}
          previewUnavailable={params.get("preview") === "unavailable"}
        />
      )}
    </div>
  );
}

export function ProjectPage() {
  const { factoryId = "", slug = "" } = useParams<{ factoryId: string; slug: string }>();
  const { data, error, isLoading, updatedAt, clockOffsetS, refetch } = useFactory(factoryId, {
    minRefreshMs: COCKPIT_MIN_REFRESH_MS,
  });
  const missing = !data && error instanceof ApiError && error.status === 404;
  const project = data?.projects.find((p) => p.slug === slug) ?? null;
  return (
    <PageScroll
      data-testid="project-page"
      maxWidthClassName="max-w-7xl"
      contentClassName="px-4 md:px-6"
    >
      <Breadcrumb
        factoryId={factoryId}
        factoryName={data?.factory.name ?? null}
        projectTitle={project?.title ?? slug}
      />
      {error && !missing && (
        <FactoryLoadError
          error={error}
          updatedAt={data ? updatedAt : 0}
          onRetry={refetch}
          what="this project"
        />
      )}
      {isLoading && <ProjectSkeleton />}
      {missing && (
        <p data-testid="factory-missing" className="text-ui text-muted-foreground">
          This factory doesn’t exist, or you can’t see it.
        </p>
      )}
      {data && !project && <NotOnBoard factoryId={factoryId} slug={slug} />}
      {data && project && (
        <FactoryControlsProvider key={data.factory.id} snapshot={data}>
          <ProjectBody snapshot={data} project={project} clockOffsetS={clockOffsetS} />
        </FactoryControlsProvider>
      )}
    </PageScroll>
  );
}
