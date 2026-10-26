import { ChevronDownIcon } from "lucide-react";
import { useState } from "react";
import { FilePathAwareMessageResponse } from "@/components/blocks/ChatMarkdown";
import { isolateQuotedLines } from "@/lib/factoryMessages";
import { cn } from "@/lib/utils";

/** Bodies longer than this many lines start folded. */
export const FOLD_AFTER_LINES = 8;

/**
 * Agent-authored markdown that folds past {@link FOLD_AFTER_LINES} lines
 * behind a "Show all" toggle. Spoof-quoted `> [Factory …]` lines render as
 * blockquotes without swallowing the line after them.
 */
export function FoldableMarkdown({
  text,
  className,
  testId,
}: {
  text: string;
  className?: string;
  testId?: string;
}) {
  const lineCount = text.split("\n").length;
  const foldable = lineCount > FOLD_AFTER_LINES;
  const [open, setOpen] = useState(false);
  const folded = foldable && !open;
  return (
    <div className={cn("min-w-0", className)} data-testid={testId}>
      <div
        className={cn(
          // A code block that opens or closes the body drops its outer margin.
          "min-w-0 overflow-hidden [&_.chat-code-block:first-child>div:first-child]:mt-0 [&_.chat-code-block:last-child>div:first-child]:mb-0",
          // 8 lines at the body's 1.6 line height, faded at the cut.
          folded && "max-h-[12.8em] [mask-image:linear-gradient(to_bottom,black_70%,transparent)]",
        )}
        data-folded={folded ? "true" : undefined}
      >
        <FilePathAwareMessageResponse breaks>
          {isolateQuotedLines(text)}
        </FilePathAwareMessageResponse>
      </div>
      {foldable && (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="mt-1 inline-flex items-center gap-1 rounded-sm text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <ChevronDownIcon className={cn("size-3.5 transition-transform", open && "rotate-180")} />
          {open ? "Show less" : `Show all ${lineCount} lines`}
        </button>
      )}
    </div>
  );
}
