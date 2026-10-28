/**
 * The factory Overview (`/factory/:factoryId`), exceptions first: what needs
 * a human (mine, my role, all), the projects table, the last hour of
 * activity per project, the live feed, and whether the machine is healthy.
 */

import { useMemo } from "react";
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
import { Link } from "@/lib/routing";

export function OverviewPage() {
  const { snapshot, nowS, clockOffsetS } = useFactoryShell();
  const split = useMemo(() => splitNeedsYou(snapshot), [snapshot]);
  useHashScroll();
  return (
    <div data-testid="factory-overview" className="flex min-w-0 flex-col gap-4">
      <NeedsYouPanel snapshot={snapshot} split={split} nowS={nowS} clockOffsetS={clockOffsetS} />
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
