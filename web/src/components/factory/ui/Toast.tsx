/**
 * The cockpit's one toast: a single polite live region, newest message wins.
 * Reversible acts (answer, vote, pause, apply) confirm here with an Undo
 * button instead of asking first; `u` presses Undo while one is offered.
 */

import { CheckIcon, XIcon } from "lucide-react";
import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { cn } from "@/lib/utils";
import { FOCUS_RING } from "./statusTokens";

export interface ToastMessage {
  message: string;
  /** Offers Undo; called once, then the toast closes. */
  undo?: () => void;
  /** Override how long it stays (ms); 12 s with Undo, 5 s without. */
  durationMs?: number;
}

export const TOAST_MS = 5_000;
export const TOAST_UNDO_MS = 12_000;

interface ToastApi {
  show: (toast: ToastMessage) => void;
  dismiss: () => void;
}

const ToastContext = createContext<ToastApi | null>(null);

/** Shows and clears the cockpit toast; a no-op outside `FactoryToastProvider`. */
export function useFactoryToast(): ToastApi {
  return useContext(ToastContext) ?? NOOP;
}

const NOOP: ToastApi = { show: () => undefined, dismiss: () => undefined };

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
}

/** The toast's face, without timers (stories and tests render it directly). */
export function ToastView({
  toast,
  onUndo,
  onDismiss,
  className,
}: {
  toast: ToastMessage;
  onUndo?: () => void;
  onDismiss?: () => void;
  className?: string;
}) {
  return (
    <div
      data-testid="factory-toast"
      className={cn(
        "pointer-events-auto flex max-w-[calc(100vw-24px)] items-center gap-3 rounded-[10px] bg-primary py-2 pr-2 pl-3.5 text-ui text-primary-foreground shadow-xl motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-2",
        className,
      )}
    >
      <CheckIcon aria-hidden className="size-3.5 shrink-0" />
      <span className="min-w-0">{toast.message}</span>
      {toast.undo && (
        <button
          type="button"
          onClick={onUndo}
          className={cn(
            "h-[26px] shrink-0 cursor-pointer rounded-md border border-primary-foreground/35 px-2.5 font-medium hover:bg-primary-foreground/10",
            FOCUS_RING,
          )}
        >
          Undo
        </button>
      )}
      {onDismiss && (
        <button
          type="button"
          aria-label="Dismiss"
          onClick={onDismiss}
          className={cn(
            "grid size-6 shrink-0 cursor-pointer place-items-center rounded-md hover:bg-primary-foreground/10",
            FOCUS_RING,
          )}
        >
          <XIcon aria-hidden className="size-3.5" />
        </button>
      )}
    </div>
  );
}

export function FactoryToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<ToastMessage | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const dismiss = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setToast(null);
  }, []);

  const show = useCallback(
    (next: ToastMessage) => {
      if (timer.current) clearTimeout(timer.current);
      setToast(next);
      timer.current = setTimeout(
        dismiss,
        next.durationMs ?? (next.undo ? TOAST_UNDO_MS : TOAST_MS),
      );
    },
    [dismiss],
  );

  const undo = useCallback(() => {
    const fn = toast?.undo;
    dismiss();
    fn?.();
  }, [toast, dismiss]);

  useEffect(() => {
    if (!toast?.undo) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "u" || event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTyping(event.target)) return;
      event.preventDefault();
      undo();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toast, undo]);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const api = useMemo(() => ({ show, dismiss }), [show, dismiss]);
  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        role="status"
        aria-live="polite"
        data-testid="factory-toast-region"
        className="pointer-events-none fixed inset-x-0 bottom-6 z-[80] flex justify-center px-3 max-[760px]:bottom-[76px]"
      >
        {toast && <ToastView toast={toast} onUndo={undo} onDismiss={dismiss} />}
      </div>
    </ToastContext.Provider>
  );
}
