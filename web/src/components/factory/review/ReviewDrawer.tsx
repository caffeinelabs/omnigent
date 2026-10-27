/**
 * The review drawer (`?review=<pr number>`): everything a human needs to
 * review one PR without leaving the cockpit. The demo (video, steps,
 * screenshots, API calls), the factory preview and which commit it runs,
 * every gate with its evidence, and the actions: approve and open GitHub's
 * merge box, request changes, merge on GitHub, "I merged it".
 */

import {
  ExternalLinkIcon,
  GitMergeIcon,
  GitPullRequestClosedIcon,
  MessageSquareWarningIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { DemoView, FactorySnapshot, PrView } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { useFactoryNow } from "../factoryClock";
import { plural, shortSha } from "../factoryFormat";
import { DemoPanel } from "./DemoPanel";
import { EvidencePanel } from "./EvidencePanel";
import { PreviewPanel } from "./PreviewPanel";
import { ReadyForMergeBanner } from "./ReadyForMergeBanner";
import { ReviewActions } from "./ReviewActions";
import { demoRecorder } from "./reviewModel";
import { DotParts, ReviewNotice } from "./reviewParts";

export interface ReviewDrawerProps {
  /** The factory (root session) id. */
  factoryId: string;
  /** The PR under review; `null` when `?review=` names a PR this factory doesn't track. */
  pr: PrView | null;
  /** The demo to show (normally `pr.demo`). */
  demo: DemoView | null;
  /** Close the drawer (clears `?review=`). */
  onClose: () => void;
  /** The cockpit snapshot: the PR's project (goal, PL, slots) and members (reviewer). */
  snapshot?: FactorySnapshot;
  /** The clock ages read against (epoch seconds); the factory clock when omitted. */
  nowS?: number;
  /** The PR's stable preview link redirected here because the preview isn't running. */
  previewUnavailable?: boolean;
}

function StatusLine({ pr, nowS }: { pr: PrView; nowS: number }) {
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
      </p>
    );
  }
  if (pr.ready) return <ReadyForMergeBanner pr={pr} nowS={nowS} testId="review-ready-banner" />;
  const reasons = pr.gates.reasons;
  return (
    <div className="flex flex-col gap-1">
      {pr.human_review?.verdict === "changes_requested" && (
        <p
          data-testid="review-changes-requested"
          className="inline-flex items-center gap-1 self-start rounded-md bg-warning/15 px-2 py-0.5 text-sm text-warning"
        >
          <MessageSquareWarningIcon aria-hidden className="size-3.5" />
          Changes requested by {pr.human_review.by} · the PL is on it
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
function PreviewUnavailableNotice({ pr }: { pr: PrView }) {
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

export function ReviewDrawer({
  factoryId,
  pr,
  demo,
  onClose,
  snapshot,
  nowS: nowProp,
  previewUnavailable = false,
}: ReviewDrawerProps) {
  const clockS = Math.floor(useFactoryNow().getTime() / 1000);
  const nowS = nowProp ?? clockS;
  const project =
    pr && snapshot ? (snapshot.projects.find((p) => p.slug === pr.project_slug) ?? null) : null;
  const members = snapshot?.members ?? [];
  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        size="xl"
        data-testid="review-drawer"
        data-pr={pr?.number}
        // Escape inside a composer is the composer's (a draft must not vanish with the drawer).
        onEscapeKeyDown={(event) => {
          if (event.target instanceof Element && event.target.closest("[data-composer]")) {
            event.preventDefault();
          }
        }}
      >
        {/* The header's right padding is the title row's (it clears the close button). */}
        <SheetHeader className="gap-1.5 pr-5">
          <div className="flex min-w-0 items-center gap-2 pr-8">
            <SheetTitle className="min-w-0 truncate">
              {pr ? `Review · PR #${pr.number}` : "Review"}
              {pr?.project_slug && (
                <span className="font-normal text-muted-foreground"> · {pr.project_slug}</span>
              )}
            </SheetTitle>
            {pr && (
              <Button asChild variant="outline" size="xs" className="ml-auto shrink-0">
                <a href={pr.url} target="_blank" rel="noreferrer" data-testid="review-open-pr">
                  Open PR
                  <ExternalLinkIcon />
                </a>
              </Button>
            )}
          </div>
          {pr ? (
            <>
              <SheetDescription className="text-ui text-foreground">{pr.title}</SheetDescription>
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
                      node: <span className="font-mono tabular-nums">{shortSha(pr.head_sha)}</span>,
                    },
                    {
                      key: "diff",
                      node: (
                        <span className="font-mono whitespace-pre tabular-nums">
                          <span className={cn(pr.additions > 0 && "text-success")}>
                            +{pr.additions}
                          </span>{" "}
                          <span className={cn(pr.deletions > 0 && "text-destructive")}>
                            −{pr.deletions}
                          </span>
                        </span>
                      ),
                    },
                    { key: "files", node: <span>{plural(pr.changed_files, "file")}</span> },
                    pr.task
                      ? { key: "task", node: <span className="font-mono">{pr.task}</span> }
                      : null,
                  ]}
                />
              </p>
              <StatusLine pr={pr} nowS={nowS} />
              {previewUnavailable && <PreviewUnavailableNotice pr={pr} />}
            </>
          ) : (
            <SheetDescription>This PR isn’t tracked by this factory.</SheetDescription>
          )}
        </SheetHeader>
        {pr && (
          <SheetBody className="grid grid-cols-1 content-start gap-6 md:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
            <DemoPanel
              factoryId={factoryId}
              pr={pr}
              project={project}
              demo={demo}
              nowS={nowS}
              recorder={demoRecorder(pr, members)}
            />
            <div className="flex min-w-0 flex-col gap-5 max-md:border-t max-md:border-border max-md:pt-5 md:border-l md:border-border md:pl-6">
              <PreviewPanel factoryId={factoryId} pr={pr} project={project} nowS={nowS} />
              <div className="border-t border-border pt-4">
                <EvidencePanel pr={pr} members={members} nowS={nowS} />
              </div>
            </div>
          </SheetBody>
        )}
        {pr && (
          <SheetFooter className="sm:flex-col sm:justify-start">
            <ReviewActions factoryId={factoryId} pr={pr} project={project} nowS={nowS} />
          </SheetFooter>
        )}
      </SheetContent>
    </Sheet>
  );
}
