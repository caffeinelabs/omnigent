/**
 * One incident or finding in the Alerts list: severity, ref, what it is on
 * (an agent link), the summary and how long it has been going, who it is
 * assigned to, its lifecycle levers and its runbook. The ref opens the
 * incident page.
 */

import {
  CircleAlertIcon,
  CircleXIcon,
  InfoIcon,
  RepeatIcon,
  TriangleAlertIcon,
} from "lucide-react";
import type { FactorySnapshot, IssueView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { formatElapsed } from "../factoryFormat";
import { agentPageHref, alertPageHref, prPageHref, projectPageHref } from "../factoryRoutes";
import { IssueLifecycleButtons, RunbookButtons, assigneeLabel } from "./IssueActions";
import { issueMember, issueTarget } from "./runbookModel";

export function SeverityIcon({
  issue,
  className,
}: {
  issue: Pick<IssueView, "severity" | "rule">;
  className?: string;
}) {
  if (issue.rule === "agent.repeat" || issue.rule.startsWith("loop.")) {
    return (
      <RepeatIcon
        aria-label={issue.severity}
        className={cn("size-3.5 shrink-0 text-warning", className)}
      />
    );
  }
  switch (issue.severity) {
    case "critical":
      return (
        <CircleXIcon
          aria-label="critical"
          className={cn("size-3.5 shrink-0 text-destructive", className)}
        />
      );
    case "warning":
      return (
        <TriangleAlertIcon
          aria-label="warning"
          className={cn("size-3.5 shrink-0 text-warning", className)}
        />
      );
    case "notice":
      return (
        <CircleAlertIcon
          aria-label="notice"
          className={cn("size-3.5 shrink-0 text-session-active", className)}
        />
      );
    default:
      return (
        <InfoIcon
          aria-label="info"
          className={cn("size-3.5 shrink-0 text-muted-foreground", className)}
        />
      );
  }
}

/** A link to what the issue is on: the agent, the PR or the project. */
export function IssueTargetLink({
  snapshot,
  issue,
}: {
  snapshot: FactorySnapshot;
  issue: IssueView;
}) {
  const fid = snapshot.factory.id;
  const [kind, id] = issueTarget(issue);
  const member = issueMember(snapshot, issue);
  const label = issue.target_title ?? member?.title ?? null;
  const className =
    "shrink-0 rounded-sm bg-muted px-1.5 py-0.5 font-mono text-xs hover:bg-muted/70 hover:underline";
  if (member) {
    return (
      <Link
        to={agentPageHref(fid, member.session_id)}
        componentId="factory.issue.target"
        data-testid={`issue-target-${issue.ref ?? issue.id}`}
        className={className}
      >
        {label ?? member.title}
      </Link>
    );
  }
  if (kind === "pr") {
    const pr = snapshot.prs.find((p) => p.id.replace(/-/g, "") === id.replace(/-/g, ""));
    if (pr) {
      return (
        <Link to={prPageHref(fid, pr.id)} componentId="factory.issue.target" className={className}>
          !{pr.number}
        </Link>
      );
    }
  }
  if (issue.project_slug) {
    return (
      <Link
        to={projectPageHref(fid, issue.project_slug)}
        componentId="factory.issue.target"
        className={className}
      >
        #{issue.project_slug}
      </Link>
    );
  }
  return label ? <span className={className}>{label}</span> : null;
}

export function IssueCard({
  snapshot,
  issue,
  nowS,
}: {
  snapshot: FactorySnapshot;
  issue: IssueView;
  nowS: number;
}) {
  const fid = snapshot.factory.id;
  const ref = issue.ref ?? issue.id;
  const open = issue.status !== "resolved" && issue.status !== "wontfix";
  const age = formatElapsed(nowS - issue.first_seen);
  return (
    <li
      data-testid={`issue-card-${ref}`}
      data-status={issue.status}
      data-severity={issue.severity}
      className={cn(
        "flex min-w-0 flex-col gap-2 border-b border-border px-4 py-3 last:border-0 max-md:px-3",
        issue.severity === "critical" && open && "bg-destructive/[0.04]",
      )}
    >
      <div className="flex min-w-0 flex-col gap-2 md:flex-row md:items-start">
        <div className="flex min-w-0 flex-1 items-start gap-2">
          <SeverityIcon issue={issue} className="mt-0.5" />
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
              <Link
                to={alertPageHref(fid, ref)}
                componentId="factory.issue.open"
                data-testid={`issue-open-${ref}`}
                className="shrink-0 font-mono font-medium hover:underline"
              >
                {issue.ref ?? issue.class}
              </Link>
              <IssueTargetLink snapshot={snapshot} issue={issue} />
              <span className="min-w-0 break-words">{issue.summary}</span>
            </div>
            <p className="flex min-w-0 flex-wrap gap-x-2 text-xs text-muted-foreground">
              <span className="font-mono">{issue.rule}</span>
              <span>
                · {issue.count > 1 ? `${issue.count}× · ` : ""}
                {age ?? "just now"}
              </span>
              <span>
                ·{" "}
                {issue.status === "acked" && issue.acked_by
                  ? `acked by ${assigneeLabel(issue.acked_by)}`
                  : issue.status}
              </span>
              <span>· for {assigneeLabel(issue.assignee)}</span>
              {issue.status === "resolved" && issue.resolved_by && (
                <span>· resolved by {assigneeLabel(issue.resolved_by)}</span>
              )}
              {issue.category && <span>· cause {issue.category.replaceAll("_", " ")}</span>}
            </p>
          </div>
        </div>
        <IssueLifecycleButtons factoryId={fid} issue={issue} />
      </div>
      {open && issue.runbook.length > 0 && (
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 pl-5.5">
          <span className="text-xs text-muted-foreground">Runbook</span>
          <RunbookButtons issue={issue} />
        </div>
      )}
    </li>
  );
}
