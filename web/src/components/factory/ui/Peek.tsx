/**
 * The cockpit's side panel (opportunity, bet, agent, source, merge, health):
 * a right-hand panel on desktop and a 90 %-tall bottom sheet with a grab
 * handle under 760 px. Focus moves to its Close button when it opens and back
 * to whatever opened it when it closes; Esc closes it. Clicks on the cockpit
 * toast (Undo, Dismiss) do not count as outside clicks, so the panel stays.
 */

import { XIcon } from "lucide-react";
import * as Dialog from "radix-ui/dialog";
import { type ReactNode, useLayoutEffect, useRef } from "react";
import { getEmbedRoot } from "@/lib/host";
import { cn } from "@/lib/utils";
import { FOCUS_RING } from "./statusTokens";
import { inToastRegion } from "./Toast";

export interface PeekProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The panel's heading (its accessible name). */
  title: ReactNode;
  /** One line under the title. */
  description?: ReactNode;
  /** Chips or links on the header row, before Close. */
  headerExtra?: ReactNode;
  /** Sticky actions at the bottom. */
  footer?: ReactNode;
  /** 700 px instead of 520 px (bets with their case against). */
  wide?: boolean;
  children: ReactNode;
  className?: string;
}

export function Peek({
  open,
  onOpenChange,
  title,
  description,
  headerExtra,
  footer,
  wide = false,
  children,
  className,
}: PeekProps) {
  const returnTo = useRef<HTMLElement | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Before the panel takes focus, remember what had it so closing can give it back.
  useLayoutEffect(() => {
    if (open && document.activeElement instanceof HTMLElement) {
      returnTo.current = document.activeElement;
    }
  }, [open]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal container={getEmbedRoot() ?? undefined}>
        <Dialog.Overlay
          data-testid="peek-scrim"
          className="fixed inset-0 z-[60] bg-background/55 motion-safe:data-open:animate-in motion-safe:data-open:fade-in-0"
        />
        <Dialog.Content
          data-testid="peek"
          data-wide={wide || undefined}
          // Without a description, say so explicitly (Radix warns otherwise).
          {...(description ? {} : { "aria-describedby": undefined })}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            closeRef.current?.focus();
          }}
          // Undo or Dismiss on the toast is not a click outside the panel.
          onInteractOutside={(event) => {
            if (inToastRegion(event.target)) event.preventDefault();
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const target = returnTo.current;
            returnTo.current = null;
            if (target?.isConnected) target.focus();
          }}
          className={cn(
            "fixed top-0 right-0 bottom-0 z-[61] flex max-w-[100vw] flex-col border-l border-border bg-card-solid pb-[var(--omnigent-inset-bottom,0px)] text-ui text-foreground shadow-xl outline-none",
            wide ? "w-[700px]" : "w-[520px]",
            "motion-safe:data-open:animate-in motion-safe:data-open:fade-in-50 motion-safe:data-open:slide-in-from-right-6",
            "max-[760px]:top-auto max-[760px]:left-0 max-[760px]:h-[90dvh] max-[760px]:w-full max-[760px]:rounded-t-2xl max-[760px]:border-t max-[760px]:border-l-0",
            "max-[760px]:motion-safe:data-open:slide-in-from-bottom-10",
            className,
          )}
        >
          <span
            aria-hidden
            data-testid="peek-grab"
            className="mx-auto mt-2 hidden h-1 w-10 shrink-0 rounded-full bg-border-strong max-[760px]:block"
          />
          <header className="flex shrink-0 items-start gap-2 border-b border-border px-4 py-3">
            <div className="min-w-0 flex-1">
              <Dialog.Title className="text-lg leading-snug font-semibold">{title}</Dialog.Title>
              {description ? (
                <Dialog.Description className="mt-0.5 text-sm text-muted-foreground">
                  {description}
                </Dialog.Description>
              ) : null}
            </div>
            {headerExtra}
            <Dialog.Close asChild>
              <button
                ref={closeRef}
                type="button"
                aria-label="Close"
                className={cn(
                  "grid size-8 shrink-0 cursor-pointer place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground",
                  FOCUS_RING,
                )}
              >
                <XIcon aria-hidden className="size-4" />
              </button>
            </Dialog.Close>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto px-[18px] pt-4 pb-6">{children}</div>
          {footer && (
            <footer className="flex shrink-0 flex-wrap gap-1.5 border-t border-border px-4 py-2.5">
              {footer}
            </footer>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
