/**
 * How a control shows it is in flight: a chip beside the menu that reads
 * "Pausing… waiting for PL" until the recipient replies, turns amber after
 * 15 min without a reply, red when refused; and the 5 s Undo toast a pause
 * leaves behind.
 */

import { CheckIcon, Loader2Icon, RotateCcwIcon, TriangleAlertIcon, XIcon } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { type PendingControl, pendingShortText, pendingText } from "./controlModel";

/** How long the Undo toast after a pause stays up. */
export const UNDO_TOAST_MS = 5000;

const TONE: Record<PendingControl["state"], string> = {
  sending: "bg-muted text-muted-foreground",
  waiting: "bg-muted text-muted-foreground",
  late: "bg-warning/15 text-warning",
  error: "bg-destructive/10 text-destructive",
  done: "bg-success/10 text-success",
};

export function PendingChip({
  control,
  who,
  testId,
  onRestart,
  onDismiss,
  className,
}: {
  control: PendingControl;
  /** `PL`, a lead's name, or null for a factory-wide control (counts replies). */
  who: string | null;
  testId: string;
  /** Offered once a control waited 15 min without a reply. */
  onRestart?: () => void;
  onDismiss?: () => void;
  className?: string;
}) {
  const text = pendingText(control, who);
  const icon =
    control.state === "late" || control.state === "error" ? (
      <TriangleAlertIcon aria-hidden className="size-3 shrink-0" />
    ) : control.state === "done" ? (
      <CheckIcon aria-hidden className="size-3 shrink-0" />
    ) : (
      <Loader2Icon
        aria-hidden
        className="size-3 shrink-0 animate-spin motion-reduce:animate-none"
      />
    );
  return (
    <span
      role="status"
      data-testid={testId}
      data-state={control.state}
      data-action={control.action}
      title={text}
      className={cn(
        "inline-flex h-6 max-w-[10rem] min-w-0 items-center gap-1 rounded-md px-1.5 text-sm whitespace-nowrap sm:max-w-[18rem]",
        TONE[control.state],
        className,
      )}
    >
      {icon}
      <span className="min-w-0 truncate max-sm:hidden">{text}</span>
      <span aria-hidden className="min-w-0 truncate sm:hidden">
        {pendingShortText(control)}
      </span>
      <span className="sr-only sm:hidden">{text}</span>
      {control.state === "late" && onRestart && (
        <button
          type="button"
          data-testid="control-late-restart"
          onClick={onRestart}
          className="ml-0.5 inline-flex shrink-0 cursor-pointer items-center gap-0.5 font-medium underline underline-offset-2"
        >
          <RotateCcwIcon aria-hidden className="size-3" />
          Restart
        </button>
      )}
      {control.state === "error" && onDismiss && (
        <button
          type="button"
          aria-label="Dismiss"
          onClick={onDismiss}
          className="ml-0.5 shrink-0 cursor-pointer rounded-sm opacity-70 hover:opacity-100"
        >
          <XIcon aria-hidden className="size-3" />
        </button>
      )}
    </span>
  );
}

/**
 * The pill a pause leaves for 5 s: "Pausing api-version · Undo". Undo runs
 * `onUndo` (it sends resume); a newer pause of the same target replaces it.
 */
export function showUndoToast({
  id,
  text,
  onUndo,
}: {
  id: string;
  text: string;
  onUndo: () => void;
}): void {
  toast.custom(
    (toastId) => (
      <div
        data-testid="control-undo-toast"
        className="flex items-center gap-3 rounded-full border border-border bg-card px-4 py-2 text-sm text-foreground shadow-composer dark:bg-card-solid"
      >
        <span>{text}</span>
        <button
          type="button"
          data-testid="control-undo"
          onClick={() => {
            toast.dismiss(toastId);
            onUndo();
          }}
          className="cursor-pointer font-bold underline underline-offset-2 hover:text-primary"
        >
          Undo
        </button>
      </div>
    ),
    { id, duration: UNDO_TOAST_MS, unstyled: true, testId: "control-undo-toast-item" },
  );
}
