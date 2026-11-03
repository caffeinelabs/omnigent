// Answering in place with Undo. An answer reaches an agent at once and can
// never be recalled, so the card holds it for a few seconds first: the row
// leaves the list, one toast says what goes out soon and offers Undo (also
// `u`), and only then is it sent. Several held answers share that one toast;
// Undo takes back the newest, then the next. The hold pauses while the
// pointer or focus is on the toast, so Undo is never too late; if it is
// anyway, the toast says so. Leaving the page, hiding the tab (a phone
// switching apps) or unmounting sends what is held at once, so no answer is
// lost. A failed send brings the row back and says why.

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import { type Ask, type AskOption, sendAskAnswer } from "@/lib/factoryAsksApi";
import { type DecisionConflictBody, isDecisionConflict } from "@/lib/factoryApi";
import { type ToastMessage, useFactoryToast } from "../ui/Toast";

/** How long an answer waits for Undo before it goes out. */
export const ANSWER_UNDO_MS = 8_000;

interface Held {
  ask: Ask;
  option: AskOption;
  /** When it goes out (`Date.now()` ms), moved later while the toast is held. */
  dueAt: number;
}

export interface DeferredAnswers {
  /** Rows held or sent and not yet gone from the server's list. */
  hidden: ReadonlySet<string>;
  /** Hold *option* as the answer to *ask*, with Undo. */
  answer: (ask: Ask, option: AskOption) => void;
  /** Forget hidden rows the server no longer lists. */
  prune: (listed: ReadonlySet<string>) => void;
}

function who(error: unknown): string | null {
  if (!isDecisionConflict(error)) return null;
  const body = (error.body ?? {}) as DecisionConflictBody;
  const by = body.answered_by ?? body.decision?.answered_by ?? null;
  return by ? by.split("@")[0] || by : "someone";
}

const refOf = (ask: Ask) => ask.ref ?? ask.id;

/** What the one toast says about the answers still held (newest last). */
export function heldMessage(held: readonly { ask: Ask; option: AskOption }[]): string {
  const newest = held.at(-1);
  if (!newest) return "";
  if (held.length === 1) {
    return `${newest.option.label}: your answer to ${refOf(newest.ask)} goes out in a few seconds.`;
  }
  return `${held.length} answers go out in a few seconds. Undo takes back ${refOf(newest.ask)}.`;
}

interface Env {
  toast: ReturnType<typeof useFactoryToast>;
  send: (entry: Held, keepalive: boolean) => void;
  setShown: (id: string, shown: boolean) => void;
}

/**
 * The held answers and their one timer (a plain object, so the toast's Undo,
 * the timer and the page-leave flush all see the same state).
 */
class AnswerHold {
  readonly held = new Map<string, Held>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private pausedAt: number | null = null;
  private readonly env: { current: Env };

  constructor(env: { current: Env }) {
    this.env = env;
  }

  answer(ask: Ask, option: AskOption): void {
    if (!ask.decision_id || this.held.has(ask.id)) return;
    this.held.set(ask.id, { ask, option, dueAt: Date.now() + ANSWER_UNDO_MS });
    this.env.current.setShown(ask.id, false);
    this.showHeld();
    this.arm();
  }

  /** Shows (or refreshes) the one toast for what is held, after *prefix* if given. */
  showHeld(prefix?: string): void {
    const entries = [...this.held.values()];
    if (!entries.length) return;
    const last = Math.max(...entries.map((e) => e.dueAt));
    const message = heldMessage(entries);
    const toast: ToastMessage = {
      message: prefix ? `${prefix} ${message}` : message,
      undo: () => this.undoNewest(),
      durationMs: Math.max(1_000, last - Date.now() + 250),
      onHoldChange: (held) => (held ? this.pause() : this.resume()),
    };
    this.env.current.toast.show(toast);
  }

  undoNewest(): void {
    const newest = [...this.held.values()].at(-1);
    if (!newest) {
      this.env.current.toast.show({ message: "Already sent: it went out before Undo." });
      return;
    }
    this.held.delete(newest.ask.id);
    this.env.current.setShown(newest.ask.id, true);
    this.showHeld();
    this.arm();
  }

