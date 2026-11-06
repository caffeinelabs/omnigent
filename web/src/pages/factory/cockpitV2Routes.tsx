/**
 * Routes of the new cockpit inside today's shell, dark unless the server
 * offers it and the viewer switched to it (see `cockpitV2`): Home in place of
 * the Overview (cockpit v3: the product's node page), Teams, the agent's full page in place of the classic Agent
 * page, and a project's team editor. The classic page still answers its debug
 * tabs (`?tab=`, Live included) and replay links (`?at=`), `/teams` without
 * the new cockpit lands on the fleet, and `/projects/:slug/team` on the
 * project page's Team section.
 */

import { Navigate } from "react-router-dom";
import { agentsPageHref, projectPageHref } from "@/components/factory/factoryRoutes";
import { useCockpitV2 } from "@/components/factory/shell/cockpitV2";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { TeamEditor } from "@/components/factory/team/TeamEditor";
import { useParams, useSearchParams } from "@/lib/routing";
import { AgentPage } from "./AgentPage";
import { AgentProfilePage } from "./AgentProfilePage";
import { TeamsPage } from "./TeamsPage";

function Pending() {
  return (
    <div aria-busy="true" aria-label="Loading" className="h-96 animate-pulse rounded-xl bg-muted" />
  );
}

/** `/teams[/:tab]`: Teams with the new cockpit, else the fleet. */
export function TeamsRoute() {
  const { snapshot } = useFactoryShell();
  const cockpit = useCockpitV2();
  if (cockpit.loading) return <Pending />;
  if (!cockpit.on) return <Navigate to={agentsPageHref(snapshot.factory.id)} replace />;
  return <TeamsPage />;
}

/** `/agents/:sid`: the agent's full page with the new cockpit (no `?tab=` or `?at=`), else the classic page. */
export function AgentRoute() {
  const cockpit = useCockpitV2();
  const [params] = useSearchParams();
  if (params.has("tab") || params.has("at")) return <AgentPage />;
  if (cockpit.loading) return <Pending />;
  return cockpit.on ? <AgentProfilePage /> : <AgentPage />;
}

/** `/projects/:slug/team`: the team editor with the new cockpit, else the project's Team section. */
export function ProjectTeamRoute() {
  const { slug = "" } = useParams<{ slug: string }>();
  const { snapshot } = useFactoryShell();
  const cockpit = useCockpitV2();
  const fid = snapshot.factory.id;
  if (cockpit.loading) return <Pending />;
  if (!cockpit.on) return <Navigate to={projectPageHref(fid, slug, { section: "team" })} replace />;
  const project = snapshot.projects.find((p) => p.slug === slug);
  // The project page says when a project is missing or hidden.
  if (!project) return <Navigate to={projectPageHref(fid, slug)} replace />;
  return <TeamEditor snapshot={snapshot} project={project} />;
}
