/**
 * Everything under `/factory/:factoryId/*`: the factory shell (header, tabs,
 * dock, presence) around one page. Overview at the root, then Projects, a
 * project, a PR, Agents and an agent, the Timeline, Alerts and an incident,
 * Activity, the Room, Decisions, the Graph and Settings. Old cockpit links
 * (`?project=`, `?review=`, `?pr=…&preview=unavailable`, `?tab=activity`)
 * are sent to their pages by the shell.
 */

import { CompassIcon } from "lucide-react";
import { Navigate, Route, Routes } from "react-router-dom";
import { ActivityList } from "@/components/factory/activity/ActivityList";
import {
  SETTINGS_SECTIONS,
  factoryPageHref,
  settingsPageHref,
} from "@/components/factory/factoryRoutes";
import { PeopleSettings } from "@/components/factory/people/PeopleSettings";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { Link, useParams } from "@/lib/routing";
import { AgentPage } from "./AgentPage";
import { AgentsPage } from "./AgentsPage";
import { AlertsPage } from "./AlertsPage";
import { DecisionsPage } from "./DecisionsPage";
import { FactoryLayout } from "./FactoryLayout";
import { GraphPage } from "./GraphPage";
import { IncidentPage } from "./IncidentPage";
import { OverviewPage } from "./OverviewPage";
import { PrPage } from "./PrPage";
import { ProjectPage } from "./ProjectPage";
import { ProjectsPage } from "./ProjectsPage";
import { RoomPage } from "./RoomPage";
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

/** `/settings/:section`: People today; the other sections join it. */
function SettingsPage() {
  const { section = "people" } = useParams<{ section: string }>();
  const { snapshot, nowS } = useFactoryShell();
  if (!SETTINGS_SECTIONS.includes(section as (typeof SETTINGS_SECTIONS)[number])) {
    return <Navigate to={settingsPageHref(snapshot.factory.id)} replace />;
  }
  return (
    <div data-testid="factory-settings-page" className="flex min-w-0 flex-col gap-4">
      <div className="flex min-w-0 items-baseline gap-2">
        <h2 className="text-base font-semibold">Settings</h2>
        <span className="text-sm text-muted-foreground">People</span>
      </div>
      <PeopleSettings factoryId={snapshot.factory.id} nowS={nowS} />
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
        <Route path="settings" element={<SettingsPage />} />
        <Route path="settings/:section" element={<SettingsPage />} />
        <Route path="*" element={<UnknownPage />} />
      </Routes>
    </FactoryLayout>
  );
}
