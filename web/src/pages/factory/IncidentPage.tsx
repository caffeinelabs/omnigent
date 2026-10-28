/**
 * One incident or finding (`/factory/:factoryId/alerts/:ref`; `12` means
 * `INC-12`): what fired, on whom, and since when; its lifecycle levers and
 * runbook; the spans and events that preceded it on the target and its PL
 * (likely culprit steps, for a human to judge); its evidence; a root-cause
 * confirm that resolves it with the cause recorded as feedback; and
 * "Propose change", which waits for the Improvements page.
 */

import { useMemo, useState } from "react";
import { ChevronRightIcon, SirenIcon } from "lucide-react";
import {
  useFactoryIssue,
  useFactoryIssues,
  useIssueLifecycle,
} from "@/components/factory/agents/opsQueries";
import { kindPill } from "@/components/factory/activity/activityModel";
import {
  IssueLifecycleButtons,
  RunbookButtons,
  assigneeLabel,
} from "@/components/factory/alerts/IssueActions";
import { IssueTargetLink, SeverityIcon } from "@/components/factory/alerts/IssueCard";
import { PROPOSALS_PENDING } from "@/components/factory/alerts/runbookModel";
import { controlErrorMessage } from "@/components/factory/controls/controlModel";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { TONE_CHIP, formatElapsed } from "@/components/factory/factoryFormat";
import {
  alertPageHref,
  alertsPageHref,
  timelinePageHref,
} from "@/components/factory/factoryRoutes";
import { clock } from "@/components/factory/overview/LiveFeed";
import { EmptyState, SectionCard } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { LeverGated, ReasonGated } from "@/components/factory/shell/viewerCan";
import { DEFAULT_SHOW, buildTimeline } from "@/components/factory/timeline/timelineModel";
import { TimelineView } from "@/components/factory/timeline/TimelineView";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { showToast } from "@/components/ui/toast";
import type { IssuePageResponse, IssueView } from "@/lib/factoryApi";
import { Link, useParams } from "@/lib/routing";
import { cn } from "@/lib/utils";

/** Seeded root-cause taxonomy (server `ROOT_CAUSES`). */
export const ROOT_CAUSES = [
  "charter_unclear",
  "task_too_big",
  "env_flaky",
  "ci_flaky",
  "tool_error",
  "prompt_gap",
  "reviewer_miss",
  "waiting_on_human",
  "coordination",
  "platform_bug",
] as const;

/** `12` → `INC-12`; `F-7` and `INC-12` stay. */
export function issueRef(param: string): string {
  return /^\d+$/.test(param)
    ? `INC-${param}`
    : param.toUpperCase().startsWith("INC-") || /^F-\d+$/i.test(param)
      ? param.toUpperCase()
      : param;
}