  pause(): void {
    if (this.pausedAt != null) return;
    this.pausedAt = Date.now();
    this.clear();
  }

  resume(): void {
    if (this.pausedAt == null) return;
    const waited = Date.now() - this.pausedAt;
    this.pausedAt = null;
    for (const entry of this.held.values()) entry.dueAt += waited;
    this.arm();
  }

  /** Sends every held answer now (leaving the page, hiding the tab, unmounting). */
  flush(): void {
    this.clear();
    this.pausedAt = null;
    const entries = [...this.held.values()];
    this.held.clear();
    for (const entry of entries) this.env.current.send(entry, true);
  }

  private clear(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  private arm(): void {
    this.clear();
    if (this.pausedAt != null || this.held.size === 0) return;
    const next = Math.min(...[...this.held.values()].map((e) => e.dueAt));
    this.timer = setTimeout(() => this.sendDue(), Math.max(0, next - Date.now()));
  }

  private sendDue(): void {
    this.timer = null;
    const now = Date.now();
    const due = [...this.held.values()].filter((e) => e.dueAt <= now);
    for (const entry of due) this.held.delete(entry.ask.id);
    for (const entry of due) this.env.current.send(entry, false);
    // The rest keep the toast (and its Undo), now saying what is left.
    if (due.length && this.held.size) this.showHeld();
    this.arm();
  }
}

export function useDeferredAnswers(factoryId: string): DeferredAnswers {
  const toast = useFactoryToast();
  const queryClient = useQueryClient();
  const mounted = useRef(true);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set());

  const setShown = useCallback((id: string, shown: boolean) => {
    setHidden((prev) => {
      if (prev.has(id) === !shown) return prev;
      const next = new Set(prev);
      if (shown) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const env = useRef<Env>(null as unknown as Env);
  const [hold] = useState(() => new AnswerHold(env));

  const send = useCallback(
    (entry: Held, keepalive: boolean) => {
      const ref = refOf(entry.ask);
      sendAskAnswer(entry.ask.decision_id ?? "", { option_id: entry.option.id }, { keepalive })
        .then(
          () => {
            // Said only once nothing else is held: it must not take another's Undo away.
            if (mounted.current && hold.held.size === 0) {
              toast.show({ message: `Answered ${ref}: ${entry.option.label}.` });
            }
          },
          (error: unknown) => {
            if (!mounted.current) return;
            const by = who(error);
            if (by == null) setShown(entry.ask.id, true);
            const why = error instanceof Error ? error.message : String(error);
            const said = by
              ? `${ref} was already answered by ${by}.`
              : `Could not answer ${ref}: ${why}`;
            if (hold.held.size) hold.showHeld(said);
            else toast.show({ message: said });
          },
        )
        .finally(() => {
          for (const key of factoryInvalidationKeys(factoryId)) {
            void queryClient.invalidateQueries({ queryKey: key });
          }
        });
    },
    [factoryId, hold, queryClient, setShown, toast],
  );
  env.current = { toast, send, setShown };

  // Leaving the page, hiding the tab (a phone switching apps, which may then
  // discard it) or unmounting sends what is held right away.
  useEffect(() => {
    mounted.current = true;
    const flush = () => hold.flush();
    const onVisibility = () => {
      if (document.visibilityState === "hidden") hold.flush();
    };
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", onVisibility);
      mounted.current = false;
      hold.flush();
    };
  }, [hold]);

  const answer = useCallback((ask: Ask, option: AskOption) => hold.answer(ask, option), [hold]);

  const prune = useCallback(
    (listed: ReadonlySet<string>) => {
      setHidden((prev) => {
        const keep = [...prev].filter((id) => listed.has(id) || hold.held.has(id));
        return keep.length === prev.size ? prev : new Set(keep);
      });
    },
    [hold],
  );

  return { hidden, answer, prune };
}
