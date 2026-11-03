/**
 * The agent panel of a page that lists teams (`?peek=agent:<handle>`): a
 * ring on a project card opens it; Full page goes to the agent's page.
 */

import { ExternalLinkIcon, UsersIcon } from "lucide-react";
import { useCallback } from "react";
import { Button } from "@/components/ui/button";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { Link, useSearchParams } from "@/lib/routing";
import { AgentPanel } from "../agents/AgentPanel";
import type { TeamMember } from "../agents/teamsModel";
import { agentPageHref } from "../factoryRoutes";
import { EmptyState } from "../project/projectParts";
import { Peek } from "../ui/Peek";

export const AGENT_PEEK = "agent:";

/** Read and set `?peek=` (a new history entry on open, replaced on close). */
export function usePeekParam(): [string | null, (value: string | null) => void] {
  const [params, setParams] = useSearchParams();
  const set = useCallback(
    (value: string | null) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set("peek", value);
          else next.delete("peek");
          return next;
        },
        { replace: value == null },
      ),
    [setParams],
  );
  return [params.get("peek"), set];
}

/** The `?peek=` value that opens *member*'s panel. */
export function agentPeek(member: TeamMember): string {
  return `${AGENT_PEEK}${member.row.handle ?? member.row.session_id}`;
}

export function AgentPeek({
  snapshot,
  members,
  nowS,
}: {
  snapshot: FactorySnapshot;
  members: readonly TeamMember[];
  nowS: number;
}) {
  const fid = snapshot.factory.id;
  const [peek, setPeek] = usePeekParam();
  const ref = peek?.startsWith(AGENT_PEEK) ? peek.slice(AGENT_PEEK.length).toLowerCase() : null;
  const agent = ref
    ? (members.find((m) => m.row.handle === ref || m.row.session_id === ref) ?? null)
    : null;
  return (
    <Peek
      open={ref != null}
      onOpenChange={(open) => !open && setPeek(null)}
      title={
        <span className="text-sm font-normal text-muted-foreground">
          {agent ? (agent.row.project ?? "Agent") : "Agent"}
        </span>
      }
      headerExtra={
        agent ? (
          <Button asChild variant="ghost" size="sm">
            <Link
              to={agentPageHref(fid, agent.row.handle ?? agent.row.session_id)}
              componentId="factory.projects.peek_full_page"
            >
              Full page
              <ExternalLinkIcon aria-hidden className="size-3.5" />
            </Link>
          </Button>
        ) : undefined
      }
    >
      {ref != null && !agent && (
        <EmptyState
          testId="projects-agent-missing"
          icon={<UsersIcon aria-hidden className="size-6 text-muted-foreground/50" />}
          title="Not in this factory"
          body={`No agent here is called “${ref}”, or you cannot see it.`}
        />
      )}
      {agent && (
        <AgentPanel
          factoryId={fid}
          member={agent}
          memberView={snapshot.members.find((m) => m.session_id === agent.row.session_id) ?? null}
          project={
            agent.row.project
              ? (snapshot.projects.find((p) => p.slug === agent.row.project) ?? null)
              : null
          }
          nowS={nowS}
          defVersion={snapshot.def_version ?? null}
        />
      )}
    </Peek>
  );
}
