/**
 * The small ways people tell the factory how it did, each one `POST
 * /v1/factory/feedback` (viewers too: feedback is not a lever):
 *
 * - `StarRating` + `ReasonTags`: 1–5 stars with reason tags (PR reviews, a
 *   project's wrap-up);
 * - `DemoThumbs`: was this demo useful 👍/👎;
 * - `UnneededButton`: "this question was unnecessary" on a decision;
 * - `CalibrationButtons`: "Is this right?" on a retro or finding label;
 * - `WrapUpRating`: rate a finished project.
 *
 * Each shows a one-line receipt once stored and doesn't send twice.
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CheckIcon, StarIcon, ThumbsDownIcon, ThumbsUpIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { type FeedbackRequest, postFactoryFeedback } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { controlErrorMessage } from "../controls/controlModel";
import { useInvalidateLearn } from "../insights/learnQueries";
import { ROOT_CAUSE_TAGS, WRAP_UP_TAGS, ratingReceipt, tagLabel, tagsFor } from "./feedbackModel";

/** Store one signal; Insights (and anything reading feedback) refreshes. */
export function useSendFeedback(factoryId: string) {
  const invalidate = useInvalidateLearn(factoryId);
  return useMutation({
    mutationFn: (request: Omit<FeedbackRequest, "factory_id">) =>
      postFactoryFeedback({ ...request, factory_id: factoryId }),
    onSuccess: invalidate,
  });
}

