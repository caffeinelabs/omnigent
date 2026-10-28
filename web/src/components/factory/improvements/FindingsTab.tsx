/**
 * Improvements ▸ Findings: the process gaps detectors and Sentinel found,
 * and Sentinel's retros. Each label (root cause) asks "Is this right?": a
 * person's confirmation or correction goes to the next retro as a few-shot
 * example. "Propose change" turns a finding into a definition change.
 */

import { type ReactNode, useMemo } from "react";
import { SearchCheckIcon } from "lucide-react";
import type { IssueView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { useFactoryIssues } from "../agents/opsQueries";
import { CalibrationButtons } from "../feedback/FeedbackWidgets";
import { tagLabel } from "../feedback/feedbackModel";
import { FactoryLoadError } from "../FactoryLoadError";
import { formatElapsed } from "../factoryFormat";
import { alertPageHref } from "../factoryRoutes";
import { EmptyState, SectionCard } from "../project/projectParts";

function IssueRow({
  factoryId,
  issue,
  nowS,
  action,
}: {
  factoryId: string;
  issue: IssueView;
  nowS: number;
  action?: ReactNode;
}) {
  const ref = issue.ref ?? issue.id.slice(0, 8);
  return (
    <li
      data-testid={`finding-${issue.ref ?? issue.id}`}
      className="flex min-w-0 flex-col gap-1 py-2 text-sm"
    >
      <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
        {issue.ref ? (
          <Link
            to={alertPageHref(factoryId, issue.ref)}
            componentId="factory.findings.open"
            className="font-mono text-xs text-muted-foreground hover:text-foreground hover:underline"
          >
            {ref}
          </Link>
        ) : (
          <span className="text-xs text-muted-foreground">retro</span>
        )}
        <span className="min-w-0 flex-1 break-words">{issue.summary}</span>
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
          {issue.project_slug ? `#${issue.project_slug} · ` : ""}
          {issue.count > 1 ? `×${issue.count} · ` : ""}
          {formatElapsed(nowS - issue.last_seen)} ago · {issue.source}
        </span>
      </span>
      <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-xs text-muted-foreground">
          label:{" "}
          <span className="font-medium text-foreground">
            {issue.category ? tagLabel(issue.category) : issue.rule.replaceAll("_", " ")}
          </span>
        </span>
        <CalibrationButtons
          factoryId={factoryId}
          issueId={issue.id}
          label={issue.category}
          project={issue.project_slug}
          testId={`calibrate-${issue.ref ?? issue.id}`}
        />
        {action && <span className="ml-auto">{action}</span>}
      </span>
    </li>
  );
}

export function FindingsTab({
  factoryId,
  nowS,
  proposeFor,
}: {
  factoryId: string;
  nowS: number;
  proposeFor: (issue: IssueView) => ReactNode;
}) {
  const findings = useFactoryIssues(factoryId, {
    class: "finding",
    status: ["open", "acked"],
    limit: 200,
  });
  const retros = useFactoryIssues(factoryId, { class: "retro", limit: 50 });
  const rows = useMemo(
    () =>
      [...(findings.data?.data ?? [])].sort(
        (a, b) => b.count - a.count || b.last_seen - a.last_seen,
      ),
    [findings.data],
  );
  if (findings.isLoading && !findings.data) {
    return (
      <div aria-busy="true" data-testid="findings-loading" className="flex flex-col gap-2">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-14 animate-pulse rounded-lg bg-muted/70" />
        ))}
      </div>
    );
  }
  if (findings.error && !findings.data) {
    return (
      <FactoryLoadError
        error={findings.error}
        updatedAt={0}
        onRetry={() => void findings.refetch()}
        what="the findings"
        testId="findings-error"
      />
    );
  }
  return (
    <div data-testid="findings-tab" className="flex min-w-0 flex-col gap-3">
      <SectionCard
        title="Findings"
        meta={`${rows.length} open · most frequent first`}
        testId="findings-list"
      >
        {rows.length === 0 ? (
          <EmptyState
            testId="findings-empty"
            icon={<SearchCheckIcon aria-hidden className="size-6 text-muted-foreground/50" />}
            title="No open findings"
            body="Detectors and Sentinel's daily retro file findings: flaky CI, ping-pong mail, unneeded questions, wasted heartbeats."
          />
        ) : (
          <ul className="flex min-w-0 flex-col divide-y divide-border">
            {rows.map((issue) => (
              <IssueRow
                key={issue.id}
                factoryId={factoryId}
                issue={issue}
                nowS={nowS}
                action={proposeFor(issue)}
              />
            ))}
          </ul>
        )}
      </SectionCard>
      <SectionCard
        title="Retros"
        meta="Sentinel's error analysis, newest first"
        testId="retros-list"
      >
        {(retros.data?.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No retro yet: Sentinel files one each day and when a project is done.
          </p>
        ) : (
          <ul className="flex min-w-0 flex-col divide-y divide-border">
            {(retros.data?.data ?? []).map((issue) => (
              <IssueRow key={issue.id} factoryId={factoryId} issue={issue} nowS={nowS} />
            ))}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}
