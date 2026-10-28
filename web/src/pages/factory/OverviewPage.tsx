/**
 * The factory Overview (`/factory/:factoryId`), exceptions first: what needs
 * a human (mine, my role, all; incidents with their first runbook step and
 * agent), the projects table, the last hour of activity per project, the
 * live feed, and whether the machine is healthy.
 */

import { useMemo } from "react";
import { useFactoryIssues } from "@/components/factory/agents/opsQueries";
import { ActivityHeatmap } from "@/components/factory/overview/ActivityHeatmap";
import { LiveFeed } from "@/components/factory/overview/LiveFeed";
import { NeedsYouPanel } from "@/components/factory/overview/NeedsYouPanel";
import { splitNeedsYou } from "@/components/factory/overview/needsYouModel";
import { OverviewHealth } from "@/components/factory/overview/OverviewHealth";
import { ProjectsTable } from "@/components/factory/projects/ProjectsTable";
import { projectsPageHref } from "@/components/factory/factoryRoutes";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { useHashScroll } from "@/components/factory/shell/scroll";
import { Button } from "@/components/ui/button";
import type { IssueFilters } from "@/lib/factoryApi";
import { Link, useSearchParams } from "@/lib/routing";

/** Open and acked incidents for Needs you (round-3 servers; older ones answer 404). */
const NEEDS_ISSUES: IssueFilters = { class: "incident", status: ["open", "acked"] };

export function OverviewPage() {
  const { snapshot, nowS, clockOffsetS, wide } = useFactoryShell();
  const [params] = useSearchParams();
  const issues = useFactoryIssues(snapshot.factory.id, NEEDS_ISSUES);
  const split = useMemo(
    () =>
      splitNeedsYou(
        snapshot,
        snapshot.viewer ?? null,
        issues.data ? { data: issues.data.data, mine: issues.data.mine } : null,
      ),
    [snapshot, issues.data],
  );
  useHashScroll();
  return (
    <div data-testid="factory-overview" className="flex min-w-0 flex-col gap-4">
      <NeedsYouPanel
        snapshot={snapshot}
        split={split}
        nowS={nowS}
        clockOffsetS={clockOffsetS}
        // The dock's rail highlights it when there is a dock.
        highlightDecision={wide ? null : params.get("decision")}
      />
      <ProjectsTable
        snapshot={snapshot}
        nowS={nowS}
        variant="overview"
        actions={
          <Button asChild size="xs" variant="ghost" className="text-muted-foreground">
            <Link
              to={projectsPageHref(snapshot.factory.id)}
              componentId="factory.overview.all_projects"
              data-testid="overview-all-projects"
            >
              All projects →
            </Link>
          </Button>
        }
      />
      <div className="grid min-w-0 grid-cols-1 items-start gap-4 2xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <ActivityHeatmap snapshot={snapshot} nowS={nowS} />
        <LiveFeed snapshot={snapshot} />
      </div>
      <OverviewHealth snapshot={snapshot} nowS={nowS} />
    </div>
  );
}
