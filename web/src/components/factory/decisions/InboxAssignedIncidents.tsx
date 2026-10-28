/**
 * Incidents assigned to the viewer (or a role they hold) across every
 * factory they can open, for the Inbox's "Assigned to you": one line each,
 * opening the incident page. Acknowledging and the runbook live there.
 */

import { useQueries } from "@tanstack/react-query";
import { useFactories } from "@/hooks/useFactory";
import { type IssueView, fetchFactoryIssues } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { SeverityIcon } from "../alerts/IssueCard";
import { formatElapsed } from "../factoryFormat";
import { alertPageHref } from "../factoryRoutes";

export interface AssignedIncident {
  factory: { id: string; name: string };
  issue: IssueView;
}

const MINE_LIMIT = 20;
const POLL_MS = 30_000;

/** Open or acknowledged incidents for the viewer, newest first (empty until loaded). */
export function useAssignedIncidents(enabled = true): AssignedIncident[] {
  const factories = useFactories({ enabled }).data?.data ?? [];
  const results = useQueries({
    queries: factories.map((factory) => ({
      queryKey: ["factory-room", factory.id, "issues", "mine"],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchFactoryIssues(
          factory.id,
          { class: "incident", status: ["open", "acked"], assigned: "me", limit: MINE_LIMIT },
          signal,
        ),
      enabled,
      refetchInterval: POLL_MS,
      retry: false,
    })),
  });
  const out: AssignedIncident[] = [];
  results.forEach((result, i) => {
    const factory = factories[i];
    if (!factory || !result.data) return;
    for (const issue of result.data.data) {
      out.push({ factory: { id: factory.id, name: factory.name }, issue });
    }
  });
  return out.sort((a, b) => b.issue.last_seen - a.issue.last_seen);
}

export function InboxAssignedIncidents({
  incidents,
  nowS,
}: {
  incidents: AssignedIncident[];
  nowS: number;
}) {
  if (incidents.length === 0) return null;
  return (
    <ul data-testid="inbox-assigned-incidents" className="flex flex-col gap-1">
      {incidents.map(({ factory, issue }) => (
        <li key={issue.id}>
          <Link
            to={alertPageHref(factory.id, issue.ref ?? issue.id)}
            componentId="inbox.factory.incident"
            data-testid={`inbox-incident-${issue.ref ?? issue.id}`}
            className="flex min-w-0 items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm hover:bg-muted/50"
          >
            <SeverityIcon issue={issue} />
            <span className="shrink-0 font-mono text-xs">{issue.ref}</span>
            <span className="shrink-0 text-xs text-muted-foreground max-sm:hidden">
              {factory.name}
            </span>
            <span className="min-w-0 flex-1 truncate">{issue.summary}</span>
            {issue.status === "acked" && (
              <span className="shrink-0 text-xs text-muted-foreground">acked</span>
            )}
            <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
              {formatElapsed(nowS - issue.first_seen)}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
