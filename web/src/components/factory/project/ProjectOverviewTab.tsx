/**
 * The project's Overview tab (new cockpit). Left: a stopped project's red
 * card (its next step from the controls, and Close project), the lead's
 * latest structured update next to the health the facts give, Proof (the
 * newest demo), and the pull requests with their gates in words. Right: what
 * waits on you (the same decisions Home lists, answered here), a ready merge
 * with the merge policy from the product profile, how the work will be
 * measured, and spend by member.
 */

import { CheckIcon, ExternalLinkIcon, PlayIcon, TriangleAlertIcon } from "lucide-react";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { factoryQueryKey } from "@/hooks/useFactory";
import type { DemoStatus, FactorySnapshot, ProjectView, PrView } from "@/lib/factoryApi";
import { closeFactoryProject } from "@/lib/factoryProjectsApi";
import { Link, useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { projectPrList } from "../board/boardModel";
import { DemoThumb } from "../board/DemoThumb";
import { controlErrorMessage } from "../controls/controlModel";
import { useProjectControlParts } from "../controls/ProjectControlsMenu";
import { DecisionsRail } from "../decisions/DecisionsRail";
import { formatElapsed } from "../factoryFormat";
import { prPageHref, projectTabHref } from "../factoryRoutes";
import { WrapUpRating } from "../feedback/FeedbackWidgets";
import { ReportProblemButton } from "../feedback/ReportProblem";
import { money } from "../home/homeModel";
import { SECTION_SCROLL_MARGIN } from "../projects/ProjectSections";
import { IMergedItButton } from "../review/ReviewActions";
import { useViewerCan } from "../shell/viewerCan";
import { DATA_HUE, FOCUS_RING, MEANING } from "../ui/statusTokens";
import { useFactoryToast } from "../ui/Toast";
import { HealthChip } from "./HealthChip";
import {
  type HealthChip as HealthChipData,
  HEALTH_WORD,
  gateWords,
  measurementPlan,
  projectBudget,
  projectDecisions,
  spendByMember,
  stepTally,
  tallyText,
} from "./projectModel";
import { SectionCard } from "./projectParts";

function StoppedCard({ snapshot, project }: { snapshot: FactorySnapshot; project: ProjectView }) {
  const fid = snapshot.factory.id;
  const { can } = useViewerCan();
  const toast = useFactoryToast();
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [closing, setClosing] = useState(false);
  const { quick } = useProjectControlParts({
    factoryId: fid,
    project,
    prs: projectPrList(snapshot, project),
    members: snapshot.members,
  });
  const close = async () => {
    setClosing(true);
    try {
      await closeFactoryProject(fid, project.slug, { reason: "closed from its page" });
      void client.invalidateQueries({ queryKey: factoryQueryKey(fid) });
      toast.show({ message: `Closed ${project.slug}. Its pull requests stay open.` });
    } catch (error) {
      toast.show({ message: `Could not close it: ${controlErrorMessage(error)}` });
    } finally {
      setClosing(false);
      setConfirming(false);
    }
  };
  return (
    <section
      aria-labelledby="project-stopped-heading"
      data-testid="project-stopped"
      className={cn("flex flex-col gap-2 rounded-xl border bg-card p-4", MEANING.broken.border)}
    >
      <div className="flex items-center gap-2">
        <TriangleAlertIcon aria-hidden className={cn("size-4", MEANING.broken.text)} />
        <h2 id="project-stopped-heading" className="grow text-ui font-semibold">
          This project stopped
        </h2>
      </div>
      <p className="text-sm text-muted-foreground">
        {project.overlay_reason ?? "Its lead stopped and could not restart."}
      </p>
      {can.apply_projects || can.restart ? (
        <div className="flex flex-wrap items-center gap-1.5">
          {can.apply_projects &&
            (confirming ? (
              <>
                <span className="text-sm">Close {project.slug}? Its pull requests stay open.</span>
                <Button
                  size="sm"
                  onClick={() => void close()}
                  disabled={closing}
                  data-testid="project-close-confirm"
                  componentId="factory.project.close_confirm"
                >
                  Close project
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setConfirming(false)}
                  componentId="factory.project.close_keep"
                >
                  Keep it
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                onClick={() => setConfirming(true)}
                data-testid="project-close"
                componentId="factory.project.close"
              >
                Close project
              </Button>
            ))}
          {quick}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">Ask an operator to close or relaunch it.</p>
      )}
    </section>
  );
}

