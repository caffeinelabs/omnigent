/**
 * Factory cockpit (`/factory/:factoryId`): what is moving, what is blocked,
 * what needs the human, and whether the machine is alive.
 *
 * Query parameters: `?tab=board|activity`, `?project=<slug>` (project
 * drawer), `?review=<pr number>` (review drawer), `?decision=<D-n>` (highlight
 * in the decisions rail). The rail sits beside the board from 1280 px and
 * folds under the Needs-you strip below that.
 */

import { useCallback, useState } from "react";
import { ArrowLeftIcon } from "lucide-react";
import { PageScroll } from "@/components/PageScroll";
import { ActivityList } from "@/components/factory/activity/ActivityList";
import { WIDE_BOARD_QUERY, useMediaQuery } from "@/components/factory/board/boardHooks";
import { FactoryBoard } from "@/components/factory/board/FactoryBoard";
import { FactoryHeader } from "@/components/factory/board/FactoryHeader";
import { HealthStrip } from "@/components/factory/board/HealthStrip";
import { needsYou } from "@/components/factory/board/boardModel";
import { ProjectDrawer } from "@/components/factory/board/ProjectDrawer";
import { DecisionsRail } from "@/components/factory/decisions/DecisionsRail";
import { useFactoryServerNowS } from "@/components/factory/factoryClock";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { ReviewDrawer } from "@/components/factory/review/ReviewDrawer";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useFactory } from "@/hooks/useFactory";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { Link, useParams, useSearchParams } from "@/lib/routing";
import { ApiError } from "@/lib/sessionsApi";

type CockpitTab = "board" | "activity";

const FLASH_MS = 1200;

/**
 * The cockpit re-reads its snapshot at least this often while visible: quiet
 * supervisor ticks, polls and probes push no frame, and the health strip ages
 * their stamps against the clock (the ETag keeps an unchanged read cheap).
 */
export const COCKPIT_MIN_REFRESH_MS = 60_000;

/** Scroll an element into view and flash a ring around it (still under reduced motion). */
export function jumpTo(targetId: string): void {
  const el = document.getElementById(targetId);
  if (!el) return;
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
  el.dataset.highlighted = "true";
  if (!reduce) {
    el.animate?.(
      [
        { boxShadow: "0 0 0 0 var(--ring)" },
        { boxShadow: "0 0 0 4px var(--ring)", offset: 0.2 },
        { boxShadow: "0 0 0 0 transparent" },
      ],
      { duration: 900, easing: "ease-out" },
    );
  }
  window.setTimeout(() => delete el.dataset.highlighted, FLASH_MS);
}

