/**
 * "Pull requests opened this session" list.
 *
 * Managed sandbox sessions can open several PRs (across several repos) as the
 * agent works. This surfaces them at the top of the Files panel: it polls the
 * session-scoped endpoint and renders each PR as a link. It self-gates — when
 * GitHub isn't connected, the session isn't managed, or no PRs have been opened
 * yet, it renders nothing, so it's safe to mount unconditionally.
 */

import { useQuery } from "@tanstack/react-query";
import { ExternalLinkIcon, GitPullRequestIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { fetchSessionPulls } from "@/lib/githubIntegration";
import { cn } from "@/lib/utils";

/** Props for {@link SessionPullRequests}. */
interface SessionPullRequestsProps {
  /** The session/conversation whose PRs to show, or undefined (renders nothing). */
  conversationId: string | undefined;
}

export function SessionPullRequests({ conversationId }: SessionPullRequestsProps) {
  const { data } = useQuery({
    queryKey: ["session-pulls", conversationId],
    queryFn: () => fetchSessionPulls(conversationId as string),
    enabled: !!conversationId,
    // Poll so PRs the agent opens mid-session appear without a manual refresh.
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const pulls = data?.connected ? data.pulls : [];
  if (pulls.length === 0) {
    return null;
  }

  return (
    <div className="border-b border-border px-2 py-2" data-testid="session-pull-requests">
      <div className="flex items-center gap-1.5 px-1 pb-1.5 text-xs font-medium text-muted-foreground">
        <GitPullRequestIcon className="size-3.5 shrink-0" />
        Pull requests opened this session
      </div>
      <ul className="flex flex-col gap-0.5">
        {pulls.map((pr) => (
          <li key={`${pr.repo}#${pr.number}`}>
            <a
              href={pr.html_url ?? "#"}
              target="_blank"
              rel="noreferrer"
              className={cn(
                "group flex items-center gap-1.5 rounded-md px-1.5 py-1 text-xs",
                "text-foreground transition-colors hover:bg-muted",
              )}
              data-testid="session-pull-request"
            >
              <span className="shrink-0 text-muted-foreground">
                {pr.repo}#{pr.number}
              </span>
              <span className="min-w-0 flex-1 truncate">{pr.title ?? pr.head_ref ?? ""}</span>
              {pr.draft && (
                <Badge variant="secondary" className="shrink-0 text-[10px]">
                  draft
                </Badge>
              )}
              <ExternalLinkIcon className="size-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-60" />
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
