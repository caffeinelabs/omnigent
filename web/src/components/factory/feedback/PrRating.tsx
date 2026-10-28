/**
 * Rate a PR 1–5 with reason tags. While the PR is open the rating rides on
 * the review (Approve or Request changes stores it); once merged or closed it
 * is sent on its own.
 */

import { useState } from "react";
import { CheckIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { PrView } from "@/lib/factoryApi";
import { controlErrorMessage } from "../controls/controlModel";
import { PR_RATING_TAGS, ratingReceipt, tagsFor } from "./feedbackModel";
import { ReasonTags, StarRating, useSendFeedback } from "./FeedbackWidgets";

export interface PrRatingValue {
  stars: number | null;
  tags: string[];
}

/** The stars and tags row, controlled (the review sends it). */
export function PrRatingInput({
  value,
  onChange,
  disabled = false,
}: {
  value: PrRatingValue;
  onChange: (value: PrRatingValue) => void;
  disabled?: boolean;
}) {
  return (
    <div data-testid="pr-rating" className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
      <span className="text-sm text-muted-foreground">Rate (optional)</span>
      <StarRating
        value={value.stars}
        onChange={(stars) => onChange({ stars, tags: stars === value.stars ? value.tags : [] })}
        disabled={disabled}
        label="PR rating"
        testId="pr-rating-stars"
      />
      <ReasonTags
        tags={tagsFor(value.stars, PR_RATING_TAGS)}
        picked={value.tags}
        disabled={disabled}
        onToggle={(tag) =>
          onChange({
            ...value,
            tags: value.tags.includes(tag)
              ? value.tags.filter((t) => t !== tag)
              : [...value.tags, tag],
          })
        }
        testId="pr-rating-tags"
      />
    </div>
  );
}

/** A merged or closed PR: rate it on its own. */
export function PrRatingStandalone({ factoryId, pr }: { factoryId: string; pr: PrView }) {
  const send = useSendFeedback(factoryId);
  const [value, setValue] = useState<PrRatingValue>({ stars: null, tags: [] });
  const [sent, setSent] = useState<string | null>(null);
  if (sent) {
    return (
      <p
        data-testid="pr-rating-sent"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground"
      >
        <CheckIcon aria-hidden className="size-3.5 text-success" />
        {sent}
      </p>
    );
  }
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <PrRatingInput value={value} onChange={setValue} disabled={send.isPending} />
      {value.stars != null && (
        <Button
          size="xs"
          variant="outline"
          loading={send.isPending}
          onClick={() =>
            send.mutate(
              {
                target_kind: "pr",
                target_id: pr.id,
                kind: "rating",
                rating: value.stars!,
                tags: value.tags,
                ...(pr.project_slug ? { project: pr.project_slug } : {}),
                ...(pr.head_sha ? { head_sha: pr.head_sha } : {}),
              },
              { onSuccess: () => setSent(ratingReceipt(value.stars!, value.tags)) },
            )
          }
          data-testid="pr-rating-send"
          componentId="factory.feedback.pr_rating"
        >
          Send rating
        </Button>
      )}
      {send.error && (
        <span className="text-sm text-destructive">{controlErrorMessage(send.error)}</span>
      )}
    </div>
  );
}
