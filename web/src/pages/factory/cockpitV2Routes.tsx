/**
 * Routes of the new cockpit inside today's shell, dark unless the server
 * offers it and the viewer switched to it (see `cockpitV2`): Teams, and the
 * agent's full page in place of the classic Agent page. The classic page
 * still answers its debug tabs (`?tab=`, Live included) and replay links
 * (`?at=`), and `/teams` without the new cockpit lands on the fleet.
 */

import { Navigate } from "react-router-dom";
import { agentsPageHref } from "@/components/factory/factoryRoutes";
import { useCockpitV2 } from "@/components/factory/shell/cockpitV2";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { useSearchParams } from "@/lib/routing";
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