function CockpitSkeleton() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading factory"
      data-testid="factory-loading"
      className="flex flex-col gap-4"
    >
      <div className="flex items-center gap-3">
        <div className="h-7 w-56 animate-pulse rounded bg-muted" />
        <div className="ml-auto h-7 w-28 animate-pulse rounded bg-muted" />
      </div>
      <div className="h-4 w-3/4 animate-pulse rounded bg-muted" />
      <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
      {[0, 1].map((i) => (
        <div key={i} className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4">
          <div className="h-5 w-48 animate-pulse rounded bg-muted" />
          <div className="h-3 w-full animate-pulse rounded bg-muted" />
          <div className="h-20 w-full animate-pulse rounded-lg bg-muted" />
          <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

function TabSwitch({ tab, onChange }: { tab: CockpitTab; onChange: (tab: CockpitTab) => void }) {
  const mobile = useIsMobileViewport();
  if (mobile) {
    return (
      <Select value={tab} onValueChange={(v) => onChange(v as CockpitTab)}>
        <SelectTrigger
          size="sm"
          aria-label="View"
          data-testid="factory-tab-select"
          className="w-40"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="board">Board</SelectItem>
          <SelectItem value="activity">Activity</SelectItem>
        </SelectContent>
      </Select>
    );
  }
  return (
    <Tabs
      value={tab}
      onValueChange={(v) => onChange(v as CockpitTab)}
      componentId="factory.cockpit.tab"
    >
      <TabsList variant="line" data-testid="factory-tabs" className="h-8">
        <TabsTrigger value="board" data-testid="factory-tab-board" className="px-2">
          Board
        </TabsTrigger>
        <TabsTrigger value="activity" data-testid="factory-tab-activity" className="px-2">
          Activity
        </TabsTrigger>
      </TabsList>
    </Tabs>
  );
}

function Cockpit({ snapshot, clockOffsetS }: { snapshot: FactorySnapshot; clockOffsetS: number }) {
  const [params, setParams] = useSearchParams();
  const nowS = useFactoryServerNowS(clockOffsetS);
  const wide = useMediaQuery(WIDE_BOARD_QUERY);
  const tab: CockpitTab = params.get("tab") === "activity" ? "activity" : "board";
  const projectSlug = params.get("project");
  const review = params.get("review");
  const decision = params.get("decision");
  const [panelOpen, setPanelOpen] = useState(decision != null);

  const setParam = useCallback(
    (key: string, value: string | null) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value == null) next.delete(key);
          else next.set(key, value);
          return next;
        },
        { replace: key === "decision" || key === "tab" },
      ),
    [setParams],
  );

  const pointAtDecision = (ref: string) => {
    setParam("decision", ref);
    if (!wide) setPanelOpen(true);
  };
  const onDecisions = () => {
    if (!wide) {
      setPanelOpen((open) => !open);
      return;
    }
    const first = needsYou(snapshot).openDecisions[0];
    if (first) setParam("decision", first.ref);
  };
  const reviewPr = review
    ? (snapshot.prs.find((pr) => String(pr.number) === review || pr.id === review) ?? null)
    : null;
  const rail = (
    <DecisionsRail
      factoryId={snapshot.factory.id}
      decisions={snapshot.decisions}
      highlightId={decision}
    />
  );

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex min-w-0 flex-col gap-2.5">
        <FactoryHeader snapshot={snapshot} nowS={nowS} />
        <HealthStrip snapshot={snapshot} nowS={nowS} />
      </div>
      <TabSwitch tab={tab} onChange={(next) => setParam("tab", next === "board" ? null : next)} />
      <div
        data-testid="factory-layout"
        data-wide={wide ? "true" : "false"}
        className={
          wide ? "grid grid-cols-[minmax(0,1fr)_18rem] items-start gap-6" : "flex min-w-0 flex-col"
        }
      >
        <div className="min-w-0">
          {tab === "board" ? (
            <FactoryBoard
              snapshot={snapshot}
              nowS={nowS}
              onOpenProject={(slug) => setParam("project", slug)}
              onReview={(number) => setParam("review", String(number))}
              onDecision={pointAtDecision}
              onDecisions={onDecisions}
              onJump={jumpTo}
              decisionsPanel={wide ? undefined : rail}
              decisionsExpanded={panelOpen}
            />
          ) : (
            <ActivityList
              factoryId={snapshot.factory.id}
              projects={snapshot.projects}
              members={snapshot.members}
            />
          )}
        </div>
        {wide && (
          // The padding (offset by the margin) keeps a highlighted card's ring inside the scroller.
          <aside
            data-testid="factory-rail"
            className="sticky -m-1 max-h-[calc(100vh-6rem)] overflow-y-auto p-1"
            style={{
              top: "calc(var(--omnigent-header-height) + var(--omnigent-inset-top) + 1rem)",
            }}
          >
            {rail}
          </aside>
        )}
      </div>
      {projectSlug && (
        <ProjectDrawer
          snapshot={snapshot}
          slug={projectSlug}
          nowS={nowS}
          onClose={() => setParam("project", null)}
        />
      )}
      {review && (
        <ReviewDrawer
          factoryId={snapshot.factory.id}
          pr={reviewPr}
          demo={reviewPr?.demo ?? null}
          onClose={() => setParam("review", null)}
        />
      )}
    </div>
  );
}

export function FactoryPage() {
  const { factoryId } = useParams<{ factoryId: string }>();
  const { data, error, isLoading, updatedAt, clockOffsetS, refetch } = useFactory(factoryId, {
    minRefreshMs: COCKPIT_MIN_REFRESH_MS,
  });
  const missing = !data && error instanceof ApiError && error.status === 404;
  return (
    <PageScroll
      data-testid="factory-page"
      maxWidthClassName="max-w-7xl"
      contentClassName="px-4 md:px-6"
    >
      <Link
        to="/factory"
        componentId="factory.page.back"
        className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeftIcon aria-hidden className="size-3.5" />
        Factory
      </Link>
      {error && !missing && (
        <FactoryLoadError
          error={error}
          updatedAt={data ? updatedAt : 0}
          onRetry={refetch}
          what="this factory"
        />
      )}
      {isLoading && <CockpitSkeleton />}
      {missing && (
        <p data-testid="factory-missing" className="text-ui text-muted-foreground">
          This factory doesn’t exist, or you can’t see it.
        </p>
      )}
      {data && <Cockpit key={data.factory.id} snapshot={data} clockOffsetS={clockOffsetS} />}
    </PageScroll>
  );
}