export function StarRating({
  value,
  onChange,
  disabled = false,
  label = "Rating",
  testId = "star-rating",
}: {
  value: number | null;
  onChange: (stars: number) => void;
  disabled?: boolean;
  label?: string;
  testId?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const shown = hover ?? value ?? 0;
  return (
    <span
      role="radiogroup"
      aria-label={label}
      data-testid={testId}
      data-value={value ?? undefined}
      className="inline-flex items-center"
      onMouseLeave={() => setHover(null)}
    >
      {[1, 2, 3, 4, 5].map((stars) => (
        <button
          key={stars}
          type="button"
          role="radio"
          aria-checked={value === stars}
          aria-label={`${stars} star${stars === 1 ? "" : "s"}`}
          disabled={disabled}
          data-testid={`${testId}-${stars}`}
          onMouseEnter={() => setHover(stars)}
          onClick={() => onChange(stars)}
          className="inline-flex size-6 cursor-pointer items-center justify-center rounded-sm text-muted-foreground transition-colors hover:text-warning focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none disabled:cursor-default disabled:opacity-60"
        >
          <StarIcon
            aria-hidden
            className={cn("size-4", stars <= shown && "fill-warning text-warning")}
          />
        </button>
      ))}
    </span>
  );
}

export function ReasonTags({
  tags,
  picked,
  onToggle,
  disabled = false,
  testId = "reason-tags",
}: {
  tags: readonly string[];
  picked: readonly string[];
  onToggle: (tag: string) => void;
  disabled?: boolean;
  testId?: string;
}) {
  if (tags.length === 0) return null;
  return (
    <span data-testid={testId} className="inline-flex min-w-0 flex-wrap gap-1">
      {tags.map((tag) => {
        const on = picked.includes(tag);
        return (
          <button
            key={tag}
            type="button"
            aria-pressed={on}
            disabled={disabled}
            data-testid={`${testId}-${tag}`}
            onClick={() => onToggle(tag)}
            className={cn(
              "h-6 cursor-pointer rounded-full border px-2 text-xs transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
              on
                ? "border-foreground/20 bg-foreground/10 text-foreground"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            {tagLabel(tag)}
          </button>
        );
      })}
    </span>
  );
}

/** 👍/👎 under a demo: was it useful for the review? */
export function DemoThumbs({
  factoryId,
  demoId,
  project,
  headSha,
}: {
  factoryId: string;
  demoId: string;
  project?: string | null;
  headSha?: string | null;
}) {
  const send = useSendFeedback(factoryId);
  const [sent, setSent] = useState<1 | -1 | null>(null);
  const vote = (rating: 1 | -1) => {
    if (sent != null || send.isPending) return;
    send.mutate(
      {
        target_kind: "demo",
        target_id: demoId,
        kind: "useful",
        rating,
        ...(project ? { project } : {}),
        ...(headSha ? { head_sha: headSha } : {}),
      },
      { onSuccess: () => setSent(rating) },
    );
  };
  return (
    <span
      data-testid="demo-thumbs"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground"
    >
      {sent != null ? (
        <span data-testid="demo-thumbs-sent" className="inline-flex items-center gap-1">
          <CheckIcon aria-hidden className="size-3.5 text-success" />
          {sent > 0 ? "Useful" : "Not useful"} · thanks
        </span>
      ) : (
        <>
          <span>Demo useful?</span>
          {([1, -1] as const).map((rating) => (
            <Button
              key={rating}
              size="icon-xs"
              variant="ghost"
              onClick={() => vote(rating)}
              disabled={send.isPending}
              aria-label={rating > 0 ? "Useful" : "Not useful"}
              data-testid={`demo-thumbs-${rating > 0 ? "up" : "down"}`}
              componentId="factory.feedback.demo_useful"
            >
              {rating > 0 ? <ThumbsUpIcon /> : <ThumbsDownIcon />}
            </Button>
          ))}
        </>
      )}
      {send.error && <span className="text-destructive">{controlErrorMessage(send.error)}</span>}
    </span>
  );
}

/** "Unnecessary?" on a decision: feeds the unneeded-questions metric and detector. */
export function UnneededButton({
  factoryId,
  decisionId,
  decisionRef,
  project,
}: {
  factoryId: string;
  decisionId: string;
  decisionRef: string;
  project?: string | null;
}) {
  const send = useSendFeedback(factoryId);
  const [sent, setSent] = useState(false);
  if (sent) {
    return (
      <span
        data-testid={`decision-unneeded-sent-${decisionRef}`}
        className="inline-flex items-center gap-1 self-start text-xs text-muted-foreground"
      >
        <CheckIcon aria-hidden className="size-3 text-success" />
        Marked unnecessary
      </span>
    );
  }
  return (
    <button
      type="button"
      disabled={send.isPending}
      title="This question didn't need a human: the agent could have decided it"
      onClick={() =>
        send.mutate(
          {
            target_kind: "decision",
            target_id: decisionId,
            kind: "unneeded",
            ...(project ? { project } : {}),
          },
          { onSuccess: () => setSent(true) },
        )
      }
      data-testid={`decision-unneeded-${decisionRef}`}
      className="cursor-pointer self-start text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:opacity-60"
    >
      Unnecessary?
    </button>
  );
}

/** "Is this right?" on a retro or finding label: confirm it or name the right cause. */
export function CalibrationButtons({
  factoryId,
  issueId,
  label,
  project,
  testId,
}: {
  factoryId: string;
  issueId: string;
  /** The label Sentinel or a detector gave (a root cause), when it has one. */
  label: string | null;
  project?: string | null;
  testId: string;
}) {
  const send = useSendFeedback(factoryId);
  const [sent, setSent] = useState<string | null>(null);
  const [correcting, setCorrecting] = useState(false);
  const submit = (rating: 1 | -1, corrected?: string) =>
    send.mutate(
      {
        target_kind: "issue",
        target_id: issueId,
        kind: "calibration",
        rating,
        tags: corrected ? [corrected] : [],
        ...(project ? { project } : {}),
      },
      {
        onSuccess: () => {
          setSent(
            rating > 0
              ? "Confirmed"
              : corrected
                ? `Corrected to ${tagLabel(corrected)}`
                : "Marked wrong",
          );
          setCorrecting(false);
        },
      },
    );
  if (sent) {
    return (
      <span
        data-testid={`${testId}-sent`}
        className="inline-flex items-center gap-1 text-xs text-muted-foreground"
      >
        <CheckIcon aria-hidden className="size-3 text-success" />
        {sent} · the next retro learns from it
      </span>
    );
  }
  return (
    <span
      data-testid={testId}
      className="inline-flex flex-wrap items-center gap-1 text-xs text-muted-foreground"
    >
      <span>Is this right?</span>
      <Button
        size="xs"
        variant="ghost"
        onClick={() => submit(1)}
        disabled={send.isPending}
        data-testid={`${testId}-yes`}
        componentId="factory.feedback.calibration_yes"
      >
        <ThumbsUpIcon />
        Yes
      </Button>
      {correcting ? (
        <Select
          onValueChange={(cause) => submit(-1, cause)}
          componentId="factory.feedback.calibration_cause"
          valueHasNoPii
        >
          <SelectTrigger
            size="sm"
            aria-label="The right cause"
            data-testid={`${testId}-cause`}
            className="h-6 w-40 text-xs"
          >
            <SelectValue placeholder="The right cause…" />
          </SelectTrigger>
          <SelectContent>
            {ROOT_CAUSE_TAGS.filter((c) => c !== label).map((cause) => (
              <SelectItem key={cause} value={cause}>
                {tagLabel(cause)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <Button
          size="xs"
          variant="ghost"
          onClick={() => setCorrecting(true)}
          disabled={send.isPending}
          data-testid={`${testId}-no`}
          componentId="factory.feedback.calibration_no"
        >
          <ThumbsDownIcon />
          No
        </Button>
      )}
    </span>
  );
}

/** Rate a finished project: how did it go? (1–5 stars, reason tags.) */
export function WrapUpRating({ factoryId, project }: { factoryId: string; project: string }) {
  const send = useSendFeedback(factoryId);
  const [stars, setStars] = useState<number | null>(null);
  const [tags, setTags] = useState<string[]>([]);
  const [sent, setSent] = useState<string | null>(null);
  if (sent) {
    return (
      <p
        data-testid="wrap-up-sent"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground"
      >
        <CheckIcon aria-hidden className="size-3.5 text-success" />
        {sent}
      </p>
    );
  }
  return (
    <div data-testid="wrap-up" className="flex min-w-0 flex-col gap-2">
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">How did this project go?</span>
        <StarRating
          value={stars}
          onChange={(s) => {
            setStars(s);
            setTags([]);
          }}
          label="Project rating"
          testId="wrap-up-stars"
        />
      </div>
      <ReasonTags
        tags={tagsFor(stars, WRAP_UP_TAGS)}
        picked={tags}
        onToggle={(tag) =>
          setTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]))
        }
        testId="wrap-up-tags"
      />
      {stars != null && (
        <Button
          size="sm"
          className="self-start"
          loading={send.isPending}
          onClick={() =>
            send.mutate(
              {
                target_kind: "project",
                target_id: project,
                kind: "rating",
                rating: stars,
                tags,
                project,
              },
              { onSuccess: () => setSent(ratingReceipt(stars, tags)) },
            )
          }
          data-testid="wrap-up-send"
          componentId="factory.feedback.wrap_up"
        >
          Send rating
        </Button>
      )}
      {send.error && <p className="text-sm text-destructive">{controlErrorMessage(send.error)}</p>}
    </div>
  );
}
