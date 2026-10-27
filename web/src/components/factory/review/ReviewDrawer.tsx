/**
 * The review drawer (`?review=<pr number>`): everything a human needs to
 * review one PR without leaving the cockpit. The demo (video, steps,
 * screenshots, API calls), the factory preview and which commit it runs,
 * every gate with its evidence, and the actions: approve and open GitHub's
 * merge box, request changes, merge on GitHub, "I merged it".
 */

import { ExternalLinkIcon, MessageSquareWarningIcon, TriangleAlertIcon } from "lucide-react";
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
import { useFactoryNow } from "../factoryClock";
import { plural, shortSha } from "../factoryFormat";
import { DemoPanel } from "./DemoPanel";
import { EvidencePanel } from "./EvidencePanel";
import { PreviewPanel } from "./PreviewPanel";
import { ReadyForMergeBanner } from "./ReadyForMergeBanner";
import { ReviewActions } from "./ReviewActions";

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
}

function StatusLine({ pr, nowS }: { pr: PrView; nowS: number }) {
  if (pr.state !== "open") return null;
  if (pr.ready) return <ReadyForMergeBanner pr={pr} nowS={nowS} />;
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

export function ReviewDrawer({
  factoryId,
  pr,
  demo,
  onClose,
  snapshot,
  nowS: nowProp,
}: ReviewDrawerProps) {
  const clockS = Math.floor(useFactoryNow().getTime() / 1000);
  const nowS = nowProp ?? clockS;
  const project =
    pr && snapshot ? (snapshot.projects.find((p) => p.slug === pr.project_slug) ?? null) : null;
  const members = snapshot?.members ?? [];
  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent size="xl" data-testid="review-drawer" data-pr={pr?.number}>
        <SheetHeader className="gap-1.5">
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
              <p className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-sm text-muted-foreground">
                <span className="min-w-0 truncate font-mono" title={pr.head_ref}>
                  {pr.head_ref}
                </span>
                <span aria-hidden>·</span>
                <span className="font-mono tabular-nums">{shortSha(pr.head_sha)}</span>
                <span aria-hidden>·</span>
                <span className="font-mono tabular-nums">
                  <span className="text-success">+{pr.additions}</span>{" "}
                  <span className="text-destructive">−{pr.deletions}</span>
                </span>
                <span aria-hidden>·</span>
                <span>{plural(pr.changed_files, "file")}</span>
                {pr.task && (
                  <>
                    <span aria-hidden>·</span>
                    <span className="font-mono">{pr.task}</span>
                  </>
                )}
              </p>
              <StatusLine pr={pr} nowS={nowS} />
            </>
          ) : (
            <SheetDescription>This PR isn’t tracked by this factory.</SheetDescription>
          )}
        </SheetHeader>
        {pr && (
          <SheetBody className="grid grid-cols-1 content-start gap-6 md:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
            <DemoPanel factoryId={factoryId} pr={pr} project={project} demo={demo} nowS={nowS} />
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