function LatestUpdate({
  project,
  chip,
  nowS,
}: {
  project: ProjectView;
  chip: HealthChipData | null;
  nowS: number;
}) {
  const update = project.update ?? null;
  const health = project.health ?? null;
  const facts = health?.facts ?? [];
  const age = update ? formatElapsed(nowS - update.at) : null;
  return (
    <SectionCard
      title="Latest update"
      testId="project-latest-update"
      action={chip ? <HealthChip chip={chip} /> : null}
    >
      {update ? (
        <div className="flex min-w-0 flex-col gap-2">
          <p className="text-xs text-muted-foreground">
            {update.by ?? "The project lead"}
            {age ? ` · ${age} ago` : ""} · said {HEALTH_WORD[update.health].toLowerCase()}
          </p>
          {update.done && <p className="text-sm">{update.done}</p>}
          <dl className="grid grid-cols-[6rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
            {update.next && (
              <>
                <dt className="text-muted-foreground">Next</dt>
                <dd>{update.next}</dd>
              </>
            )}
            <dt className="text-muted-foreground">Blocked by</dt>
            <dd>{update.blocker ?? "nothing"}</dd>
          </dl>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground" data-testid="project-update-none">
          No update from the project lead yet.
        </p>
      )}
      {health && (
        <p
          data-testid="project-update-facts"
          className="flex items-start gap-1.5 text-xs text-muted-foreground"
        >
          <CheckIcon aria-hidden className="mt-0.5 size-3 shrink-0" />
          <span>
            {health.stale
              ? "The update is stale, so health comes from the facts"
              : update == null
                ? "Health comes from the facts"
                : health.agrees
                  ? "Agrees with the facts"
                  : `The facts say ${HEALTH_WORD[health.computed].toLowerCase()}`}
            {facts.length ? `: ${facts.join(", ")}.` : "."} A lead's "on track" never overrides the
            facts.
          </span>
        </p>
      )}
    </SectionCard>
  );
}

const DEMO_WORD: Record<DemoStatus, string> = {
  PASS: "Demo passed",
  PARTIAL: "Demo partly passed",
  FAIL: "Demo failed",
  ERROR: "Demo could not run",
};

function Proof({
  factoryId,
  project,
  prs,
}: {
  factoryId: string;
  project: ProjectView;
  prs: PrView[];
}) {
  const withDemo = prs.find((pr) => pr.state === "open" && pr.demo) ?? prs.find((pr) => pr.demo);
  if (!withDemo?.demo) return null;
  const demo = withDemo.demo;
  const tally = stepTally(demo);
  return (
    <SectionCard
      title="Proof"
      testId="project-proof"
      meta={`demo of #${withDemo.number}${withDemo.older_demos ? ` · ${withDemo.older_demos + 1} runs` : ""}`}
    >
      <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
        <DemoThumb prNumber={withDemo.number} demo={demo} />
        <div className="flex min-w-0 flex-col gap-1.5 text-sm">
          <p className="font-semibold">
            {DEMO_WORD[demo.status]}: {tallyText(tally)}
          </p>
          {demo.summary && <p className="text-muted-foreground">{demo.summary}</p>}
          <Button asChild variant="outline" size="sm" className="self-start">
            <Link
              to={`${projectTabHref(factoryId, project.slug, "work")}#demos`}
              componentId="factory.project.watch_demo"
            >
              <PlayIcon aria-hidden />
              Watch demo
            </Link>
          </Button>
        </div>
      </div>
    </SectionCard>
  );
}

