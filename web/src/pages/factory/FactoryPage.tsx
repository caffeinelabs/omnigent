/**
 * Everything under `/factory/:factoryId/*`: the factory shell (header, tabs,
 * dock, presence) around one page. Overview at the root, then Projects, a
 * project, a PR, Activity, the Room and Settings ▸ People. Old cockpit links
 * (`?project=`, `?review=`, `?pr=…&preview=unavailable`, `?tab=activity`)
 * are sent to their pages by the shell.
 */

import { CompassIcon, MessagesSquareIcon } from "lucide-react";
import { Navigate, Route, Routes } from "react-router-dom";
import { ActivityList } from "@/components/factory/activity/ActivityList";
import { DecisionsRail } from "@/components/factory/decisions/DecisionsRail";
import {
  SETTINGS_SECTIONS,
  factoryPageHref,
  settingsPageHref,
} from "@/components/factory/factoryRoutes";
import { PeopleSettings } from "@/components/factory/people/PeopleSettings";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { RoomSlot, dockThread } from "@/components/factory/shell/RoomDock";
import { Link, useParams, useSearchParams } from "@/lib/routing";
import { FactoryLayout } from "./FactoryLayout";
import { OverviewPage } from "./OverviewPage";
import { PrPage } from "./PrPage";
import { ProjectPage } from "./ProjectPage";
import { ProjectsPage } from "./ProjectsPage";

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

/** `/room/:thread?`: the Room's page; until it lands, the thread's slot and the decisions. */
function RoomPage() {
  const { snapshot, location, clockOffsetS } = useFactoryShell();
  const [params] = useSearchParams();
  const thread = location.id
    ? {
        key: location.id,
        title: location.id.startsWith("project:")
          ? `#${location.id.slice(8)}`
          : location.id.startsWith("pr:")
            ? dockThread(snapshot, { page: "pr", id: location.id.slice(3) }).title
            : `#${location.id}`,
      }
    : { key: "factory", title: "#factory" };
  return (
    <div
      data-testid="factory-room-page"
      className="grid min-w-0 grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]"
    >
      <div className="flex min-w-0 flex-col gap-3">
        <h2 className="inline-flex items-center gap-2 text-base font-semibold">
          <MessagesSquareIcon aria-hidden className="size-4 text-muted-foreground" />
          Room
        </h2>
        <RoomSlot thread={thread} />
      </div>
      <div className="flex min-w-0 flex-col gap-3">
        <h2 className="text-base font-semibold">Decisions</h2>
        <DecisionsRail
          factoryId={snapshot.factory.id}
          decisions={snapshot.decisions}
          highlightId={params.get("decision")}
          clockOffsetS={clockOffsetS}
        />
      </div>
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
        <Route path="activity" element={<ActivityPage />} />
        <Route path="room" element={<RoomPage />} />
        <Route path="room/:thread" element={<RoomPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="settings/:section" element={<SettingsPage />} />
        <Route path="*" element={<UnknownPage />} />
      </Routes>
    </FactoryLayout>
  );
}
