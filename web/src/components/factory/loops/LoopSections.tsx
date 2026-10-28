/**
 * Where loops show: the PR page's strip (the PR's loops and its task's
 * implement loop) and the project page's Loops tab (every loop of the
 * project: one strip per PR, then the task and plan loops). Both read the
 * project's loops and their iteration spans; an older server without the
 * loops route falls back to the snapshot's loop chips and counts.
 */

import { useMemo } from "react";
import { RepeatIcon } from "lucide-react";
import type { FactorySnapshot, LoopChipView, PrView, ProjectView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import {
  isMissingRoute,
  useFactoryIssues,
  useFactoryLoops,
  useLoopSpans,
} from "../agents/opsQueries";
import { FactoryLoadError } from "../FactoryLoadError";
import { TONE_CHIP } from "../factoryFormat";
import { prPageHref } from "../factoryRoutes";
import { EmptyState } from "../project/projectParts";
import { LoopStrip } from "./LoopStrip";
import { loopLines, prLoops } from "./loopStripModel";

const DAY_MS = 86_400_000;

const CHIP_TONE: Record<LoopChipView["state"], keyof typeof TONE_CHIP> = {
  running: "active",
  converged: "pass",
  stalled: "warning",
  thrashing: "fail",
  exhausted: "fail",
  escalated: "warning",
  cancelled: "muted",
  superseded: "muted",
};

/** The snapshot's loop chips (older servers, and while the strip loads). */
export function LoopChips({ chips }: { chips: readonly LoopChipView[] }) {
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-1.5">
      {chips.map((loop) => (
        <span
          key={loop.id}
          data-testid={`pr-loop-${loop.kind}`}
          data-state={loop.state}
          className={cn(
            "inline-flex h-5 items-center gap-1 rounded-sm px-1.5 font-mono text-xs",
            TONE_CHIP[CHIP_TONE[loop.state]],
          )}
        >
          {loop.kind} {loop.iteration}
          {loop.limit != null ? `/${loop.limit}` : ""}
          {loop.state === "exhausted" ? " ⛔" : loop.state === "converged" ? " ✓" : ""}
        </span>
      ))}
    </span>
  );
}

function sinceOf(nowS: number): number {
  // Loops of a week cover a project's PRs; older ones show from their counts.
  return Math.floor((nowS * 1000 - 7 * DAY_MS) / 3_600_000) * 3_600_000;
}

/** The PR page's loop strip. */
export function PrLoopStrip({
  snapshot,
  pr,
  nowS,
}: {
  snapshot: FactorySnapshot;
  pr: PrView;
  nowS: number;
}) {
  const fid = snapshot.factory.id;
  const project = pr.project_slug;
  const loops = useFactoryLoops(fid, { project });
  const spans = useLoopSpans(fid, project, sinceOf(nowS));
  const issues = useFactoryIssues(fid, {
    class: "finding",
    status: ["open", "acked"],
    project: project ?? undefined,
  });
  const lines = useMemo(
    () =>
      loopLines(
        prLoops(loops.data?.data ?? [], pr),
        spans.data?.data ?? [],
        issues.data?.data ?? [],
      ),
    [loops.data, spans.data, issues.data, pr],
  );
  const chips = pr.loops ?? [];
  let body;
  if (loops.isLoading) {
    body = (
      <div
        aria-busy="true"
        aria-label="Loading loops"
        data-testid="pr-loops-loading"
        className="flex flex-col gap-1.5"
      >
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-6 animate-pulse rounded bg-muted" />
        ))}
      </div>
    );
  } else if (loops.error && !loops.data) {
    body = isMissingRoute(loops.error) ? (
      chips.length > 0 ? (
        <LoopChips chips={chips} />
      ) : (
        <Empty />
      )
    ) : (
      <>
        <FactoryLoadError
          error={loops.error}
          updatedAt={0}
          onRetry={() => void loops.refetch()}
          what="the loops"
          testId="pr-loops-error"
          className="mb-0"
        />
        {chips.length > 0 && <LoopChips chips={chips} />}
      </>
    );
  } else if (lines.length === 0) {
    body = <Empty />;
  } else {
    body = <LoopStrip snapshot={snapshot} lines={lines} title={null} testId="pr-loop-strip" />;
  }
  return (
    <section
      aria-label="Loops"
      data-testid="pr-loops"
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-4 max-md:p-3"
    >
      <h2 className="inline-flex items-center gap-1.5 text-ui font-semibold">
        <RepeatIcon aria-hidden className="size-3.5 text-muted-foreground" />
        Loops
        {loops.data?.engine_mode && (
          <span className="text-xs font-normal text-muted-foreground">
            · engine {loops.data.engine_mode}
          </span>
        )}
      </h2>
      {body}
    </section>
  );
}

