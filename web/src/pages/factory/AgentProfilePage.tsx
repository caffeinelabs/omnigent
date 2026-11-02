/**
 * An agent's full page in the new cockpit (`/factory/:factoryId/agents/:handle`):
 * the agent panel at page width, under a Teams › project › name breadcrumb.
 * The session id works in place of the handle; the eight debug tabs stay on
 * the classic Agent page (`?tab=…`).
 */

import { ChevronRightIcon } from "lucide-react";
import { useMemo } from "react";
import { AgentPanel } from "@/components/factory/agents/AgentPanel";
import { waitingOnViewer } from "@/components/factory/agents/fleetModel";
import { isMissingRoute, useFactoryAgents } from "@/components/factory/agents/opsQueries";
import { teamMember, teamRows } from "@/components/factory/agents/teamsModel";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { projectPageHref, teamsPageHref } from "@/components/factory/factoryRoutes";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { Link, useParams } from "@/lib/routing";

const norm = (id: string) => id.replace(/-/g, "").toLowerCase();

export function AgentProfilePage() {
  const { snapshot, nowS } = useFactoryShell();
  const fid = snapshot.factory.id;
  const { sid: ref = "" } = useParams<{ sid: string }>();
  const agents = useFactoryAgents(fid);
  const fallback = agents.error != null && !agents.data;
  const rows = useMemo(
    () => (agents.data || fallback ? teamRows(snapshot, agents.data?.data) : null),
    [agents.data, fallback, snapshot],
  );
  const waitingOnMe = useMemo(
    () => waitingOnViewer(snapshot.decisions, snapshot.viewer),
    [snapshot.decisions, snapshot.viewer],
  );
  const wanted = ref.replace(/^@/, "").toLowerCase();
  const row = rows?.find((r) => r.handle === wanted || norm(r.session_id) === norm(wanted)) ?? null;
  if (!rows) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading agent"
        className="h-96 animate-pulse rounded-xl bg-muted"
      />
    );
  }
  if (!row) {
    return (
      <EmptyState
        testId="agent-profile-missing"
        title="Agent not found"
        body={`@${wanted} is not in this factory, or you cannot see it.`}
        action={
          <Link
            to={teamsPageHref(fid)}
            componentId="factory.agent_profile.back"
            className="text-sm underline"
          >
            Back to Teams
          </Link>
        }
      />
    );
  }
  const member = teamMember(row, snapshot.factory.root_session_id, waitingOnMe);
  const project = row.project
    ? (snapshot.projects.find((p) => p.slug === row.project) ?? null)
    : null;
  return (
    <div
      data-testid="factory-agent-profile"
      className="mx-auto flex w-full max-w-4xl min-w-0 flex-col gap-3"
    >
      <nav
        aria-label="Breadcrumb"
        className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground"
      >
        <Link
          to={teamsPageHref(fid)}
          componentId="factory.agent_profile.crumb_teams"
          className="hover:text-foreground"
        >
          Teams
        </Link>
        {project && (
          <>
            <ChevronRightIcon aria-hidden className="size-3" />
            <Link
              to={projectPageHref(fid, project.slug)}
              componentId="factory.agent_profile.crumb_project"
              className="font-mono hover:text-foreground"
            >
              {project.slug}
            </Link>
          </>
        )}
        <ChevronRightIcon aria-hidden className="size-3" />
        <span aria-current="page" className="text-foreground">
          {member.name}
        </span>
      </nav>
      {agents.error != null && !isMissingRoute(agents.error) && (
        <FactoryLoadError
          error={agents.error}
          updatedAt={agents.dataUpdatedAt}
          onRetry={() => void agents.refetch()}
          what="the agent"
          testId="agent-profile-error"
          className="mb-0"
        />
      )}
      <section className="rounded-xl border border-border bg-card p-4">
        <AgentPanel
          factoryId={fid}
          member={member}
          memberView={snapshot.members.find((m) => m.session_id === row.session_id) ?? null}
          project={project}
          nowS={nowS}
          defVersion={snapshot.def_version ?? null}
          full
        />
      </section>
    </div>
  );
}
