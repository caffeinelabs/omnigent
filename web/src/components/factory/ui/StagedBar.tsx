/**
 * The bar that says changes are staged and nothing applies yet ("3 changes
 * staged · v13 · Review"). Violet and dashed, like every staged control;
 * fixed above the phone tab bar, or inline where a page places it.
 */

import { PencilIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { plural } from "../factoryFormat";
import { FOCUS_RING } from "./statusTokens";

export interface StagedBarProps {
  count: number;
  /** What is staged, singular ("change", "team change"). */
  noun?: string;
  /** After the count ("v13", "on the project's team"). */
  context?: string;
  onReview: () => void;
  reviewLabel?: string;
  onDiscard?: () => void;
  /** `fixed` floats at the bottom (above the phone tab bar); `inline` flows in place. */
  placement?: "fixed" | "inline";
  className?: string;
}

/** "1 change staged · v13". */
export function stagedText(count: number, noun = "change", context?: string): string {
  return `${plural(count, noun)} staged${context ? ` · ${context}` : ""}`;
}

export function StagedBar({
  count,
  noun = "change",
  context,
  onReview,
  reviewLabel = "Review",
  onDiscard,
  placement = "fixed",
  className,
}: StagedBarProps) {
  if (count <= 0) return null;
  return (
    <div
      role="status"
      data-testid="staged-bar"
      className={cn(
        "flex items-center gap-2.5 rounded-xl border border-dashed border-status-violet py-2 pr-2 pl-3.5 text-ui shadow-xl",
        "bg-[color-mix(in_srgb,var(--card-solid)_90%,var(--status-violet))]",
        placement === "fixed" &&
          "fixed bottom-[18px] left-1/2 z-[45] max-w-[calc(100vw-24px)] -translate-x-1/2 max-[760px]:right-3 max-[760px]:bottom-[72px] max-[760px]:left-3 max-[760px]:translate-x-0",
        className,
      )}
    >
      <PencilIcon aria-hidden className="size-3.5 shrink-0 text-status-violet-fg" />
      <span className="min-w-0 flex-1">
        <b className="font-semibold">{plural(count, noun)}</b> staged
        {context && <span className="text-foreground/75"> · {context}</span>}
      </span>
      {onDiscard && (
        <button
          type="button"
          onClick={onDiscard}
          className={cn(
            "h-7 shrink-0 cursor-pointer rounded-md px-2 text-foreground/75 hover:bg-muted hover:text-foreground",
            FOCUS_RING,
          )}
        >
          Discard
        </button>
      )}
      <button
        type="button"
        onClick={onReview}
        className={cn(
          "h-7 shrink-0 cursor-pointer rounded-md bg-status-violet px-3 font-semibold text-[#160f2b] hover:bg-status-violet/85",
          FOCUS_RING,
        )}
      >
        {reviewLabel}
      </button>
    </div>
  );
}
