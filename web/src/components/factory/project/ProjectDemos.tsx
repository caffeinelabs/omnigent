/**
 * The project page's Demos tab: one card per PR with its newest demo. The
 * poster plays inline (`<video>` over the same-origin demo file route), then
 * the status, which commit and when it was recorded, the step tally, the
 * steps with skip reasons and failure notes, console errors, API calls and
 * the screenshots (click for the lightbox). Earlier runs of the PR fold
 * under the card and can be shown in its place.
 */

import { useState } from "react";
import {
  BracesIcon,
  ChevronRightIcon,
  ClapperboardIcon,
  ExternalLinkIcon,
  HistoryIcon,
  ImageIcon,
  PlayIcon,
} from "lucide-react";
import { ZoomableImage } from "@/components/ImageLightbox";
import { RunningDot } from "@/components/RunningDot";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { useFactoryDemos } from "@/hooks/useFactory";
import type { DemoView, MemberView, PrView, ProjectDemoView, ProjectView } from "@/lib/factoryApi";
import { prPreviewUrl } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { ageText, clipDuration, prNoEnvReason } from "../board/boardModel";
import { DemoStatusTag, demoApiCallCount, demoPoster } from "../board/DemoThumb";
import { FactoryLoadError } from "../FactoryLoadError";
import { plural, sameSha, shortSha } from "../factoryFormat";
import { DemoStepList } from "../review/DemoPanel";
import { demoFileHref, demoRecorder, demoScreenshots, demoVideo } from "../review/reviewModel";
import { DotParts, ReviewNotice } from "../review/reviewParts";
import {
  type DemoGroup,
  demosSummary,
  githubPrUrl,
  groupDemosByPr,
  stepTally,
  tallyText,
  undemoedPrs,
} from "./projectModel";
import { EmptyState } from "./projectParts";

/** Demos the tab asks for: every run of the project's PRs, up to the server's cap. */
export const PROJECT_DEMOS_LIMIT = 200;

// ── Media ──────────────────────────────────────────────────────

function DemoMedia({ demo, prNumber }: { demo: DemoView; prNumber: number }) {
  const [playing, setPlaying] = useState(false);
  const [broken, setBroken] = useState(false);
  const video = demoVideo(demo);
  const poster = demoPoster(demo);
  const posterHref = poster && !broken ? demoFileHref(demo, poster) : undefined;
  const length = clipDuration(demo.duration_s);
  if (video && playing) {
    return (
      // A demo has no audio or speech; its captions are burnt into the frames.
      <video
        data-testid={`demo-video-${prNumber}`}
        controls
        autoPlay
        playsInline
        preload="metadata"
        src={demoFileHref(demo, video)}
        poster={posterHref}
        aria-label={video.caption ?? `Recording of the ${demo.status} demo of #${prNumber}`}
        className="aspect-video w-full bg-black object-contain"
      />
    );
  }
  if (video) {
    const label = `Play the ${demo.status} demo of #${prNumber}${length ? ` (${length})` : ""}`;
    return (
      <button
        type="button"
        onClick={() => setPlaying(true)}
        data-testid={`demo-play-${prNumber}`}
        aria-label={label}
        title={label}
        className="group/play relative block aspect-video w-full cursor-pointer overflow-hidden bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none focus-visible:ring-inset"
      >
        {posterHref ? (
          <img
            src={posterHref}
            alt=""
            loading="lazy"
            onError={() => setBroken(true)}
            className="absolute inset-0 size-full object-cover object-top"
          />
        ) : (
          <span className="absolute inset-0 flex items-center justify-center text-muted-foreground/60">
            <ImageIcon aria-hidden className="size-6" />
          </span>
        )}
        <span className="absolute inset-0 flex items-center justify-center bg-black/0 transition-colors group-hover/play:bg-black/10 motion-reduce:transition-none">
          <span className="flex size-12 items-center justify-center rounded-full bg-black/60 text-white shadow-md backdrop-blur-[2px] transition-transform group-hover/play:scale-105 motion-reduce:transition-none">
            <PlayIcon aria-hidden className="ml-0.5 size-5 fill-current" />
          </span>
        </span>
        {length && (
          <span className="absolute right-2 bottom-2 rounded-sm bg-black/65 px-1.5 font-mono text-xs leading-5 text-white tabular-nums">
            {length}
          </span>
        )}
      </button>
    );
  }
  if (posterHref) {
    return (
      <div className="overflow-hidden bg-muted">
        <ZoomableImage
          src={posterHref}
          alt={poster?.caption ?? `Still of the demo of #${prNumber}`}
          onError={() => setBroken(true)}
          className="aspect-video w-full object-cover object-top"
        />
      </div>
    );
  }
  const calls = demoApiCallCount(demo);
  return (
    <div
      data-testid={`demo-no-media-${prNumber}`}
      className="flex aspect-video w-full flex-col items-center justify-center gap-1 bg-muted text-muted-foreground"
    >
      {calls != null ? (
        <>
          <BracesIcon aria-hidden className="size-6" />
          <span className="font-mono text-sm">
            API demo{calls ? ` · ${plural(calls, "call")}` : ""}
          </span>
        </>
      ) : (
        <>
          <ImageIcon aria-hidden className="size-6 opacity-60" />
          <span className="text-sm">No recording</span>
        </>
      )}
    </div>
  );
}

