/**
 * The review drawer's action bar: approve and open GitHub's merge box,
 * request changes (the text goes to the PL), merge on GitHub, copy a merge
 * summary, open the PL's chat, and "I merged it" (asks the poller to look
 * now). Humans merge caffeinelabs/app; the factory never does. Once the
 * poller sees the merge, the bar becomes a merged banner.
 */

import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  CheckIcon,
  CopyIcon,
  ExternalLinkIcon,
  GitMergeIcon,
  GitPullRequestClosedIcon,
  MessageSquareIcon,
  MessageSquareWarningIcon,
  RefreshCwIcon,
  SendIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import { copyText } from "@/lib/clipboard";
import type { PrView, ProjectView } from "@/lib/factoryApi";
import { FactoryApiError, refreshFactoryPr, sendPrFeedback } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { ageText } from "../board/boardModel";
import { mergeUrl } from "../board/PrRow";
import { TONE_CHIP, shortSha } from "../factoryFormat";
import { mergeSummary } from "./reviewModel";
import { DotParts } from "./reviewParts";

export const HUMANS_MERGE = "Humans merge caffeinelabs/app. The factory never merges.";

/** How long "I merged it" stays disabled after a check (the server allows 1 per 10 s). */
export const REFRESH_COOLDOWN_MS = 10_000;

function useInvalidateFactory(factoryId: string) {
  const queryClient = useQueryClient();
  return () => {
    for (const key of factoryInvalidationKeys(factoryId)) {
      void queryClient.invalidateQueries({ queryKey: key });
    }
  };
}

/**
 * "⟳ I merged it": asks the PR poller to re-read the PR now. Disabled for
 * 10 s after each check (the server rate-limits it), reading "Checking…".
 */
export function IMergedItButton({
  factoryId,
  pr,
  className,
  size = "sm",
  testId = "review-i-merged-it",
}: {
  factoryId: string;
  pr: PrView;
  className?: string;
  size?: "sm" | "xs";
  testId?: string;
}) {
  const invalidate = useInvalidateFactory(factoryId);
  const [checking, setChecking] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  useEffect(() => () => window.clearTimeout(timer.current ?? undefined), []);
  const coolDown = (ms: number) => {
    setChecking(true);
    window.clearTimeout(timer.current ?? undefined);
    timer.current = window.setTimeout(() => {
      setChecking(false);
      setNote(null);
    }, ms);
  };
  const check = () => {
    if (checking) return;
    setNote(null);
    coolDown(REFRESH_COOLDOWN_MS);
    refreshFactoryPr(pr.id).then(invalidate, (error: unknown) => {
      if (error instanceof FactoryApiError && error.status === 429) {
        const retry = Number((error.body as { retry_after?: number } | null)?.retry_after);
        setNote("Checked moments ago");
        if (Number.isFinite(retry) && retry > 0) coolDown(retry * 1000);
        return;
      }
      setNote(error instanceof Error ? `Couldn’t check: ${error.message}` : "Couldn’t check");
      window.clearTimeout(timer.current ?? undefined);
      setChecking(false);
    });
  };
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <Button
        variant="ghost"
        size={size}
        onClick={check}
        disabled={checking}
        data-testid={testId}
        data-state={checking ? "checking" : "idle"}
        componentId="factory.review.i_merged_it"
        title="Merged on GitHub? The poller checks now instead of on its next round."
        className="text-muted-foreground max-md:flex-1"
      >
        <RefreshCwIcon
          className={cn(checking && "animate-spin motion-reduce:animate-none")}
          aria-hidden
        />
        {checking ? "Checking…" : "I merged it"}
      </Button>
      {note && (
        <span data-testid={`${testId}-note`} className="text-sm text-muted-foreground">
          {note}
        </span>
      )}
    </span>
  );
}

function MergedBanner({ pr, nowS }: { pr: PrView; nowS: number }) {
  const merged = pr.merged;
  const previewGone = !pr.preview || pr.preview.state === "down";
  return (
    <p
      data-testid="review-merged-banner"
      className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 rounded-lg bg-brand-accent/10 px-3 py-2 text-sm text-brand-accent"
    >
      <span className="relative inline-flex">
        <span
          aria-hidden
          className="absolute -inset-1 animate-[user-msg-flash_800ms_ease-out] rounded-full motion-reduce:hidden"
        />
        <GitMergeIcon aria-hidden className="size-4" />
      </span>
      <DotParts
        parts={[
          {
            key: "by",
            node: `Merged${merged?.by ? ` by @${merged.by}` : ""}`,
            className: "font-medium",
          },
          merged?.sha
            ? { key: "sha", node: shortSha(merged.sha), className: "font-mono tabular-nums" }
            : null,
          merged ? { key: "at", node: ageText(nowS - merged.at) } : null,
          { key: "pl", node: "PL notified" },
          {
            key: "preview",
            node: previewGone ? "preview torn down" : "preview teardown pending",
          },
        ]}
      />
    </p>
  );
}

