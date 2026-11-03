/**
 * The cockpit's one toast: a single polite live region, newest message wins.
 * Reversible acts (answer, vote, pause, apply) confirm here with an Undo
 * button instead of asking first; `u` presses Undo while one is offered.
 * It stays up while the pointer or focus is on it, and gives focus back to
 * where it came from when it closes.
 */

import { CheckIcon, XIcon } from "lucide-react";
import {
  type FocusEvent,
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
  /**
   * Told when the pointer or focus starts (true) or stops (false) holding
   * this toast open, and false when it goes while held: an act that runs
   * on its own after a delay pauses meanwhile, so Undo never comes too late.
   */
  onHoldChange?: (held: boolean) => void;
}

export const TOAST_MS = 5_000;
export const TOAST_UNDO_MS = 12_000;

/**
 * Marks the toast region. Layers that close on an outside click (Peek) leave
 * clicks here alone, so Undo works from inside an open panel.
 */
export const TOAST_REGION_SELECTOR = "[data-factory-toast]";

/** True when `target` sits inside the toast region. */
export function inToastRegion(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest(TOAST_REGION_SELECTOR) !== null;
}

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

/** Widgets that read printable keys themselves (typeahead, grids, sliders). */
const KEY_OWNERS = [
  "menu",
  "menubar",
  "menuitem",
  "menuitemcheckbox",
  "menuitemradio",
  "listbox",
  "option",
  "combobox",
  "tree",
  "treeitem",
  "grid",
  "slider",
  "spinbutton",
  "textbox",
  "searchbox",
]
  .map((role) => `[role=${role}]`)
  .join(",");

/** True when a `u` keypress belongs to whatever has focus, not to Undo. */
function keyBelongsToTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return true;
  return target.closest(KEY_OWNERS) !== null;
}

/** The toast's face, without timers (stories and tests render it directly). */
export function ToastView({
  toast,
  onUndo,
  onDismiss,
  onHoldChange,
  className,
}: {
  toast: ToastMessage;
  onUndo?: () => void;
  onDismiss?: () => void;
  /** Called with true while the pointer is over it or focus is inside it. */
  onHoldChange?: (held: boolean) => void;
  className?: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const hovered = useRef(false);
  const focused = useRef(false);
  const report = useCallback(
    () => onHoldChange?.(hovered.current || focused.current),
    [onHoldChange],
  );
  // A newer toast can drop the button that had focus without a blur event.
  useEffect(() => {
    if (focused.current && !root.current?.contains(document.activeElement)) {
      focused.current = false;
      report();
    }
  }, [toast, report]);
  return (
    <div
      ref={root}
      data-testid="factory-toast"
      onPointerEnter={() => {
        hovered.current = true;
        report();
      }}
      onPointerLeave={() => {
        hovered.current = false;
        report();
      }}
      onFocus={() => {
        focused.current = true;
        report();
      }}
      onBlur={(event: FocusEvent<HTMLDivElement>) => {
        if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
        focused.current = false;
        report();
      }}
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
  const current = useRef<ToastMessage | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const held = useRef(false);
  const region = useRef<HTMLDivElement>(null);
  /** What had focus before it moved into the toast. */
  const returnFocus = useRef<HTMLElement | null>(null);

  const clearTimer = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const dismiss = useCallback(() => {
    clearTimer();
    const hadFocus = region.current?.contains(document.activeElement) ?? false;
    const back = returnFocus.current;
    returnFocus.current = null;
    if (held.current) current.current?.onHoldChange?.(false);
    held.current = false;
    current.current = null;
    setToast(null);
    if (hadFocus && back?.isConnected) back.focus();
  }, [clearTimer]);

  /** (Re)starts the close timer, unless the pointer or focus is holding it open. */
  const arm = useCallback(() => {
    clearTimer();
    const next = current.current;
    if (!next || held.current) return;
    timer.current = setTimeout(dismiss, next.durationMs ?? (next.undo ? TOAST_UNDO_MS : TOAST_MS));
  }, [clearTimer, dismiss]);

  const show = useCallback(
    (next: ToastMessage) => {
      const prev = current.current;
      if (held.current && prev !== next) {
        // The pointer or focus now holds the newer one.
        prev?.onHoldChange?.(false);
        next.onHoldChange?.(true);
      }
      current.current = next;
      setToast(next);
      arm();
    },
    [arm],
  );

  const onHoldChange = useCallback(
    (next: boolean) => {
      if (held.current === next) return;
      held.current = next;
      current.current?.onHoldChange?.(next);
      if (next) clearTimer();
      else arm();
    },
    [arm, clearTimer],
  );

  const undo = useCallback(() => {
    const fn = current.current?.undo;
    dismiss();
    fn?.();
  }, [dismiss]);

  useEffect(() => {
    if (!toast?.undo) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "u") return;
      if (event.defaultPrevented || event.repeat) return;
      if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      if (keyBelongsToTarget(event.target)) return;
      event.preventDefault();
      undo();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toast, undo]);

  useEffect(() => clearTimer, [clearTimer]);

  const api = useMemo(() => ({ show, dismiss }), [show, dismiss]);
  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        ref={region}
        role="status"
        aria-live="polite"
        data-testid="factory-toast-region"
        data-factory-toast=""
        onFocusCapture={(event) => {
          const from = event.relatedTarget;
          if (from instanceof HTMLElement && !event.currentTarget.contains(from)) {
            returnFocus.current = from;
          }
        }}
        className={cn(
          "pointer-events-none fixed inset-x-0 z-[80] flex justify-center px-3",
          // Above the OS inset; on a phone also above the 56 px tab bar (shown below `md`).
          "bottom-[calc(24px+var(--omnigent-inset-bottom,0px))]",
          "max-md:bottom-[calc(76px+var(--omnigent-inset-bottom,0px))]",
        )}
      >
        {toast && (
          <ToastView toast={toast} onUndo={undo} onDismiss={dismiss} onHoldChange={onHoldChange} />
        )}
      </div>
    </ToastContext.Provider>
  );
}
