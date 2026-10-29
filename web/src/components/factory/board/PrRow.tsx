/**
 * One PR on a project card: title line, the gates GitHub reports, Sentinel and
 * human-review badges, the factory preview and the newest demo, and the two
 * ways forward (review it here, merge it on GitHub). Merged and closed PRs
 * collapse to one line.
 */

import {
  CheckIcon,
  CloudOffIcon,
  ExternalLinkIcon,
  GitMergeIcon,
  GitPullRequestClosedIcon,
  GitPullRequestIcon,
  PlayIcon,
  ShieldCheckIcon,
  ShieldXIcon,
  TriangleAlertIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import type { PrView, ProjectView } from "@/lib/factoryApi";
import { beginGithubConnect } from "@/lib/githubIntegration";
import { cn } from "@/lib/utils";
import {
  type FactoryTone,
  TONE_CHIP,
  changesRequestedOnHead,
  plural,
  readyForHuman,
  sameSha,
  shortSha,
} from "../factoryFormat";
import { useChangedSinceMount } from "./boardHooks";
import { ageText, clipDuration, prAnchorId, prHasNoEnv, prTouchesUi } from "./boardModel";
import { DemoStatusTag, DemoThumb } from "./DemoThumb";
import { GateChip, shownGates } from "./GateChip";
import { PreviewPill } from "./PreviewPill";

/** Where GitHub's merge box is; humans merge, the factory never does. */
export function mergeUrl(pr: PrView): string {
  return `${pr.url}#partial-pull-merging`;
}

function Badge({
  tone,
  icon,
  title,
  testId,
  children,
}: {
  tone: FactoryTone;
  icon?: ReactNode;
  title?: string;
  testId?: string;
  children: ReactNode;
}) {
  return (
    <span
      data-testid={testId}
      title={title}
      className={cn(
        "inline-flex h-5 items-center gap-1 rounded-md px-1.5 text-sm whitespace-nowrap",
        TONE_CHIP[tone],
      )}
    >
      {icon}
      {children}
    </span>
  );
}

function prBadges(pr: PrView): ReactNode[] {
  const badges: ReactNode[] = [];
  const s = pr.sentinel;
  if (s) {
    const current = sameSha(s.head_sha, pr.head_sha);
    badges.push(
      s.verdict === "verified" ? (
        <Badge
          key="sentinel"
          testId={`sentinel-badge-${pr.number}`}
          tone={current ? "pass" : "muted"}
          icon={<ShieldCheckIcon aria-hidden className="size-3" />}
          title={[s.by, s.note].filter(Boolean).join(": ") || undefined}
        >
          Sentinel ✓{!current && s.head_sha ? ` @${shortSha(s.head_sha)}` : ""}
        </Badge>
      ) : (
        <Badge
          key="sentinel"
          testId={`sentinel-badge-${pr.number}`}
          tone="fail"
          icon={<ShieldXIcon aria-hidden className="size-3" />}
          title={s.note ?? undefined}
        >
          Sentinel ✖
        </Badge>
      ),
    );
  }
  const review = pr.human_review;
  if (review?.verdict === "changes_requested") {
    const held = changesRequestedOnHead(pr);
    badges.push(
      <Badge
        key="human"
        testId={`changes-requested-${pr.number}`}
        tone={held ? "warning" : "muted"}
        title={review.text ?? undefined}
      >
        Changes requested by {review.by}
        {!held && " (old head)"}
      </Badge>,
    );
  } else if (review?.verdict === "approved") {
    badges.push(
      <Badge key="human" tone="pass" icon={<CheckIcon aria-hidden className="size-3" />}>
        Approved by {review.by}
      </Badge>,
    );
  }
  if (pr.demo && !pr.demo.is_current_head) {
    badges.push(
      <Badge key="demo" testId={`demo-badge-${pr.number}`} tone="warning">
        Demo (old head {shortSha(pr.demo.head_sha)})
      </Badge>,
    );
  } else if (!pr.demo && pr.gates.demo == null && pr.state === "open" && prTouchesUi(pr)) {
    badges.push(
      <Badge key="demo" testId={`demo-badge-${pr.number}`} tone="warning">
        Demo missing
      </Badge>,
    );
  }
  return badges;
}

function DemoSummary({
  pr,
  project,
  onReview,
}: {
  pr: PrView;
  project: ProjectView | null;
  onReview: () => void;
}) {
  const demo = pr.demo;
  if (!demo) {
    return (
      <p data-testid={`demo-none-${pr.number}`} className="text-sm text-muted-foreground">
        Demo{" "}
        {prHasNoEnv(pr, project)
          ? "· not needed for this PR"
          : "— none yet · recorded after the preview passes verify"}
      </p>
    );
  }
  const skipped = demo.steps.filter((s) => s.status === "skipped").length;
  const failed = demo.steps.filter((s) => s.status === "fail").length;
  const length = clipDuration(demo.duration_s);
  const meta = [
    { key: "steps", text: plural(demo.steps.length, "step") },
    failed > 0 && { key: "failed", text: `${failed} failed`, className: "text-destructive" },
    skipped > 0 && { key: "skipped", text: `${skipped} skipped` },
    length && { key: "length", text: length, className: "font-mono tabular-nums" },
    { key: "sha", text: shortSha(demo.head_sha), className: "font-mono tabular-nums" },
  ].filter((item): item is { key: string; text: string; className?: string } => Boolean(item));
  return (
    <div className="flex min-w-0 flex-col gap-2 @md:flex-row @md:items-start @md:gap-3">
      <DemoThumb
        prNumber={pr.number}
        demo={demo}
        onOpen={onReview}
        className="@max-md:w-full @max-md:max-w-sm"
      />
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="flex flex-wrap items-center gap-x-1.5 text-sm text-muted-foreground">
          <span className="text-foreground">Demo</span>
          <DemoStatusTag status={demo.status} />
          {meta.map((item, i) => (
            // The separator trails its item, so a wrapped line never starts with one.
            <span key={item.key} className={cn("whitespace-nowrap", item.className)}>
              {item.text}
              {i < meta.length - 1 && (
                <span className="ml-1.5 font-sans text-muted-foreground">·</span>
              )}
            </span>
          ))}
        </p>
        <p className="line-clamp-2 text-sm text-foreground/80">{demo.summary}</p>
        {pr.older_demos > 0 && (
          <p className="text-sm text-muted-foreground">{plural(pr.older_demos, "older demo")}</p>
        )}
      </div>
    </div>
  );
}

function PollError({ pr, nowS }: { pr: PrView; nowS: number }) {
  if (!pr.poll_error) return null;
  const message = pr.poll_error.replace(/\s*·\s*Connect$/, "");
  const needsConnect = /token|auth|connect|401|disconnect/i.test(pr.poll_error);
  return (
    <p
      data-testid={`pr-poll-error-${pr.number}`}
      className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground"
    >
      <CloudOffIcon aria-hidden className="size-3.5" />
      <span>{message}</span>
      {pr.last_polled_at != null && <span>· last checked {ageText(nowS - pr.last_polled_at)}</span>}
      {needsConnect && (
        <Button
          variant="outline"
          size="xs"
          componentId="factory.board.pr_connect_github"
          onClick={() => beginGithubConnect(window.location.pathname + window.location.search)}
        >
          Connect GitHub
        </Button>
      )}
    </p>
  );
}

/** A merged or closed PR: one quiet line. */
function ClosedPrRow({ pr, nowS, pulse }: { pr: PrView; nowS: number; pulse: boolean }) {
  const merged = pr.state === "merged";
  return (
    <div
      id={prAnchorId(pr.number)}
      data-testid={`pr-row-${pr.number}`}
      data-state={pr.state}
      className="flex min-w-0 items-center gap-2 rounded-lg px-1 py-0.5 text-sm"
    >
      <span className="relative flex size-4 shrink-0 items-center justify-center">
        {merged && pulse && (
          <span
            aria-hidden
            className="absolute inset-0 animate-[user-msg-flash_800ms_ease-out] rounded-full motion-reduce:hidden"
          />
        )}
        {merged ? (
          <GitMergeIcon aria-hidden className="size-3.5 text-brand-accent" />
        ) : (
          <GitPullRequestClosedIcon aria-hidden className="size-3.5 text-muted-foreground" />
        )}
      </span>
      <a
        href={pr.url}
        target="_blank"
        rel="noreferrer"
        className="shrink-0 font-mono text-foreground tabular-nums hover:underline"
      >
        #{pr.number}
      </a>
      <span className="min-w-0 truncate text-muted-foreground" title={pr.title}>
        {pr.title}
      </span>
      <span className="ml-auto shrink-0 text-muted-foreground">
        {merged
          ? `merged${pr.merged?.by ? ` by @${pr.merged.by}` : ""}${pr.merged ? ` · ${ageText(nowS - pr.merged.at)}` : ""}`
          : "closed without merge"}
      </span>
    </div>
  );
}

export function PrRow({
  pr,
  project,
  nowS,
  onReview,
  isNew = false,
  readyBanner,
}: {
  pr: PrView;
  project: ProjectView | null;
  nowS: number;
  /** Open the review drawer for this PR (`?review=<number>`). */
  onReview: (number: number) => void;
  /** First seen after the board loaded: slides in. */
  isNew?: boolean;
  /** Shown instead of the demo, preview and actions while the PR is ready. */
  readyBanner?: ReactNode;
}) {
  // Tracked here: the row swaps layouts when the PR merges, and only that swap pulses.
  const changed = useChangedSinceMount(pr.state);
  if (pr.state !== "open") return <ClosedPrRow pr={pr} nowS={nowS} pulse={changed !== null} />;
  const review = () => onReview(pr.number);
  const badges = prBadges(pr);
  const ready = readyForHuman(pr);
  // Held by a human: the gates may all pass, but only a new head can be ready.
  const reasons = changesRequestedOnHead(pr)
    ? ["waits on a new head", ...pr.gates.reasons]
    : pr.gates.reasons;
  return (
    <div
      id={prAnchorId(pr.number)}
      data-testid={`pr-row-${pr.number}`}
      data-state={pr.state}
      data-ready={ready ? "true" : "false"}
      className={cn(
        "@container flex min-w-0 flex-col gap-2.5 rounded-lg border border-border bg-background p-3 dark:bg-background/40",
        isNew && "animate-in duration-300 fade-in-0 slide-in-from-top-1 motion-reduce:animate-none",
      )}
    >
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <GitPullRequestIcon
          aria-hidden
          className={cn(
            "size-3.5 shrink-0 self-center",
            ready ? "text-success" : "text-muted-foreground",
          )}
        />
        <a
          href={pr.url}
          target="_blank"
          rel="noreferrer"
          className="shrink-0 font-mono text-ui font-medium tabular-nums hover:underline"
        >
          #{pr.number}
        </a>
        {pr.is_draft && <span className="shrink-0 text-sm text-muted-foreground">draft</span>}
        <span className="min-w-0 flex-1 basis-48 truncate text-ui" title={pr.title}>
          {pr.title}
        </span>
        <span className="shrink-0 text-sm whitespace-nowrap text-muted-foreground">
          <span className="font-mono tabular-nums">{shortSha(pr.head_sha)}</span>
          <span className="mx-1">·</span>
          <span className="font-mono text-success tabular-nums">+{pr.additions}</span>{" "}
          <span className="font-mono text-destructive tabular-nums">−{pr.deletions}</span>
          <span className="mx-1">·</span>
          {plural(pr.changed_files, "file")}
        </span>
      </div>

      <div
        data-testid={`pr-gates-${pr.number}`}
        className="grid grid-cols-3 gap-1 @md:flex @md:flex-wrap @md:items-center"
      >
        {shownGates(pr).map((gate) => (
          <GateChip key={gate} pr={pr} gate={gate} nowS={nowS} className="@max-md:justify-center" />
        ))}
        {badges.length > 0 && (
          <span className="col-span-3 flex flex-wrap items-center gap-1 @md:contents">
            {badges}
          </span>
        )}
      </div>

      <PollError pr={pr} nowS={nowS} />

      {ready && readyBanner ? (
        readyBanner
      ) : (
        <>
          <div className="flex min-w-0 flex-col gap-2 @2xl:flex-row @2xl:items-start @2xl:justify-between @2xl:gap-4">
            <DemoSummary pr={pr} project={project} onReview={review} />
            <div className="shrink-0 @2xl:pt-0.5">
              <PreviewPill pr={pr} project={project} nowS={nowS} />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant={ready ? "default" : "outline"}
              onClick={review}
              data-testid={`review-open-${pr.number}`}
              componentId="factory.board.review_open"
              className="@max-md:flex-1"
            >
              <PlayIcon className="size-3.5" />
              Review
            </Button>
            {ready ? (
              <Button
                asChild
                size="sm"
                variant="outline"
                className="@max-md:flex-1"
                title="Humans merge. The factory never merges."
              >
                <a
                  href={mergeUrl(pr)}
                  target="_blank"
                  rel="noreferrer"
                  data-testid={`merge-link-${pr.number}`}
                >
                  Merge on GitHub
                  <ExternalLinkIcon className="size-3.5" />
                </a>
              </Button>
            ) : (
              <Button
                asChild
                size="sm"
                variant="ghost"
                className="text-muted-foreground @max-md:flex-1"
              >
                <a
                  href={pr.url}
                  target="_blank"
                  rel="noreferrer"
                  data-testid={`github-link-${pr.number}`}
                >
                  GitHub
                  <ExternalLinkIcon className="size-3.5" />
                </a>
              </Button>
            )}
            {ready && pr.ready_since != null && (
              <span className="ml-auto text-sm text-success">
                ready {ageText(nowS - pr.ready_since).replace(" ago", "")}
              </span>
            )}
            {!ready && reasons.length > 0 && (
              <span
                data-testid={`pr-not-ready-${pr.number}`}
                className="ml-auto inline-flex min-w-0 items-center gap-1 text-sm text-muted-foreground"
              >
                <TriangleAlertIcon aria-hidden className="size-3 shrink-0" />
                <span className="truncate" title={reasons.join(" · ")}>
                  {reasons[0]}
                  {reasons.length > 1 && ` · ${reasons.length - 1} more`}
                </span>
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