function ScreenshotStrip({ demo, prNumber }: { demo: DemoView; prNumber: number }) {
  const shots = demoScreenshots(demo);
  if (shots.length === 0) return null;
  return (
    <ul
      data-testid={`demo-shots-${prNumber}`}
      aria-label="Screenshots"
      className="-mx-1 flex gap-2 overflow-x-auto px-1 pt-0.5 pb-1"
    >
      {shots.map((shot) => (
        <li
          key={shot.id}
          className="relative w-28 shrink-0 overflow-hidden rounded-md border border-border bg-muted"
          title={shot.caption ?? shot.name}
        >
          <ZoomableImage
            src={demoFileHref(demo, shot)}
            alt={shot.caption ?? shot.name}
            loading="lazy"
            className="aspect-video w-28 object-cover object-top transition-opacity hover:opacity-90"
          />
          {shot.step != null && (
            <span className="pointer-events-none absolute top-1 left-1 rounded-sm bg-background/95 px-1 font-mono text-[10px] leading-4 text-muted-foreground shadow-xs ring-1 ring-black/10">
              {shot.step}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

// ── One PR's card ──────────────────────────────────────────────

function Tally({ demo, prNumber }: { demo: DemoView; prNumber: number }) {
  const tally = stepTally(demo);
  const calls = demoApiCallCount(demo);
  return (
    <p
      data-testid={`demo-tally-${prNumber}`}
      className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-sm text-muted-foreground"
    >
      <DotParts
        parts={[
          tally.passed > 0 && {
            key: "passed",
            node: `${tally.passed} passed`,
            className: "text-foreground/80",
          },
          tally.failed > 0 && {
            key: "failed",
            node: `${tally.failed} failed`,
            className: "text-destructive",
          },
          tally.skipped > 0 && { key: "skipped", node: `${tally.skipped} skipped` },
          demo.steps.length === 0 && { key: "steps", node: tallyText(tally) },
          demo.console_errors != null && {
            key: "console",
            node: plural(demo.console_errors, "console error"),
            className: demo.console_errors > 0 ? "text-warning" : undefined,
          },
          calls != null && { key: "api", node: plural(calls, "API call") },
        ]}
      />
    </p>
  );
}

function Steps({ demo, prNumber }: { demo: DemoView; prNumber: number }) {
  if (demo.steps.length === 0) return null;
  const failed = stepTally(demo).failed > 0;
  return (
    <Collapsible key={demo.id} defaultOpen={failed}>
      <CollapsibleTrigger
        data-testid={`demo-steps-toggle-${prNumber}`}
        className="group inline-flex cursor-pointer items-center gap-1 rounded-sm text-sm text-muted-foreground hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <ChevronRightIcon
          aria-hidden
          className="size-3.5 transition-transform group-data-[state=open]:rotate-90 motion-reduce:transition-none"
        />
        Steps ({demo.steps.length})
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-2">
        <DemoStepList demo={demo} testIdPrefix={`demo-${prNumber}`} />
      </CollapsibleContent>
    </Collapsible>
  );
}

function EarlierRunList({
  runs,
  selectedId,
  onSelect,
  nowS,
}: {
  runs: ProjectDemoView[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  nowS: number;
}) {
  return (
    <ul className="flex flex-col gap-0.5">
      {runs.map((run) => {
        const selected = run.id === selectedId;
        return (
          <li key={run.id}>
            <button
              type="button"
              onClick={() => onSelect(selected ? null : run.id)}
              aria-pressed={selected}
              data-testid={`demo-run-${run.id}`}
              className={cn(
                "grid w-full cursor-pointer grid-cols-[auto_auto_minmax(0,1fr)_auto] items-center gap-2 rounded-md px-1.5 py-1 text-left text-sm hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
                selected && "bg-muted",
              )}
            >
              <DemoStatusTag status={run.status} />
              <span className="font-mono tabular-nums">{shortSha(run.head_sha)}</span>
              <span className="truncate text-muted-foreground">{tallyText(stepTally(run))}</span>
              <span className="text-muted-foreground tabular-nums">
                {ageText(nowS - run.created_at)}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** The PR's live factory preview, when one is up to open. */
function previewHref(pr: PrView | null): string | null {
  const preview = pr?.preview;
  if (!pr || pr.state !== "open" || !preview) return null;
  if (preview.state !== "ready" && preview.state !== "stale") return null;
  return preview.stable_url ?? prPreviewUrl(pr.id);
}

function DemoCard({
  group,
  members,
  nowS,
  onReview,
}: {
  group: DemoGroup;
  members: readonly MemberView[];
  nowS: number;
  onReview: (number: number) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [runsOpen, setRunsOpen] = useState(false);
  const { pr, prNumber } = group;
  const run = group.earlier.find((r) => r.id === selectedId) ?? group.latest;
  const earlierShown = run.id !== group.latest.id;
  const open = pr?.state === "open";
  const atHead = pr ? sameSha(run.head_sha, pr.head_sha) : null;
  const recorder = pr && open ? demoRecorder(pr, members) : null;
  const preview = previewHref(pr);
  return (
    <article
      data-testid={`demo-card-${prNumber}`}
      data-status={run.status}
      data-run={earlierShown ? "earlier" : "latest"}
      aria-label={`Demo of PR #${prNumber}`}
      className="flex min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-card"
    >
      <DemoMedia key={run.id} demo={run} prNumber={prNumber} />
      <div className="flex min-w-0 flex-1 flex-col gap-2.5 p-4 max-md:p-3">
        <p
          data-testid={`demo-meta-${prNumber}`}
          className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-sm text-muted-foreground"
        >
          <DemoStatusTag status={run.status} />
          <span>
            PR{" "}
            <a
              href={pr?.url ?? githubPrUrl(group.repo, prNumber)}
              target="_blank"
              rel="noreferrer"
              className="font-mono text-foreground tabular-nums hover:underline"
            >
              #{prNumber}
            </a>
          </span>
          <span aria-hidden>·</span>
          <span className="font-mono tabular-nums">
            {shortSha(run.head_sha)}
            {open && atHead === true && <span className="font-sans text-success"> = head</span>}
            {open && atHead === false && <span className="font-sans text-warning"> ≠ head</span>}
          </span>
          <span aria-hidden>·</span>
          <span>recorded {ageText(nowS - run.created_at)}</span>
          {pr && !open && (
            <span className="rounded-sm bg-muted px-1 text-[11px] leading-4">{pr.state}</span>
          )}
        </p>
        <h3 className="text-ui leading-snug font-medium">{pr?.title ?? run.title}</h3>
        {run.summary && (
          <p className="line-clamp-3 text-sm text-foreground/80" title={run.summary}>
            {run.summary}
          </p>
        )}
        {recorder && !earlierShown && (
          <p
            data-testid={`demo-recording-${prNumber}`}
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground"
          >
            <RunningDot className="size-3" />
            {recorder.title} is recording{" "}
            {atHead ? "again" : <span className="font-mono">{shortSha(pr?.head_sha)}</span>}
          </p>
        )}
        {earlierShown && (
          <ReviewNotice tone="muted" testId={`demo-earlier-notice-${prNumber}`}>
            An earlier run, recorded {ageText(nowS - run.created_at)} at{" "}
            <span className="font-mono">{shortSha(run.head_sha)}</span>.{" "}
            <button
              type="button"
              onClick={() => setSelectedId(null)}
              className="cursor-pointer text-foreground underline underline-offset-2"
            >
              Show the latest
            </button>
          </ReviewNotice>
        )}
        <Tally demo={run} prNumber={prNumber} />
        <Steps demo={run} prNumber={prNumber} />
        <ScreenshotStrip demo={run} prNumber={prNumber} />
        <Collapsible open={runsOpen} onOpenChange={setRunsOpen} className="mt-auto">
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
            {pr ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => onReview(pr.number)}
                data-testid={`demo-review-${prNumber}`}
                componentId="factory.project.demo_review"
              >
                Open review
              </Button>
            ) : (
              <Button asChild size="sm" variant="outline">
                <a href={githubPrUrl(group.repo, prNumber)} target="_blank" rel="noreferrer">
                  GitHub
                  <ExternalLinkIcon />
                </a>
              </Button>
            )}
            {preview && (
              <Button asChild size="sm" variant="ghost">
                <a
                  href={preview}
                  target="_blank"
                  rel="noreferrer"
                  data-testid={`demo-preview-${prNumber}`}
                >
                  Open preview
                  <ExternalLinkIcon />
                </a>
              </Button>
            )}
            {group.earlier.length > 0 && (
              <CollapsibleTrigger
                data-testid={`demo-earlier-${prNumber}`}
                className="group ml-auto inline-flex cursor-pointer items-center gap-1 rounded-sm text-sm text-muted-foreground hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                <HistoryIcon aria-hidden className="size-3.5" />
                {plural(group.earlier.length, "earlier run")}
                <ChevronRightIcon
                  aria-hidden
                  className="size-3.5 transition-transform group-data-[state=open]:rotate-90 motion-reduce:transition-none"
                />
              </CollapsibleTrigger>
            )}
          </div>
          <CollapsibleContent className="pt-2">
            <EarlierRunList
              runs={group.earlier}
              selectedId={selectedId}
              onSelect={setSelectedId}
              nowS={nowS}
            />
          </CollapsibleContent>
        </Collapsible>
      </div>
    </article>
  );
}

// ── Tab ────────────────────────────────────────────────────────

function GallerySkeleton() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading demos"
      data-testid="project-demos-loading"
      className="grid grid-cols-1 gap-4 md:grid-cols-2"
    >
      {[0, 1].map((i) => (
        <div key={i} className="overflow-hidden rounded-xl border border-border bg-card">
          <div className="aspect-video w-full animate-pulse bg-muted" />
          <div className="flex flex-col gap-2 p-4">
            <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
            <div className="h-4 w-4/5 animate-pulse rounded bg-muted" />
            <div className="h-3 w-2/3 animate-pulse rounded bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Open PRs without a demo, and why. */
function Undemoed({
  prs,
  project,
  members,
}: {
  prs: PrView[];
  project: ProjectView;
  members: readonly MemberView[];
}) {
  if (prs.length === 0) return null;
  return (
    <div
      data-testid="project-demos-pending"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground"
    >
      <span>Not demoed yet:</span>
      {prs.map((pr) => {
        const recorder = demoRecorder(pr, members);
        const noEnv = prNoEnvReason(pr, project);
        return (
          <span key={pr.id} className="inline-flex items-center gap-1.5">
            <span className="font-mono text-foreground tabular-nums">#{pr.number}</span>
            {recorder ? (
              <>
                <RunningDot className="size-3" />
                recording now
              </>
            ) : noEnv ? (
              "needs no demo"
            ) : (
              "after its preview passes verify"
            )}
          </span>
        );
      })}
    </div>
  );
}

export function ProjectDemos({
  factoryId,
  project,
  prs,
  members,
  nowS,
  onReview,
}: {
  factoryId: string;
  project: ProjectView;
  /** The project's PRs (the snapshot's view: titles, heads, previews). */
  prs: PrView[];
  members: readonly MemberView[];
  nowS: number;
  /** Open the review drawer for a PR. */
  onReview: (number: number) => void;
}) {
  const query = useFactoryDemos(factoryId, { project: project.slug, limit: PROJECT_DEMOS_LIMIT });
  const groups = query.data ? groupDemosByPr(query.data.data, prs) : [];
  const pending = query.data ? undemoedPrs(prs, groups) : [];
  const previewsOn = project.charter?.preview.enabled === true;
  return (
    <div data-testid="project-demos" className="flex min-w-0 flex-col gap-4">
      {query.error && (
        <FactoryLoadError
          error={query.error}
          updatedAt={query.data ? query.dataUpdatedAt : 0}
          onRetry={() => void query.refetch()}
          what="the demos"
          testId="project-demos-error"
        />
      )}
      {query.isLoading && <GallerySkeleton />}
      {query.data && groups.length === 0 && (
        <EmptyState
          testId="project-demos-empty"
          icon={<ClapperboardIcon aria-hidden className="size-6 text-muted-foreground/50" />}
          title="No demos yet"
          body={
            previewsOn
              ? "The demo agent records each PR once its factory preview passes verify: the recording, every step and its screenshots land here."
              : "Previews and demos are off for this project. Turn previews on in the charter to record a demo of every PR."
          }
        />
      )}
      {groups.length > 0 && (
        <>
          <p className="text-sm text-muted-foreground" data-testid="project-demos-summary">
            {demosSummary(groups)} · newest first
          </p>
          <div className="grid grid-cols-1 items-start gap-4 md:grid-cols-2">
            {groups.map((group) => (
              <DemoCard
                key={group.key}
                group={group}
                members={members}
                nowS={nowS}
                onReview={onReview}
              />
            ))}
          </div>
        </>
      )}
      <Undemoed prs={pending} project={project} members={members} />
    </div>
  );
}
