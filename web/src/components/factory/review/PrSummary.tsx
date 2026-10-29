/**
 * The top of the PR page: which PR (branch, head, base, diff size, task),
 * whether it is ready for a human merge or why not, and why the PR's stable
 * preview link landed here when the preview isn't running.
 */

import {
  CheckIcon,
  GitMergeIcon,
  GitPullRequestClosedIcon,
  MessageSquareWarningIcon,
  TriangleAlertIcon,
} from "lucide-react";
import type { PrView } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { changesRequestedOnHead, plural, readyForHuman, sameSha, shortSha } from "../factoryFormat";
import { ReadyForMergeBanner } from "./ReadyForMergeBanner";
import { DotParts, ReviewNotice } from "./reviewParts";

/** `factory/db-version/route · 5e1c2d4 · base temp-main ✓ · +88 −3 · 4 files · T-4`. */
export function PrMeta({ pr, expectedBase }: { pr: PrView; expectedBase?: string | null }) {
  const baseOk = pr.base_ref != null && expectedBase != null ? pr.base_ref === expectedBase : null;
  return (
    <p
      data-testid="review-pr-meta"
      className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-sm text-muted-foreground"
    >
      <DotParts
        parts={[
          {
            key: "ref",
            node: (
              <span className="min-w-0 truncate font-mono" title={pr.head_ref}>
                {pr.head_ref}
              </span>
            ),
          },
          {
            key: "sha",
            node: <span className="font-mono tabular-nums">@{shortSha(pr.head_sha)}</span>,
          },
          pr.base_ref
            ? {
                key: "base",
                node: (
                  <span data-testid="review-pr-base" className="inline-flex items-center gap-1">
                    base <span className="font-mono text-foreground">{pr.base_ref}</span>
                    {baseOk === true && (
                      <CheckIcon aria-label="expected base" className="size-3 text-success" />
                    )}
                    {baseOk === false && <span className="text-warning">≠ {expectedBase}</span>}
                  </span>
                ),
              }
            : null,
          {
            key: "diff",
            node: (
              <span className="font-mono whitespace-pre tabular-nums">
                <span className={cn(pr.additions > 0 && "text-success")}>+{pr.additions}</span>{" "}
                <span className={cn(pr.deletions > 0 && "text-destructive")}>−{pr.deletions}</span>
              </span>
            ),
          },
          { key: "files", node: <span>{plural(pr.changed_files, "file")}</span> },
          pr.task_ref || pr.task
            ? { key: "task", node: <span className="font-mono">{pr.task_ref ?? pr.task}</span> }
            : null,
        ]}
      />
    </p>
  );
}

/** Ready for a human merge, merged, closed, or not ready yet and why. */
export function StatusLine({ pr, nowS }: { pr: PrView; nowS: number }) {
  if (pr.state === "merged" || pr.state === "closed") {
    const merged = pr.state === "merged";
    const Icon = merged ? GitMergeIcon : GitPullRequestClosedIcon;
    return (
      <p
        data-testid="review-state-chip"
        data-state={pr.state}
        className={cn(
          "inline-flex items-center gap-1 self-start rounded-md px-2 py-0.5 text-sm",
          merged ? "bg-brand-accent/10 text-brand-accent" : "bg-muted text-muted-foreground",
        )}
      >
        <Icon aria-hidden className="size-3.5" />
        {merged ? "Merged" : "Closed without merge"}
        {merged && pr.merged?.sha && (
          <span className="font-mono">
            {" "}
            · {sameSha(pr.merged.sha, pr.head_sha) ? "at head" : shortSha(pr.merged.sha)}
          </span>
        )}
      </p>
    );
  }
  if (readyForHuman(pr)) {
    return <ReadyForMergeBanner pr={pr} nowS={nowS} testId="review-ready-banner" />;
  }
  const review = pr.human_review;
  const held = changesRequestedOnHead(pr);
  // Held by a human: every gate may pass, but only a new head can be ready again.
  const reasons = held ? ["waits on a new head", ...pr.gates.reasons] : pr.gates.reasons;
  return (
    <div className="flex flex-col gap-1">
      {review?.verdict === "changes_requested" && (
        <p
          data-testid="review-changes-requested"
          data-head={held ? "current" : "old"}
          className="inline-flex max-w-full min-w-0 items-start gap-1 self-start rounded-md bg-warning/15 px-2 py-0.5 text-sm text-warning"
        >
          <MessageSquareWarningIcon aria-hidden className="mt-[0.2em] size-3.5 shrink-0" />
          <span className="min-w-0 break-words">
            Changes requested by {review.by}
            {review.head_sha && " on "}
            {review.head_sha && (
              <span className="font-mono tabular-nums">{shortSha(review.head_sha)}</span>
            )}
            {held ? " · the PL is on it" : " (old head)"}
          </span>
        </p>
      )}
      <p
        data-testid="review-not-ready"
        className="flex min-w-0 items-center gap-1.5 text-sm text-muted-foreground"
      >
        <TriangleAlertIcon aria-hidden className="size-3.5 shrink-0" />
        <span className="min-w-0 truncate" title={reasons.join(" · ")}>
          Not ready yet{reasons.length > 0 ? ` · ${reasons[0]}` : ""}
          {reasons.length > 1 && ` · ${reasons.length - 1} more`}
        </span>
      </p>
    </div>
  );
}

/** Why the PR's stable preview link landed here; nothing once the preview is back up. */
export function PreviewUnavailableNotice({ pr }: { pr: PrView }) {
  const state = pr.preview?.state ?? null;
  if (state === "ready" || state === "stale") return null;
  return (
    <ReviewNotice tone="warning" testId="review-preview-unavailable">
      The preview link brought you here: this PR’s factory preview isn’t running (
      {state ?? "not deployed"}). {state === "parked" ? "Restore" : state ? "Redeploy" : "Deploy"}{" "}
      it under Preview to look at the PR live.
    </ReviewNotice>
  );
}