function ClosedBanner({ pr, nowS }: { pr: PrView; nowS: number }) {
  return (
    <p
      data-testid="review-closed-banner"
      className="flex flex-wrap items-center gap-x-1.5 rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground"
    >
      <GitPullRequestClosedIcon aria-hidden className="size-4" />
      <DotParts
        parts={[
          {
            key: "closed",
            node: "Closed without merge",
            className: "font-medium text-foreground",
          },
          pr.closed_at != null ? { key: "at", node: ageText(nowS - pr.closed_at) } : null,
          { key: "pl", node: "the PL was asked whether to rework or abandon it" },
        ]}
      />
    </p>
  );
}

type FeedbackState =
  | { phase: "idle" }
  | { phase: "sending"; verdict: "approved" | "changes_requested" }
  | { phase: "sent"; verdict: "approved" | "changes_requested" }
  | { phase: "error"; verdict: "approved" | "changes_requested"; message: string };

export function ReviewActions({
  factoryId,
  pr,
  project,
  nowS,
}: {
  factoryId: string;
  pr: PrView;
  project: ProjectView | null;
  nowS: number;
}) {
  const invalidate = useInvalidateFactory(factoryId);
  const [feedback, setFeedback] = useState<FeedbackState>({ phase: "idle" });
  const [composing, setComposing] = useState(false);
  const [text, setText] = useState("");
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  const [blockedUrl, setBlockedUrl] = useState<string | null>(null);

  if (pr.state === "merged") return <MergedBanner pr={pr} nowS={nowS} />;
  if (pr.state === "closed") return <ClosedBanner pr={pr} nowS={nowS} />;

  const busy = feedback.phase === "sending";

  const approve = () => {
    setFeedback({ phase: "sending", verdict: "approved" });
    const url = mergeUrl(pr);
    // Open the tab inside the click (popup blockers allow only that) and point
    // it at GitHub once the approval is stored. No "noopener" feature: with it,
    // window.open returns null even when the tab opened.
    const tab = window.open("about:blank", "_blank");
    if (tab) tab.opener = null;
    sendPrFeedback(pr.id, { verdict: "approved" }).then(
      () => {
        setFeedback({ phase: "sent", verdict: "approved" });
        invalidate();
        if (tab && !tab.closed) tab.location.href = url;
        else setBlockedUrl(url);
      },
      (error: unknown) => {
        tab?.close();
        setFeedback({
          phase: "error",
          verdict: "approved",
          message: error instanceof Error ? error.message : String(error),
        });
      },
    );
  };

  const sendChanges = () => {
    const body = text.trim();
    if (!body || busy) return;
    // Optimistic: the chip shows at once and the composer closes; an error restores both.
    setFeedback({ phase: "sending", verdict: "changes_requested" });
    setComposing(false);
    sendPrFeedback(pr.id, { verdict: "changes_requested", text: body }).then(
      () => {
        setFeedback({ phase: "sent", verdict: "changes_requested" });
        setText("");
        invalidate();
      },
      (error: unknown) => {
        setFeedback({
          phase: "error",
          verdict: "changes_requested",
          message: error instanceof Error ? error.message : String(error),
        });
        setComposing(true);
      },
    );
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // The drawer ignores Escape here; an empty composer folds, a draft stays.
    if (event.key === "Escape") {
      if (!text.trim()) setComposing(false);
      return;
    }
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      sendChanges();
    }
  };

  const copySummary = () => {
    const flash = (state: "copied" | "failed") => {
      setCopied(state);
      window.setTimeout(() => setCopied("idle"), 2000);
    };
    copyText(mergeSummary(pr, project, window.location.origin, factoryId)).then(
      () => flash("copied"),
      () => flash("failed"),
    );
  };

  const chip =
    feedback.phase === "sending" || feedback.phase === "sent" ? (
      <span
        data-testid="review-feedback-chip"
        data-state={feedback.phase}
        className={cn(
          "inline-flex h-6 items-center gap-1 rounded-md px-2 text-sm",
          TONE_CHIP[feedback.verdict === "approved" ? "pass" : "warning"],
        )}
      >
        {feedback.verdict === "approved" ? (
          <CheckIcon aria-hidden className="size-3.5" />
        ) : (
          <MessageSquareWarningIcon aria-hidden className="size-3.5" />
        )}
        {feedback.verdict === "approved" ? "Approved by you" : "Changes requested by you"} ·{" "}
        {feedback.phase === "sending" ? "sending…" : "sent to the PL"}
      </span>
    ) : null;

  const composer = composing && (
    // `data-composer`: the drawer leaves Escape here to the composer.
    <div className="flex flex-col gap-2" data-testid="review-feedback-composer" data-composer>
      <Textarea
        data-testid="review-feedback-text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="What should change? The PL gets this verbatim and re-dispatches the IC."
        aria-label="Changes to request"
        autoFocus
        componentId="factory.review.feedback_text"
        className="max-h-40 min-h-16 bg-background"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          onClick={sendChanges}
          disabled={!text.trim() || busy}
          data-testid="review-feedback-send"
          componentId="factory.review.feedback_send"
          className="max-md:flex-1"
        >
          <SendIcon />
          Send to PL
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setComposing(false)}
          componentId="factory.review.feedback_cancel"
          className="text-muted-foreground"
        >
          Cancel
        </Button>
        <span className="text-sm text-muted-foreground max-md:hidden">⌘↵ sends</span>
      </div>
    </div>
  );

  // While composing on a phone only "Request changes…" and the composer stay.
  const hideWhileComposing = composing && "max-md:hidden";

  return (
    <TooltipProvider delayDuration={300}>
      <div data-testid="review-actions" className="flex w-full min-w-0 flex-col gap-2">
        {feedback.phase === "error" && (
          <p data-testid="review-feedback-error" className="text-sm text-destructive">
            Couldn’t send {feedback.verdict === "approved" ? "the approval" : "your changes"}:{" "}
            {feedback.message}
          </p>
        )}
        {blockedUrl && (
          <p className="text-sm text-muted-foreground">
            Approved. Your browser blocked the new tab:{" "}
            <a
              href={blockedUrl}
              target="_blank"
              rel="noreferrer"
              className="text-foreground underline"
            >
              open GitHub’s merge box
            </a>
            .
          </p>
        )}
        <span id="review-humans-merge" className="sr-only">
          {HUMANS_MERGE}
        </span>
        <div className="grid grid-cols-2 gap-2 md:flex md:flex-wrap md:items-center">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                size="sm"
                // Filled only once every gate is green; approving earlier stays possible.
                variant={pr.ready ? "default" : "outline"}
                onClick={approve}
                loading={busy && feedback.verdict === "approved"}
                disabled={busy}
                data-testid="review-approve"
                componentId="factory.review.approve"
                className={cn("col-span-2", hideWhileComposing)}
              >
                <CheckIcon />
                Approve & open merge
                <ExternalLinkIcon />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{HUMANS_MERGE}</TooltipContent>
          </Tooltip>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setComposing((v) => !v)}
            aria-expanded={composing}
            disabled={busy}
            data-testid="review-request-changes"
            componentId="factory.review.request_changes"
            className={cn(composing && "max-md:col-span-2")}
          >
            <MessageSquareWarningIcon />
            Request changes…
          </Button>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button asChild size="sm" variant="outline" className={cn(hideWhileComposing)}>
                <a
                  href={mergeUrl(pr)}
                  target="_blank"
                  rel="noreferrer"
                  data-testid="review-merge-github"
                  // Always in the DOM, unlike the tooltip's own content.
                  aria-describedby="review-humans-merge"
                >
                  Merge on GitHub
                  <ExternalLinkIcon />
                </a>
              </Button>
            </TooltipTrigger>
            <TooltipContent>{HUMANS_MERGE}</TooltipContent>
          </Tooltip>
          {chip && <span className="col-span-2 md:contents">{chip}</span>}
          <span className={cn("col-span-2 flex items-center gap-1 md:ml-auto", hideWhileComposing)}>
            <Button
              size="sm"
              variant="ghost"
              onClick={copySummary}
              data-testid="review-copy-summary"
              data-state={copied}
              aria-label={
                copied === "copied"
                  ? "Copied"
                  : copied === "failed"
                    ? "Couldn’t copy"
                    : "Copy merge summary"
              }
              componentId="factory.review.copy_summary"
              className={cn(
                "max-md:flex-1",
                copied === "failed" ? "text-destructive" : "text-muted-foreground",
              )}
            >
              {copied === "copied" ? <CheckIcon /> : <CopyIcon />}
              {copied === "copied" ? (
                "Copied"
              ) : copied === "failed" ? (
                "Couldn’t copy"
              ) : (
                <>
                  <span className="max-md:hidden">Copy merge summary</span>
                  <span className="md:hidden">Summary</span>
                </>
              )}
            </Button>
            {project?.pl && (
              <Button
                asChild
                size="sm"
                variant="ghost"
                className="text-muted-foreground max-md:flex-1"
              >
                <Link
                  to={project.pl.url}
                  componentId="factory.review.open_pl"
                  data-testid="review-open-pl"
                  aria-label="Open PL chat"
                >
                  <MessageSquareIcon />
                  <span className="max-md:hidden">Open PL chat</span>
                  <span className="md:hidden">PL chat</span>
                </Link>
              </Button>
            )}
            <IMergedItButton factoryId={factoryId} pr={pr} className="max-md:flex-1" />
          </span>
        </div>
        {composer}
      </div>
    </TooltipProvider>
  );
}