function RootCause({ factoryId, issue }: { factoryId: string; issue: IssueView }) {
  const lifecycle = useIssueLifecycle(factoryId);
  const [cause, setCause] = useState<string | null>(issue.category);
  const [note, setNote] = useState("");
  const ref = issue.ref ?? issue.id;
  const resolved = issue.status === "resolved" || issue.status === "wontfix";
  if (resolved && issue.category) {
    return (
      <p data-testid="incident-root-cause" className="text-sm">
        Root cause: <span className="font-medium">{issue.category.replaceAll("_", " ")}</span>
        {issue.resolved_by && (
          <span className="text-muted-foreground">
            {" "}
            · confirmed by {assigneeLabel(issue.resolved_by)}
          </span>
        )}
      </p>
    );
  }
  return (
    <div data-testid="incident-root-cause" className="flex min-w-0 flex-col gap-2">
      <p className="text-sm text-muted-foreground">
        Automated attribution is unreliable: pick the cause you see in the steps above. It is
        recorded as feedback and feeds the factory’s retros.
      </p>
      <div role="radiogroup" aria-label="Root cause" className="flex flex-wrap gap-1">
        {ROOT_CAUSES.map((c) => (
          <button
            key={c}
            type="button"
            role="radio"
            aria-checked={cause === c}
            onClick={() => setCause(cause === c ? null : c)}
            data-testid={`incident-cause-${c}`}
            className={cn(
              "h-6 cursor-pointer rounded-md border px-2 text-xs focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
              cause === c
                ? "border-foreground bg-foreground text-background"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            {c.replaceAll("_", " ")}
          </button>
        ))}
      </div>
      <Textarea
        value={note}
        onChange={(event) => setNote(event.target.value)}
        placeholder="What happened (optional)"
        aria-label="Resolution note"
        className="min-h-12 text-sm"
      />
      <div className="flex flex-wrap items-center gap-2">
        <LeverGated lever="control">
          <Button
            size="sm"
            disabled={!cause || lifecycle.isPending || resolved}
            onClick={() =>
              lifecycle.mutate(
                {
                  ref,
                  change: {
                    action: "resolve",
                    root_cause: cause!,
                    ...(note.trim() ? { note: note.trim() } : {}),
                  },
                },
                {
                  onSuccess: () =>
                    showToast(`${ref} resolved · cause ${cause!.replaceAll("_", " ")}`),
                  onError: (error) => showToast(`${ref}: ${controlErrorMessage(error)}`),
                },
              )
            }
            data-testid="incident-confirm-cause"
            componentId="factory.incident.confirm_cause"
          >
            Confirm cause & resolve
          </Button>
        </LeverGated>
        <ReasonGated reason={PROPOSALS_PENDING} testId="propose">
          <Button
            size="sm"
            variant="outline"
            data-testid="incident-propose"
            componentId="factory.incident.propose"
          >
            Propose change
          </Button>
        </ReasonGated>
      </div>
    </div>
  );
}

function Evidence({ factoryId, page }: { factoryId: string; page: IssuePageResponse }) {
  const items = Array.isArray(page.issue.evidence)
    ? page.issue.evidence
    : page.issue.evidence
      ? [page.issue.evidence]
      : [];
  if (items.length === 0)
    return <p className="text-sm text-muted-foreground">No evidence attached.</p>;
  return (
    <ul data-testid="incident-evidence" className="flex flex-col gap-1 text-sm">
      {items.map((item) => {
        const text = typeof item === "string" ? item : JSON.stringify(item);
        const span =
          typeof item === "string" && item.startsWith("span:")
            ? page.spans.find((s) => s.id === item.slice(5))
            : null;
        return (
          <li key={text} className="min-w-0 break-words">
            {span ? (
              <Link
                to={timelinePageHref(factoryId, {
                  at: (span.ended_at ?? span.started_at) + 1,
                  ...(span.project_slug ? { project: span.project_slug } : {}),
                })}
                componentId="factory.incident.evidence_span"
                className="font-mono text-xs hover:underline"
              >
                {span.kind} · {span.name} · {clock(span.started_at)}
              </Link>
            ) : (
              <span className="font-mono text-xs">{text}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function IncidentPage() {
  const { ref: param = "" } = useParams<{ ref: string }>();
  const ref = issueRef(decodeURIComponent(param));
  const { snapshot, nowS } = useFactoryShell();
  const fid = snapshot.factory.id;
  const query = useFactoryIssue(fid, ref);
  const page = query.data;
  // The issues list follows every `issues` frame; the page's own copy may lag.
  const related = useFactoryIssues(fid, {}, { enabled: Boolean(page) });
  const issue = page
    ? (related.data?.data.find((i) => i.id === page.issue.id) ?? page.issue)
    : undefined;
  const range = useMemo(() => {
    if (!issue) return null;
    const to = ((issue.resolved_at ?? issue.last_seen) + 60) * 1000;
    const earliest = page!.spans.reduce(
      (min, s) => Math.min(min, s.started_at),
      issue.first_seen * 1000 - 30 * 60_000,
    );
    return { from: Math.max(earliest, issue.first_seen * 1000 - 2 * 3_600_000), to };
  }, [issue, page]);
  const groups = useMemo(
    () =>
      page && range
        ? buildTimeline(page.spans, snapshot, {
            ...range,
            show: { ...DEFAULT_SHOW, heartbeats: true },
            replay: true,
            // Only the incident's agents (its target and PL) and their loops.
            agents: page.sessions.map((s) => s.session_id),
            project: page.issue.project_slug,
          })
        : [],
    [page, range, snapshot],
  );
  if (query.isLoading) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading the incident"
        data-testid="incident-loading"
        className="flex flex-col gap-3"
      >
        <div className="h-4 w-40 animate-pulse rounded bg-muted" />
        <div className="h-7 w-96 max-w-full animate-pulse rounded bg-muted" />
        <div className="h-24 animate-pulse rounded-xl bg-muted" />
        <div className="h-64 animate-pulse rounded-xl bg-muted" />
      </div>
    );
  }
  if (!page || !issue) {
    if (query.error && (query.error as { status?: number }).status !== 404) {
      return (
        <FactoryLoadError
          error={query.error}
          updatedAt={0}
          onRetry={() => void query.refetch()}
          what={ref}
          testId="incident-error"
        />
      );
    }
    return (
      <EmptyState
        testId="incident-missing"
        icon={<SirenIcon aria-hidden className="size-6 text-muted-foreground/50" />}
        title={`No ${ref} in this factory`}
        body={
          <Link
            to={alertsPageHref(fid)}
            componentId="factory.incident.back"
            className="underline underline-offset-2 hover:text-foreground"
          >
            Back to Alerts
          </Link>
        }
      />
    );
  }
  const others = (related.data?.data ?? []).filter(
    (i) => i.id !== issue.id && i.target === issue.target,
  );
  const open = issue.status !== "resolved" && issue.status !== "wontfix";
  return (
    <article
      data-testid="factory-incident-page"
      data-ref={issue.ref ?? issue.id}
      data-status={issue.status}
      className="flex min-w-0 flex-col gap-4"
    >
      <header className="flex min-w-0 flex-col gap-2">
        <nav aria-label="Breadcrumb">
          <ol className="flex min-w-0 items-center gap-1 text-sm text-muted-foreground">
            <li>
              <Link
                to={alertsPageHref(fid)}
                componentId="factory.incident.crumb"
                className="hover:text-foreground"
              >
                Alerts
              </Link>
            </li>
            <li className="flex items-center gap-1" aria-current="page">
              <ChevronRightIcon aria-hidden className="size-3.5 opacity-60" />
              <span className="font-mono text-foreground">{issue.ref ?? issue.class}</span>
            </li>
          </ol>
        </nav>
        <div className="flex min-w-0 flex-col gap-2 md:flex-row md:items-start md:justify-between">
          <div className="flex min-w-0 flex-col gap-1.5">
            <h1 className="flex min-w-0 items-start gap-2 text-xl leading-snug font-semibold">
              <SeverityIcon issue={issue} className="mt-1.5 size-4" />
              <span className="min-w-0 break-words">
                <span className="font-mono text-muted-foreground">{issue.ref}</span> {issue.summary}
              </span>
            </h1>
            <p
              data-testid="incident-meta"
              className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground"
            >
              <IssueTargetLink snapshot={snapshot} issue={issue} />
              <span
                className={cn(
                  "inline-flex h-5 items-center rounded-sm px-1.5 text-xs font-medium",
                  open ? TONE_CHIP.warning : TONE_CHIP.pass,
                )}
              >
                {issue.status}
              </span>
              <span className="font-mono text-xs">{issue.rule}</span>
              <span>· {issue.severity}</span>
              <span>
                · first {formatElapsed(nowS - issue.first_seen) ?? "just now"} ago
                {issue.count > 1 ? ` · ${issue.count}×` : ""}
              </span>
              <span>· for {assigneeLabel(issue.assignee)}</span>
              {issue.acked_by && <span>· acked by {assigneeLabel(issue.acked_by)}</span>}
              {page.mine && (
                <span className={cn("rounded-sm px-1 text-xs", TONE_CHIP.active)}>yours</span>
              )}
            </p>
            {issue.body && <p className="max-w-prose text-sm break-words">{issue.body}</p>}
          </div>
          <IssueLifecycleButtons factoryId={fid} issue={issue} />
        </div>
      </header>
      {open && issue.runbook.length > 0 && (
        <SectionCard title="Runbook" meta="each action notifies the PL" testId="incident-runbook">
          <RunbookButtons issue={issue} size="sm" />
        </SectionCard>
      )}
      <SectionCard
        title="Before the incident"
        meta={`spans and events on ${page.sessions.map((s) => s.title ?? s.session_id.slice(0, 8)).join(" and ") || "the project"}`}
        testId="incident-timeline"
      >
        {groups.length > 0 && range ? (
          <TimelineView
            factoryId={fid}
            groups={groups}
            from={range.from}
            to={range.to}
            replayAt={issue.first_seen * 1000}
            testId="incident-swimlanes"
          />
        ) : (
          <p className="text-sm text-muted-foreground">
            No spans recorded on its agents in the two hours before it.
          </p>
        )}
        {page.events.length > 0 && (
          <ol data-testid="incident-events" className="flex max-h-80 flex-col overflow-y-auto">
            {page.events.map((event) => (
              <li
                key={event.id}
                className="grid min-w-0 grid-cols-[4.25rem_minmax(0,1fr)] items-baseline gap-x-2 py-0.5 text-sm"
              >
                <span className="font-mono text-xs text-muted-foreground tabular-nums">
                  {clock(event.ts)}
                </span>
                <span className="min-w-0 truncate" title={event.summary}>
                  <span
                    className={cn(
                      "mr-1.5 font-mono text-xs",
                      event.severity === "warning" || event.severity === "critical"
                        ? "text-warning"
                        : "text-muted-foreground",
                    )}
                  >
                    {kindPill(event)}
                  </span>
                  {event.summary}
                </span>
              </li>
            ))}
          </ol>
        )}
      </SectionCard>
      <div className="grid min-w-0 grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <SectionCard title="Root cause" testId="incident-cause-card">
          <RootCause key={issue.id} factoryId={fid} issue={issue} />
        </SectionCard>
        <div className="flex min-w-0 flex-col gap-4">
          <SectionCard title="Evidence" testId="incident-evidence-card">
            <Evidence factoryId={fid} page={page} />
          </SectionCard>
          <SectionCard title="Linked" meta="on the same target" testId="incident-linked">
            {others.length === 0 ? (
              <p className="text-sm text-muted-foreground">Nothing else on it.</p>
            ) : (
              <ul className="flex flex-col gap-1 text-sm">
                {others.map((other) => (
                  <li key={other.id} className="flex min-w-0 items-baseline gap-2">
                    <Link
                      to={alertPageHref(fid, other.ref ?? other.id)}
                      componentId="factory.incident.linked"
                      className="shrink-0 font-mono hover:underline"
                    >
                      {other.ref ?? other.class}
                    </Link>
                    <span className="min-w-0 truncate">{other.summary}</span>
                    <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                      {other.status}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>
        </div>
      </div>
    </article>
  );
}