function Empty() {
  return (
    <p data-testid="pr-loops-empty" className="text-sm text-muted-foreground">
      None reported yet: implement attempts, review rounds, verify and demo runs show here.
    </p>
  );
}

/** The project page's Loops tab: a strip per PR, then task and plan loops without a PR. */
export function ProjectLoops({
  snapshot,
  project,
  nowS,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  nowS: number;
}) {
  const fid = snapshot.factory.id;
  const loops = useFactoryLoops(fid, { project: project.slug });
  const spans = useLoopSpans(fid, project.slug, sinceOf(nowS));
  const issues = useFactoryIssues(fid, {
    class: "finding",
    status: ["open", "acked"],
    project: project.slug,
  });
  const prs = snapshot.prs.filter((pr) => pr.project_slug === project.slug);
  const groups = useMemo(() => {
    const all = loops.data?.data ?? [];
    const spanList = spans.data?.data ?? [];
    const findings = issues.data?.data ?? [];
    const used = new Set<string>();
    const perPr = prs
      .map((pr) => {
        const own = prLoops(all, pr);
        for (const loop of own) used.add(loop.id);
        return { pr, lines: loopLines(own, spanList, findings) };
      })
      .filter((g) => g.lines.length > 0);
    const rest = loopLines(
      all.filter((l) => !used.has(l.id)),
      spanList,
      findings,
    );
    return { perPr, rest };
  }, [loops.data, spans.data, issues.data, prs]);
  if (loops.isLoading) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading loops"
        data-testid="project-loops-loading"
        className="h-32 animate-pulse rounded-xl bg-muted"
      />
    );
  }
  if (loops.error && !loops.data && !isMissingRoute(loops.error)) {
    return (
      <FactoryLoadError
        error={loops.error}
        updatedAt={0}
        onRetry={() => void loops.refetch()}
        what="the loops"
        testId="project-loops-error"
        className="mb-0"
      />
    );
  }
  if (groups.perPr.length === 0 && groups.rest.length === 0) {
    return (
      <EmptyState
        testId="project-loops-empty"
        icon={<RepeatIcon aria-hidden className="size-6 text-muted-foreground/50" />}
        title="No loops reported yet"
        body="Implement, review, verify and demo loops show here with their attempts, budget and why they stopped."
      />
    );
  }
  const summary = project.loops_summary;
  return (
    <div data-testid="project-loops" className="flex min-w-0 flex-col gap-3">
      {summary && (
        <p
          data-testid="project-loops-summary"
          className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground tabular-nums"
        >
          <span>
            <span className="font-semibold text-session-active">{summary.running}</span> running
          </span>
          <span>
            <span className="font-semibold text-success">{summary.converged}</span> converged
          </span>
          <span>
            <span className={cn("font-semibold", summary.exhausted ? "text-destructive" : "")}>
              {summary.exhausted}
            </span>{" "}
            exhausted
          </span>
          <span>
            <span
              className={cn(
                "font-semibold",
                summary.stalled + summary.thrashing ? "text-warning" : "",
              )}
            >
              {summary.stalled + summary.thrashing}
            </span>{" "}
            stalled or thrashing
          </span>
          <span>
            <span className="font-semibold">{summary.escalated}</span> escalated
          </span>
        </p>
      )}
      {groups.perPr.map(({ pr, lines }) => (
        <div key={pr.id} className="min-w-0 rounded-xl border border-border bg-card p-4 max-md:p-3">
          <h3 className="mb-2 flex min-w-0 items-baseline gap-2 text-sm">
            <Link
              to={prPageHref(fid, pr.id)}
              componentId="factory.project.loops_pr"
              className="font-mono font-medium hover:underline"
            >
              !{pr.number}
            </Link>
            <span className="min-w-0 truncate text-muted-foreground">{pr.title}</span>
          </h3>
          <LoopStrip
            snapshot={snapshot}
            lines={lines}
            title={null}
            testId={`project-loops-pr-${pr.number}`}
          />
        </div>
      ))}
      {groups.rest.length > 0 && (
        <div className="min-w-0 rounded-xl border border-border bg-card p-4 max-md:p-3">
          <h3 className="mb-2 text-sm font-medium">Tasks and plan</h3>
          <LoopStrip
            snapshot={snapshot}
            lines={groups.rest}
            showSubject
            title={null}
            testId="project-loops-rest"
          />
        </div>
      )}
    </div>
  );
}
