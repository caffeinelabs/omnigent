/**
 * Everything under `/factory/:factoryId/*`: the cockpit (sidebar, presence)
 * around one page. Its places are Home at the root, Demos, Chats
 * (`chats/<thread>` one conversation), Decisions and the node pages
 * (`n/<ref>`, `n/<ref>/<level>`). Older addresses (a project or PR page, an
 * initiative, the Room, `?review=`) are sent to the place that shows them now
 * by the shell; the earlier cockpits' other pages stay reachable from Debug.
 */

import { CompassIcon } from "lucide-react";
import { Route, Routes } from "react-router-dom";
import { ActivityList } from "@/components/factory/activity/ActivityList";
import { factoryPageHref } from "@/components/factory/factoryRoutes";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { Link } from "@/lib/routing";
import { AgentsPage } from "./AgentsPage";
import { AlertsPage } from "./AlertsPage";
import { DebugPage } from "./DebugPage";
import { CockpitDecisions } from "./CockpitDecisions";
import { DiscoveryPage } from "./DiscoveryPage";
import { FactoryLayout } from "./FactoryLayout";
import { GraphPage } from "./GraphPage";
import { ImprovementsPage } from "./ImprovementsPage";
import { IncidentPage } from "./IncidentPage";
import { InsightsPage } from "./InsightsPage";
import { ProposalPage } from "./ProposalPage";
import { SettingsPage } from "./SettingsPage";
import { SteerPage } from "./SteerPage";
import { TimelinePage } from "./TimelinePage";
import { AgentRoute, ProjectTeamRoute, TeamsRoute } from "./cockpitV2Routes";
import { ChatsPage } from "./ChatsPage";
import { CockpitHome } from "./CockpitHome";
import { DemosPage } from "./DemosPage";
import { NodeRoute } from "./NodeRoute";

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
        <Route index element={<CockpitHome />} />
        <Route path="demos" element={<DemosPage />} />
        <Route path="chats" element={<ChatsPage />} />
        <Route path="chats/:thread" element={<ChatsPage />} />
        <Route path="projects/:slug/team" element={<ProjectTeamRoute />} />
        <Route path="agents" element={<AgentsPage />} />
        <Route path="agents/:sid" element={<AgentRoute />} />
        <Route path="teams" element={<TeamsRoute />} />
        <Route path="teams/:tab" element={<TeamsRoute />} />
        <Route path="timeline" element={<TimelinePage />} />
        <Route path="alerts" element={<AlertsPage />} />
        <Route path="alerts/:ref" element={<IncidentPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="decisions" element={<CockpitDecisions />} />
        <Route path="graph" element={<GraphPage />} />
        <Route path="insights" element={<InsightsPage />} />
        <Route path="improvements" element={<ImprovementsPage />} />
        <Route path="improvements/:ref" element={<ProposalPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="settings/:section" element={<SettingsPage />} />
        <Route path="discovery" element={<DiscoveryPage />} />
        <Route path="discovery/:view" element={<DiscoveryPage />} />
        <Route path="steer" element={<SteerPage />} />
        <Route path="steer/:section" element={<SteerPage />} />
        <Route path="debug" element={<DebugPage />} />
        <Route path="debug/:view" element={<DebugPage />} />
        <Route path="debug/incidents/:ref" element={<DebugPage />} />
        <Route path="debug/agent/:sid" element={<DebugPage />} />
        <Route path="n/:ref" element={<NodeRoute />} />
        <Route path="n/:ref/:level" element={<NodeRoute />} />
        <Route path="*" element={<UnknownPage />} />
      </Routes>
    </FactoryLayout>
  );
}