function PullRequests({
  factoryId,
  prs,
  nowS,
}: {
  factoryId: string;
  prs: PrView[];
  nowS: number;
}) {
  const open = prs.filter((pr) => pr.state === "open");
  const merged = prs.filter((pr) => pr.state === "merged");
  return (
    <SectionCard
      title="Pull requests"
      testId="project-overview-prs"
      meta={merged.length ? `${merged.length} merged` : undefined}
    >
      {open.length === 0 ? (
        <p className="text-sm text-muted-foreground">No open pull requests.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col divide-y divide-border p-0">
          {open.map((pr) => (
            <li key={pr.id} className="flex min-w-0 flex-col gap-0.5 py-2 first:pt-0 last:pb-0">
              <Link
                to={prPageHref(factoryId, pr.id)}
                componentId="factory.project.overview_pr"
                className={cn("text-sm font-medium hover:underline", FOCUS_RING)}
              >
                {pr.title} <span className="font-mono text-muted-foreground">#{pr.number}</span>
              </Link>
              <p
                className="text-xs text-muted-foreground"
                data-testid={`project-pr-gates-${pr.number}`}
              >
                {pr.ready ? "Ready for merge · " : ""}
                {gateWords(pr).join(" · ") || "gates not reported yet"}
                {pr.opened_at ? ` · opened ${formatElapsed(nowS - pr.opened_at)} ago` : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

function ReadyForMerge({ snapshot, prs }: { snapshot: FactorySnapshot; prs: PrView[] }) {
  // A PR into an owner-set auto-merge target is the server's to merge, not the viewer's.
  const ready = prs.filter((pr) => pr.state === "open" && pr.ready && !pr.auto_merge);
  if (ready.length === 0) return null;
  const policy = snapshot.factory.product?.delivery?.merge_policy ?? null;
  return (
    <SectionCard
      title="Ready for your merge"
      testId="project-ready-merge"
      className={MEANING.needsYou.border}
    >
      <p className="text-sm text-muted-foreground">
        {policy ? `${policy.replace(/\.?$/, ".")} ` : "A person merges. "}
        Agents never merge.
      </p>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {ready.map((pr) => (
          <li key={pr.id} className="flex flex-col gap-1.5">
            <p className="text-sm">
              <span className="font-mono">#{pr.number}</span> · {gateWords(pr).join(" · ")}
            </p>
            <div className="flex flex-wrap items-center gap-1.5">
              {pr.url && (
                <Button asChild size="sm" variant="outline">
                  <a href={pr.url} target="_blank" rel="noreferrer">
                    Open on the code host
                    <ExternalLinkIcon aria-hidden className="size-3.5" />
                  </a>
                </Button>
              )}
              <IMergedItButton
                factoryId={snapshot.factory.id}
                pr={pr}
                testId={`project-merged-${pr.number}`}
              />
            </div>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

function Measured({ project }: { project: ProjectView }) {
  const plan = measurementPlan(project);
  const frozen = project.why?.frozen_at
    ? `frozen ${new Date(project.why.frozen_at * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}${project.why.frozen_by ? ` by ${project.why.frozen_by}` : ""}`
    : undefined;
  return (
    <SectionCard
      title="How it will be measured"
      testId="project-measured"
      meta={plan ? frozen : undefined}
    >
      {plan ? (
        <dl className="grid grid-cols-[6rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
          {plan.map((row) => (
            <div key={row.label} className="contents">
              <dt className="text-muted-foreground">{row.label}</dt>
              <dd className="min-w-0 break-words">{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="text-sm text-muted-foreground">
          It will not be: requested work has no metric. Add one and the server reads it out after
          release like any bet.
        </p>
      )}
    </SectionCard>
  );
}

function Spend({ snapshot, project }: { snapshot: FactorySnapshot; project: ProjectView }) {
  const rows = spendByMember(snapshot, project);
  const budget = projectBudget(project);
  const top = Math.max(1, ...rows.map((r) => r.usd));
  return (
    <SectionCard
      title="Spend"
      testId="project-spend"
      meta={`${money(project.cost_usd ?? 0)}${budget ? ` of ${money(budget)}` : ""}`}
    >
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing spent yet.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
          {rows.map((row) => (
            <li key={row.id} className="flex items-center gap-2 text-sm">
              <span className="w-24 min-w-0 truncate">{row.name}</span>
              <span
                aria-hidden
                className="relative block h-1.5 grow overflow-hidden rounded-full bg-muted"
              >
                <span
                  className="absolute inset-y-0 left-0 rounded-full"
                  style={{ width: `${(row.usd / top) * 100}%`, background: DATA_HUE }}
                />
              </span>
              <span className="w-14 text-right font-mono text-xs tabular-nums">
                {money(row.usd)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

export function ProjectOverviewTab({
  snapshot,
  project,
  chip,
  nowS,
  clockOffsetS,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  chip: HealthChipData | null;
  nowS: number;
  clockOffsetS: number;
}) {
  const fid = snapshot.factory.id;
  const [params] = useSearchParams();
  const prs = projectPrList(snapshot, project);
  const open = projectDecisions(snapshot, project).filter((d) => d.status === "open");
  const finished =
    project.stage === "done" ||
    project.stage === "merged" ||
    project.status === "done" ||
    project.status === "merged";
  return (
    <div
      data-testid="project-overview"
      className="grid min-w-0 grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]"
    >
      <div className="flex min-w-0 flex-col gap-4">
        {project.overlay === "failed" && <StoppedCard snapshot={snapshot} project={project} />}
        <LatestUpdate project={project} chip={chip} nowS={nowS} />
        <Proof factoryId={fid} project={project} prs={prs} />
        <PullRequests factoryId={fid} prs={prs} nowS={nowS} />
        {finished && (
          <SectionCard title="Wrap-up" testId="project-wrap-up">
            <WrapUpRating factoryId={fid} project={project.slug} />
          </SectionCard>
        )}
      </div>
      <div className="flex min-w-0 flex-col gap-4">
        {open.length > 0 && (
          <SectionCard
            id="decisions"
            title="Waiting on you"
            testId="project-waiting"
            style={{ scrollMarginTop: SECTION_SCROLL_MARGIN }}
            className={MEANING.needsYou.border}
            meta="the same asks as on Home; answer them in either place"
          >
            <DecisionsRail
              factoryId={fid}
              decisions={open}
              highlightId={params.get("decision")}
              clockOffsetS={clockOffsetS}
              hideHeading
            />
          </SectionCard>
        )}
        <ReadyForMerge snapshot={snapshot} prs={prs} />
        <Measured project={project} />
        <Spend snapshot={snapshot} project={project} />
        <div className="flex justify-end">
          <ReportProblemButton
            factoryId={fid}
            target={`project:${project.slug}`}
            targetLabel={project.title}
            project={project.slug}
            testId="project-report-problem"
          />
        </div>
      </div>
    </div>
  );
}
