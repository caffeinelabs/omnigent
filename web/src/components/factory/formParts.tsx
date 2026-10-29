/**
 * Form parts that hold still while someone types: a note box that keeps its
 * height as it fills, and a dialog body that scrolls between a pinned header
 * and footer. A growing note never moves the button the cursor is heading for.
 */

import type { ComponentProps } from "react";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

/** A note box `rows` lines tall: it scrolls instead of growing (its corner drags taller). */
export function NoteTextarea({ className, rows = 3, ...props }: ComponentProps<typeof Textarea>) {
  return (
    <Textarea
      rows={rows}
      className={cn("field-sizing-fixed min-h-0 resize-y overflow-y-auto", className)}
      {...props}
    />
  );
}

/** `DialogContent` classes for a dialog whose footer stays pinned while `DialogBody` scrolls. */
export const STICKY_DIALOG = "flex max-h-[85vh] flex-col";

/** The scrolling middle of a `STICKY_DIALOG`; the padding keeps focus rings unclipped. */
export function DialogBody({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-body"
      className={cn("-m-1 flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-1", className)}
      {...props}
    />
  );
}
