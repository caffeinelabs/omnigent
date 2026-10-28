/**
 * Everything under `/factory/:factoryId/*`: the factory shell (header, tabs,
 * dock, presence) around one page. Overview at the root, then Projects, a
 * project, a PR, Agents and an agent, the Timeline, Alerts and an incident,
 * Activity, the Room, Decisions, the Graph, Insights, Improvements and a
 * change, and Settings. Old cockpit links
 * (`?project=`, `?review=`, `?pr=…&preview=unavailable`, `?tab=activity`)
 * are sent to their pages by the shell.
 */

import { CompassIcon } from "lucide-react";
import { Route, Routes } from "react-router-dom";
import { ActivityList } from "@/components/factory/activity/ActivityList";
import { factoryPageHref } from "@/components/factory/factoryRoutes";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { Link } from "@/lib/routing";
import { AgentPage } from "./AgentPage";
import { AgentsPage } from "./AgentsPage";
import { AlertsPage } from "./AlertsPage";
import { DecisionsPage } from "./DecisionsPage";
import { FactoryLayout } from "./FactoryLayout";
import { GraphPage } from "./GraphPage";
import { ImprovementsPage } from "./ImprovementsPage";
import { IncidentPage } from "./IncidentPage";
import { InsightsPage } from "./InsightsPage";
import { OverviewPage } from "./OverviewPage";
import { PrPage } from "./PrPage";
import { ProposalPage } from "./ProposalPage";
import { ProjectPage } from "./ProjectPage";
import { ProjectsPage } from "./ProjectsPage";
import { RoomPage } from "./RoomPage";
import { SettingsPage } from "./SettingsPage";
import { TimelinePage } from "./TimelinePage";

/** `/activity`: every factory event, filterable by project, member and kind. */
function ActivityPage() {
  const { snapshot } = useFactoryShell();
  return (
    <div data-testid="factory-activity-page" className="flex min-w-0 flex-col gap-3">
      <h2 className="text-base font-semibold">Activity</h2>
      <ActivityList
        factoryId={snapshot.factory.id}
        projects={snapshot.projects}
        members={snapshot.members}
      />
    </div>
  );
}

/** A factory path this build has no page for yet. */
function UnknownPage() {
  const { snapshot } = useFactoryShell();
  return (
    <EmptyState
      testId="factory-unknown-page"
      icon={<CompassIcon aria-hidden className="size-6 text-muted-foreground/50" />}
      title="No such page in this factory"
      body={
        <Link
          to={factoryPageHref(snapshot.factory.id)}
          componentId="factory.unknown.back"
          className="underline underline-offset-2 hover:text-foreground"
        >
          Back to the Overview
        </Link>
      }
    />
  );
}

export function FactoryPage() {
  return (
    <FactoryLayout>
      <Routes>
        <Route index element={<OverviewPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="projects/:slug" element={<ProjectPage />} />
        <Route path="prs/:prId" element={<PrPage />} />
        <Route path="agents" element={<AgentsPage />} />
        <Route path="agents/:sid" element={<AgentPage />} />
        <Route path="timeline" element={<TimelinePage />} />
        <Route path="alerts" element={<AlertsPage />} />
        <Route path="alerts/:ref" element={<IncidentPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="room" element={<RoomPage />} />
        <Route path="room/:thread" element={<RoomPage />} />
        <Route path="decisions" element={<DecisionsPage />} />
        <Route path="graph" element={<GraphPage />} />
        <Route path="insights" element={<InsightsPage />} />
        <Route path="improvements" element={<ImprovementsPage />} />
        <Route path="improvements/:ref" element={<ProposalPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="settings/:section" element={<SettingsPage />} />
        <Route path="*" element={<UnknownPage />} />
      </Routes>
    </FactoryLayout>
  );
}
